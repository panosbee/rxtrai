"""
RxTrail — Queue router: GET /queue/today — scored daily queue items.
REQ-03: Value-weighted scoring. REQ-04: Geography grouping. REQ-06: Contact due.
"""
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Request

from backend.auth.deps import get_current_user, get_org
from backend.config import settings
from backend.core.clocks import days_overdue, ensure_utc
from backend.core.scoring import score_queue_item
from backend.db import get_db

router = APIRouter(prefix="/queue", tags=["queue"])


def _json_safe(obj):
    """Μετατρέπει ObjectId → str παντού (το FastAPI δεν τα σειριοποιεί)."""
    from bson import ObjectId as _OID
    if isinstance(obj, _OID):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


@router.get("/today")
async def get_today_queue(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
    group_by: str = Query("none", description="Group by: 'area' or 'none'"),
):
    """Return scored daily queue for the authenticated org."""
    db = await get_db()
    now = datetime.now(timezone.utc)
    items = []

    # ── Helper: build a queue item with REQ-03 value scoring ─────────────
    def make_item(kind: str, case: dict, why: str, due_at=None, days_over=0, contact_due=False):
        est = case.get("est_monthly_value") if case else None
        base = _base_score(kind, days_over)
        scored = score_queue_item(kind, days_over, est_monthly_value=est, contact_due=contact_due)
        return {
            "_id": f"{kind.lower()}_{case['_id']}" if case else kind.lower(),
            "kind": kind,
            "base_score": base,
            "value_factor": round(scored / base, 2) if base > 0 else 1.0,
            "final_score": scored,
            "est_monthly_value": est,
            "why": why,
            "case_id": case["_id"] if case else None,
            "case": case,
            "due_at": str(due_at) if due_at else None,
        }

    # ── AT_RISK cases ────────────────────────────────────────────────────
    async for case in db.cases.find({"org_id": org_id, "status": "AT_RISK"}).sort("updated_at", -1).limit(20):
        case["_id"] = str(case["_id"])
        items.append(make_item("AT_RISK", case, f"🚨 {case.get('alias', 'Περιστατικό')} — η επανάληψη άργησε"))

    # ── REPEAT_DUE notifications (skip no_repeat; only when due) ─────────
    async for notif in db.notifications.find({
        "org_id": org_id, "kind": "REPEAT_DUE", "seen": False, "dismissed": False,
        "due_at": {"$lte": now},
    }).sort("due_at", 1).limit(20):
        notif["_id"] = str(notif["_id"])
        case = await db.cases.find_one({"_id": ObjectId(notif["ref_id"])}) if notif.get("ref_id") else None
        if case and case.get("no_repeat"):
            continue  # REQ-02: skip one-shot products
        if case:
            case["_id"] = str(case["_id"])  # BUG FIX: χωρίς αυτό → 500 (ObjectId not serializable)
        overdue = days_overdue(notif.get("due_at", now), now)
        alias = case.get("alias", "Περιστατικό") if case else "Άγνωστο"
        item = make_item("REPEAT_DUE", case, f"🔁 {alias} — επανάληψη", notif.get("due_at"), overdue)
        item["_id"] = f"repeat_{notif['_id']}"
        items.append(item)

    # ── SLA_NEW cases (>=5 days) ──────────────────────────────────────────
    async for case in db.cases.find({"org_id": org_id, "status": "NEW"}).limit(50):
        created = case.get("created_at")
        if created and days_overdue(created, now) >= 5:
            case["_id"] = str(case["_id"])
            overdue = days_overdue(created, now) - 5
            items.append(make_item("SLA_NEW", case,
                f"🆕 {case.get('alias', 'Νέο')} — αξιολόγηση {overdue + 5} μέρες", days_over=max(0, overdue)))

    # ── SLA_ASSESSED (>=7 days) ───────────────────────────────────────────
    async for case in db.cases.find({"org_id": org_id, "status": "ASSESSED"}).limit(50):
        for entry in case.get("status_history", []):
            if entry.get("to") == "ASSESSED":
                entered = entry.get("at")
                if entered and days_overdue(entered, now) >= 7:
                    case["_id"] = str(case["_id"])
                    overdue = days_overdue(entered, now) - 7
                    items.append(make_item("SLA_ASSESSED", case,
                        f"📋 {case.get('alias', 'Περιστατικό')} — γιατρός {overdue + 7} μέρες", days_over=max(0, overdue)))
                break

    # ── SLA_PRESCRIBED (>=7 days without fulfil) ──────────────────────────
    async for case in db.cases.find({"org_id": org_id, "status": "PRESCRIBED"}).limit(50):
        for entry in case.get("status_history", []):
            if entry.get("to") == "PRESCRIBED":
                entered = entry.get("at")
                if entered and days_overdue(entered, now) >= 7:
                    case["_id"] = str(case["_id"])
                    overdue = days_overdue(entered, now) - 7
                    items.append(make_item("SLA_PRESCRIBED", case,
                        f"📝 {case.get('alias', 'Περιστατικό')} — ανεκτέλεστη {overdue + 7} μέρες", days_over=max(0, overdue)))
                break

    # ── REQ-06: Contact due today ────────────────────────────────────────
    async for case in db.cases.find({"org_id": org_id, "status": {"$nin": ["CLOSED", "LOST"]}}).limit(100):
        attempts = case.get("contact_attempts", [])
        if not attempts:
            continue
        last = attempts[-1]
        next_try = ensure_utc(last.get("next_try_at"))
        if next_try and next_try <= now:
            case["_id"] = str(case["_id"])
            outcome_label = {"NO_ANSWER": "δεν απάντησε", "REACHED": "μιλήσατε", "CALLBACK_REQUESTED": "ζήτησε κλήση", "WRONG_NUMBER": "λάθος νούμερο"}.get(last.get("outcome", ""), "")
            items.append(make_item("CONTACT_DUE", case,
                f"📞 {case.get('alias', 'Επαφή')} — ξαναπάρε ({outcome_label})", contact_due=True))

    # ── Sort by final_score descending, tie-break next_repeat_due ────────
    items.sort(key=lambda x: (
        -x["final_score"],
        x["case"].get("next_repeat_due") or "9999-12-31" if x.get("case") else "9999-12-31"
    ))

    # ── REQ-04: Geo grouping ─────────────────────────────────────────────
    if group_by == "area":
        groups = _group_by_area(items)
        return _json_safe({
            "groups": groups,
            "total_items": len(items),
            "generated_at": now.isoformat(),
        })

    return _json_safe({
        "items": items[:40],
        "generated_at": now.isoformat(),
    })


# ── Helpers ──────────────────────────────────────────────────────────────────

def _base_score(kind: str, days_overdue: int = 0) -> float:
    """Return the base score (before value factor) for a queue item kind."""
    from backend.core.clocks import urgency_decay
    bases = {
        "AT_RISK": 50,
        "REPEAT_DUE": 38 * urgency_decay(days_overdue),
        "SLA_NEW": 25 * urgency_decay(days_overdue),
        "SLA_ASSESSED": 15,
        "SLA_PRESCRIBED": 20,
        "ACTION_DUE": 20,
        "VISIT_SUGGEST": 10,
        "CONTACT_DUE": 12,
    }
    return round(bases.get(kind, 10), 1)


def _group_by_area(items: list[dict]) -> list[dict]:
    """REQ-04: Group queue items by case.area, ordered by max(final_score) per group."""
    groups: dict[str, dict] = {}

    for item in items:
        case = item.get("case") or {}
        area = (case.get("area") or "").strip()
        if not area:
            area = "Χωρίς περιοχή"

        if area not in groups:
            groups[area] = {"area": area, "group_score": 0.0, "total_value": 0.0, "items": []}

        groups[area]["items"].append(item)
        groups[area]["group_score"] = max(groups[area]["group_score"], item["final_score"])
        groups[area]["total_value"] += item.get("est_monthly_value") or 0

    # Sort items within each group by final_score desc
    for g in groups.values():
        g["items"].sort(key=lambda x: -x["final_score"])
        g["total_value"] = round(g["total_value"], 2)

    # Sort groups: by max(final_score) desc, "Χωρίς περιοχή" last
    result = sorted(groups.values(), key=lambda g: (
        1 if g["area"] == "Χωρίς περιοχή" else 0,
        -g["group_score"]
    ))

    return result
