"""
RxTrail — Week planner: deterministic geography-based week proposal.

V1 logic (simple, deterministic — NO LLM, NO TSP):
  1. Collect all pending queue items + fixed appointments
  2. Group items by prefecture/zone
  3. Zones with ≥3 mature items → suggest a day-zone
  4. Fixed appointments pinned first
  5. Lock/unlock for drag & drop
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.auth.deps import get_current_user, get_org
from backend.core.clocks import ensure_utc
from backend.db import get_db

router = APIRouter(prefix="/week", tags=["week"])

# Day indices: 0=Monday, 4=Friday
DAYS = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή"]
DAY_KEYS = ["mon", "tue", "wed", "thu", "fri"]

# Prefecture → zone mapping for Crete
PREFECTURE_ZONES = {
    "Ηράκλειο": "Ηράκλειο-πόλη",
    "Ηρακλείου": "Ηράκλειο-πόλη",
    "Ηράκλειο-περιφέρεια": "Ηράκλειο-περιφέρεια",
    "Ρέθυμνο": "Ρέθυμνο",
    "Ρεθύμνου": "Ρέθυμνο",
    "Χανιά": "Χανιά",
    "Χανίων": "Χανιά",
    "Λασίθι": "Λασίθι",
    "Λασιθίου": "Λασίθι",
    "Άγιος Νικόλαος": "Λασίθι",
    "Ιεράπετρα": "Λασίθι",
    "Σητεία": "Λασίθι",
}


def _zone_for(prefecture: str, area: str) -> str:
    """Map a prefecture or area string to a geographic zone."""
    p = (prefecture or "").strip()
    if p in PREFECTURE_ZONES:
        return PREFECTURE_ZONES[p]
    # Try area-based matching
    a = (area or "").strip().lower()
    for keyword, zone in [
        ("γάζι", "Ηράκλειο-περιφέρεια"),
        ("μαλάδες", "Ηράκλειο-πόλη"),
        ("αλικαρνασσός", "Ηράκλειο-πόλη"),
        ("χερσόνησος", "Ηράκλειο-περιφέρεια"),
        ("μοίρες", "Ηράκλειο-περιφέρεια"),
        ("ρεθυμνο", "Ρέθυμνο"),
        ("χανιά", "Χανιά"),
        ("αγιος νικολαος", "Λασίθι"),
        ("ιεράπετρα", "Λασίθι"),
    ]:
        if keyword in a:
            return zone
    return "Άλλο"


@router.get("/proposal")
async def week_proposal(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Generate a deterministic week plan based on queue items + geography."""
    db = await get_db()
    now = datetime.now(timezone.utc)

    # ── 1. Collect queue items ───────────────────────────────────────────
    # ΑΠΟΦΥΓΗ ΔΙΠΛΟΕΜΦΑΝΙΣΗΣ: περιστατικά που έχουν ήδη ραντεβού αυτή την
    # εβδομάδα (π.χ. από το πλάνο του Ασκληπιού) ΔΕΝ ξαναμπαίνουν ως προτάσεις.
    week_start = _monday_of_week(now)
    week_end = week_start + timedelta(days=7)
    scheduled_case_ids = set()
    async for a in db.appointments.find({
        "org_id": org_id,
        "case_id": {"$exists": True, "$ne": None},
        "at": {"$gte": week_start, "$lt": week_end},
    }, {"case_id": 1}):
        scheduled_case_ids.add(a.get("case_id"))

    def _skip_scheduled(case) -> bool:
        return str(case["_id"]) in scheduled_case_ids

    # AT_RISK cases
    risk_cases = []
    async for case in db.cases.find({"org_id": org_id, "status": "AT_RISK"}).limit(20):
        if _skip_scheduled(case):
            continue
        risk_cases.append(_case_item(case, "AT_RISK"))

    # NEW cases (pending assessment)
    new_cases = []
    async for case in db.cases.find({"org_id": org_id, "status": "NEW"}).limit(20):
        if _skip_scheduled(case):
            continue
        created = ensure_utc(case.get("created_at"))
        if created and (now - created).days >= 2:  # Only suggest if ≥2 days old
            new_cases.append(_case_item(case, "NEW"))

    # ASSESSED cases
    assessed_cases = []
    async for case in db.cases.find({"org_id": org_id, "status": "ASSESSED"}).limit(20):
        if _skip_scheduled(case):
            continue
        assessed_cases.append(_case_item(case, "ASSESSED"))

    # Fixed appointments (doctor_appointment_at is set)
    fixed = []
    async for case in db.cases.find({
        "org_id": org_id,
        "doctor_appointment_at": {"$exists": True, "$ne": None},
        "status": "AT_DOCTOR",
    }).limit(20):
        appt = case.get("doctor_appointment_at")
        if appt:
            fixed.append({
                "_id": str(case["_id"]),
                "alias": case.get("alias", ""),
                "area": case.get("area", ""),
                "kind": "APPOINTMENT",
                "zone": _zone_for("", case.get("area", "")),
                "appointment_at": appt.isoformat() if isinstance(appt, datetime) else str(appt),
                "case_id": str(case["_id"]),
            })

    # ── 2. Combine & group by zone ───────────────────────────────────────
    all_items = risk_cases + new_cases + assessed_cases
    zones = defaultdict(list)
    for item in all_items:
        item["zone"] = _zone_for("", item.get("area", ""))
        zones[item["zone"]].append(item)

    # ── 3. Assign days to zones with ≥3 items ────────────────────────────
    day_assignments = {}
    sorted_zones = sorted(zones.items(), key=lambda x: -len(x[1]))
    day_idx = 0
    for zone_name, items in sorted_zones:
        if len(items) >= 3 and day_idx < 5:
            day_assignments[zone_name] = DAY_KEYS[day_idx]
            day_idx += 1

    # ── 4. Build proposal structure ──────────────────────────────────────
    proposal = {}
    for dk in DAY_KEYS:
        proposal[dk] = {"label": DAYS[DAY_KEYS.index(dk)], "items": [], "locked": False}

    # Place fixed appointments first
    for item in fixed:
        if item.get("appointment_at"):
            try:
                appt_date = datetime.fromisoformat(item["appointment_at"])
                wday = appt_date.weekday()  # 0=Mon
                if 0 <= wday < 5:
                    proposal[DAY_KEYS[wday]]["items"].append(item)
            except (ValueError, TypeError):
                pass

    # Place zone-grouped items
    for zone_name, items in sorted_zones:
        dk = day_assignments.get(zone_name)
        if dk:
            proposal[dk]["items"].extend(items)
        else:
            # Distribute remaining items evenly across days
            for i, item in enumerate(items):
                proposal[DAY_KEYS[i % 5]]["items"].append(item)

    # ── 5. Check if there's a locked plan from a previous session ────────
    locked_plan = await db.week_plans.find_one({
        "org_id": org_id,
        "week_start": _monday_of_week(now).isoformat(),
    })
    if locked_plan:
        for dk in DAY_KEYS:
            if dk in locked_plan.get("days", {}):
                proposal[dk] = locked_plan["days"][dk]
                proposal[dk]["locked"] = True

    return {
        "proposal": proposal,
        "zone_summary": {z: len(items) for z, items in zones.items()},
        "zone_assignments": day_assignments,
        "total_items": len(all_items) + len(fixed),
        "week_start": _monday_of_week(now).isoformat(),
    }


@router.post("/lock")
async def lock_week(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Lock the current week plan. Saves the current proposal state."""
    db = await get_db()
    now = datetime.now(timezone.utc)

    # Read the proposal again (same logic)
    proposal_data = await week_proposal(request, current_user, org_id)
    days = proposal_data["proposal"]
    week_start = proposal_data["week_start"]

    # Mark all as locked
    for dk in DAY_KEYS:
        days[dk]["locked"] = True

    await db.week_plans.update_one(
        {"org_id": org_id, "week_start": week_start},
        {"$set": {
            "org_id": org_id,
            "week_start": week_start,
            "days": days,
            "locked_at": now,
            "locked_by": str(current_user["_id"]),
            "updated_at": now,
        }},
        upsert=True,
    )

    return {"ok": True, "week_start": week_start, "days": days}


@router.post("/unlock")
async def unlock_week(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Unlock the current week plan. Removes the lock."""
    db = await get_db()
    now = datetime.now(timezone.utc)
    week_start = _monday_of_week(now).isoformat()

    result = await db.week_plans.delete_one({
        "org_id": org_id,
        "week_start": week_start,
    })

    return {"ok": True, "week_start": week_start, "deleted": result.deleted_count > 0}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _case_item(case: dict, kind: str) -> dict:
    return {
        "_id": str(case["_id"]),
        "alias": case.get("alias", ""),
        "area": case.get("area", ""),
        "kind": kind,
        "case_id": str(case["_id"]),
        "status": case.get("status", ""),
        "need_categories": case.get("need_categories", []),
        "est_monthly_value": case.get("est_monthly_value"),
    }


def _monday_of_week(dt: datetime) -> datetime:
    """Return Monday 00:00 of the week containing dt."""
    monday = dt - timedelta(days=dt.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)
