"""
RxTrail — State Machine: THE single source of truth for case status transitions.

NO other code changes case.status directly. Every transition flows through
`transition(case_id, action, payload, user_id)`.

Side-effects are executed atomically within each transition:
  - status_history append
  - audit log write
  - source/doctor metrics updates
  - patient creation (→ACTIVE)
  - notification lifecycle (→LOST/CLOSED purge ALL)
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.config import settings
from backend.core.clocks import ensure_utc

import logging
logger = logging.getLogger(__name__)

# ── Transition table ─────────────────────────────────────────────────────────
# (from_status, action) → to_status
TRANSITIONS: dict[tuple[str, str], str] = {
    ("NEW",        "assess"):     "ASSESSED",
    ("NEW",        "lose"):       "LOST",
    ("NEW",        "close"):      "CLOSED",
    ("ASSESSED",   "to_doctor"):  "AT_DOCTOR",
    ("ASSESSED",   "lose"):       "LOST",
    ("ASSESSED",   "close"):      "CLOSED",
    ("AT_DOCTOR",  "prescribe"):  "PRESCRIBED",
    ("AT_DOCTOR",  "reassign"):   "ASSESSED",   # REQ-01: send back for re-evaluation
    ("AT_DOCTOR",  "lose"):       "LOST",
    ("AT_DOCTOR",  "close"):      "CLOSED",
    ("PRESCRIBED", "fulfil"):     "ACTIVE",
    ("PRESCRIBED", "lose"):       "LOST",        # REQ-01: lost after prescription
    ("PRESCRIBED", "at_risk"):    "AT_RISK",     # REQ-01: scheduler-only, fulfil SLA expired
    ("ACTIVE",     "fulfil"):     "ACTIVE",       # repeat fulfilment
    ("ACTIVE",     "at_risk"):    "AT_RISK",      # scheduler-only
    ("ACTIVE",     "close"):      "CLOSED",
    ("AT_RISK",    "reactivate"): "ACTIVE",
    ("AT_RISK",    "fulfil"):     "ACTIVE",       # repeat from AT_RISK
    ("AT_RISK",    "lose"):       "LOST",
    ("AT_RISK",    "close"):      "CLOSED",
}

# Actions that REQUIRE a payload field (for initial transition validation)
REQUIRED_PAYLOAD: dict[str, list[str]] = {
    "assess":     ["need_categories"],
    "lose":       ["lost_reason"],
    "close":      ["closed_reason"],
    "to_doctor":  ["doctor_id"],
    "prescribe":  ["prescription_id"],
    "reassign":   [],   # REQ-01: no required fields, optional new_doctor_id
}


class TransitionError(Exception):
    """Raised when a transition is invalid (bad action, missing payload, etc.)."""
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _as_dt(v):
    """Parse a payload datetime (ISO string or datetime) into tz-aware UTC."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return ensure_utc(v)
    if isinstance(v, str):
        try:
            return ensure_utc(datetime.fromisoformat(v.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


# ── Public API ───────────────────────────────────────────────────────────────

async def transition(
    db: AsyncIOMotorDatabase,
    case_id: str,
    action: str,
    payload: dict,
    user_id: str,
) -> dict:
    """
    Execute a status transition on a case. This is the ONLY way to change case.status.

    Args:
        db: Motor database handle
        case_id: The case's _id (string or ObjectId)
        action: Transition action name (e.g. 'assess', 'to_doctor', 'fulfil')
        payload: Additional data required by the transition (doctor_id, lost_reason, etc.)
        user_id: The user performing the transition (for audit)

    Returns:
        The updated case document (with _id as string)

    Raises:
        TransitionError: If the transition is invalid or missing required fields
    """
    now = datetime.now(timezone.utc)

    # Load case
    case = await db.cases.find_one({"_id": ObjectId(case_id)})
    if not case:
        raise TransitionError("Case not found", 404)

    org_id = case["org_id"]
    from_status = case["status"]

    # Validate transition
    key = (from_status, action)
    to_status = TRANSITIONS.get(key)
    if to_status is None:
        allowed = [a for (s, a), t in TRANSITIONS.items() if s == from_status]
        raise TransitionError(
            f"Invalid transition '{action}' from '{from_status}'. Allowed: {allowed}"
        )

    # Validate required payload fields
    missing = []
    for field in REQUIRED_PAYLOAD.get(action, []):
        if field not in payload or payload[field] is None:
            missing.append(field)
    if missing:
        raise TransitionError(f"Missing required fields for '{action}': {missing}")

    # REQ-01: Custom validation — WENT_TO_COMPETITOR requires lost_competitor
    if action == "lose" and payload.get("lost_reason") == "WENT_TO_COMPETITOR":
        if not payload.get("lost_competitor"):
            raise TransitionError(
                "Το πεδίο 'lost_competitor' είναι υποχρεωτικό όταν ο λόγος είναι WENT_TO_COMPETITOR"
            )

    # REQ-02: Custom validation — fulfil on no_repeat case only via initial
    if action == "fulfil" and case.get("no_repeat") and from_status == "ACTIVE":
        raise TransitionError(
            "Δεν επιτρέπεται repeat fulfilment σε εφάπαξ προϊόν (no_repeat). "
            "Το περιστατικό είναι ενεργό χωρίς κύκλο επανάληψης."
        )

    # REQ-05: Idempotency for repeat — check last fulfil was < REPEAT_MIN_GAP_HOURS ago
    if action == "fulfil" and from_status in ("ACTIVE", "AT_RISK"):
        last_fulfil = case.get("last_fulfilment_id")
        if last_fulfil and not payload.get("force"):
            last = await db.fulfilments.find_one({"_id": ObjectId(last_fulfil)})
            if last:
                last_at = ensure_utc(last.get("created_at") or last.get("fulfilled_at"))
                if last_at:
                    gap_hours = (now - last_at).total_seconds() / 3600
                    if gap_hours < settings.REPEAT_MIN_GAP_HOURS:
                        raise TransitionError(
                            f"Η επανάληψη έγινε ήδη πριν από {gap_hours:.1f} ώρες. "
                            f"Ελάχιστο μεσοδιάστημα: {settings.REPEAT_MIN_GAP_HOURS} ώρες. "
                            "Χρησιμοποίησε force=true για παράκαμψη.",
                            status_code=409
                        )

    # ── Build status_history entry ───────────────────────────────────────
    history_entry = {
        "from_status": from_status,
        "to": to_status,
        "at": now,
        "by": user_id,
    }
    if payload.get("note"):
        history_entry["note"] = payload["note"]

    # ── Build update document ────────────────────────────────────────────
    update_fields: dict = {
        "status": to_status,
        "updated_at": now,
    }

    # ── Per-action payload application ───────────────────────────────────
    if action == "assess":
        update_fields["need_categories"] = payload.get("need_categories", [])
        if "est_monthly_value" in payload:
            update_fields["est_monthly_value"] = payload["est_monthly_value"]
        if "needs_freehand" in payload:
            update_fields["needs_freehand"] = payload["needs_freehand"]
        if "proposed_skus" in payload:
            update_fields["proposed_skus"] = payload["proposed_skus"]

    elif action == "to_doctor":
        update_fields["doctor_id"] = payload["doctor_id"]
        if "doctor_appointment_at" in payload:
            update_fields["doctor_appointment_at"] = payload["doctor_appointment_at"]

    elif action == "prescribe":
        update_fields["prescription_id"] = payload["prescription_id"]
        # REQ-01: Set fulfil SLA deadline
        update_fields["fulfil_sla_due"] = now + timedelta(days=settings.FULFIL_SLA_DAYS)

    elif action == "fulfil":
        is_repeat = from_status == "ACTIVE" or from_status == "AT_RISK"
        if "patient_id" in payload:
            update_fields["patient_id"] = payload["patient_id"]
        if "fulfilment_id" in payload:
            update_fields["last_fulfilment_id"] = payload["fulfilment_id"]

        # Cycle management
        current_cycle = case.get("cycle_number", 0) if is_repeat else 0
        update_fields["cycle_number"] = current_cycle + 1 if is_repeat else 1

        # REQ-02: no_repeat — check if ALL SKU items in fulfilment are no_repeat
        no_repeat = payload.get("no_repeat", False)
        if no_repeat:
            update_fields["no_repeat"] = True
            # No clock for one-shot products
            update_fields["repeat_clock_start"] = None
            update_fields["next_repeat_due"] = None
        else:
            # Clock starts from the chosen fulfilment date (falls back to now)
            base = _as_dt(payload.get("fulfilled_at")) or now
            duration_days = payload.get("duration_days", 30)
            update_fields["repeat_clock_start"] = base
            update_fields["next_repeat_due"] = base + timedelta(days=duration_days)

    elif action == "lose":
        update_fields["lost_reason"] = payload["lost_reason"]
        update_fields["lost_reason_text"] = payload.get("lost_reason_text", "")
        if "lost_competitor" in payload:
            update_fields["lost_competitor"] = payload["lost_competitor"]
        if "lost_note" in payload:
            update_fields["lost_note"] = payload["lost_note"]

    elif action == "close":
        update_fields["closed_reason"] = payload["closed_reason"]

    elif action == "reactivate":
        pass  # just status change back to ACTIVE

    elif action == "reassign":
        # REQ-01: Clear doctor and optionally assign new one
        update_fields["doctor_id"] = None
        update_fields["doctor_appointment_at"] = None
        if "new_doctor_id" in payload and payload["new_doctor_id"]:
            update_fields["doctor_id"] = payload["new_doctor_id"]

    elif action == "at_risk":
        pass  # just status change

    # ── Atomic update ────────────────────────────────────────────────────
    await db.cases.update_one(
        {"_id": ObjectId(case_id)},
        {
            "$set": update_fields,
            "$push": {"status_history": history_entry},
        },
    )

    # ── Side-effects ─────────────────────────────────────────────────────
    await _side_effects(db, case, from_status, to_status, action, payload, user_id, now, org_id)

    # Return updated case
    case = await db.cases.find_one({"_id": ObjectId(case_id)})
    if case:
        case["_id"] = str(case["_id"])
    return case


# ── Side-effects ─────────────────────────────────────────────────────────────

async def _side_effects(
    db: AsyncIOMotorDatabase,
    case: dict,
    from_status: str,
    to_status: str,
    action: str,
    payload: dict,
    user_id: str,
    now: datetime,
    org_id: str,
):
    """Execute all side-effects for a transition. Called atomically after the case update."""

    case_id = str(case["_id"])

    # ── AUDIT LOG (every transition) ─────────────────────────────────────
    await db.audit_log.insert_one({
        "org_id": org_id,
        "user_id": user_id,
        "action": f"case.{action}",
        "collection": "cases",
        "doc_id": case_id,
        "before": {"status": from_status},
        "after": {"status": to_status},
        "at": now,
    })

    # ── SOURCE metrics ───────────────────────────────────────────────────
    source_id = case.get("source_id")
    if source_id:
        source_updates: dict = {"updated_at": now}
        if to_status == "ACTIVE":
            source_updates["cases_active"] = 1  # will be $inc
        if to_status == "LOST":
            source_updates["cases_lost"] = 1
        if to_status == "CLOSED":
            source_updates["cases_closed"] = 1

        # Apply $inc for counters
        inc_fields = {}
        for k, v in source_updates.items():
            if isinstance(v, int) and k != "updated_at":
                inc_fields[k] = v
        if inc_fields:
            await db.sources.update_one(
                {"_id": ObjectId(source_id)},
                {"$inc": inc_fields, "$set": {"updated_at": now, "last_referral_at": now}},
            )
        else:
            await db.sources.update_one(
                {"_id": ObjectId(source_id)},
                {"$set": {"updated_at": now}},
            )

    # ── DOCTOR metrics ───────────────────────────────────────────────────
    doctor_id = case.get("doctor_id") or payload.get("doctor_id")
    if doctor_id:
        doc_updates: dict = {"updated_at": now}
        doc_inc: dict = {}

        if to_status == "AT_DOCTOR":
            doc_inc["open_cases_count"] = 1
            doc_updates["last_visit_at"] = now

        if to_status == "PRESCRIBED":
            doc_inc["rx_count_90d"] = 1
            # Calculate pickup days
            status_history = case.get("status_history", [])
            entered_doctor_at = None
            for entry in status_history:
                if entry.get("to") == "AT_DOCTOR":
                    entered_doctor_at = entry.get("at")
            entered_doctor_at = ensure_utc(entered_doctor_at)
            if entered_doctor_at:
                pickup_days = (now - entered_doctor_at).days
                # Rolling average
                current_avg = (await db.doctors.find_one({"_id": ObjectId(doctor_id)})) or {}
                old_avg = current_avg.get("avg_pickup_days")
                old_count = max(current_avg.get("rx_count_90d", 0), 1)
                if old_avg:
                    new_avg = (old_avg * (old_count - 1) + pickup_days) / old_count
                else:
                    new_avg = pickup_days
                doc_updates["avg_pickup_days"] = round(new_avg, 1)

        if to_status == "ACTIVE" and from_status == "PRESCRIBED":
            doc_inc["open_cases_count"] = -1

        # REQ-01: reassign decrements old doctor's open_cases_count
        if action == "reassign":
            doc_inc["open_cases_count"] = -1

        # REQ-01: lose from PRESCRIBED/AT_DOCTOR decrements doctor counter
        if action == "lose" and from_status in ("AT_DOCTOR", "PRESCRIBED"):
            doc_inc["open_cases_count"] = -1

        if doc_inc:
            await db.doctors.update_one(
                {"_id": ObjectId(doctor_id)},
                {"$inc": doc_inc, "$set": doc_updates},
            )
        elif doc_updates:
            await db.doctors.update_one(
                {"_id": ObjectId(doctor_id)},
                {"$set": doc_updates},
            )

    # ── PATIENT creation (→ACTIVE) ──────────────────────────────────────
    if to_status == "ACTIVE":
        existing = await db.patients.find_one({"case_id": case_id, "org_id": org_id})
        if not existing:
            await db.patients.insert_one({
                "org_id": org_id,
                "case_id": case_id,
                "name": "",
                "phone": "",
                "address": "",
                "consent_at": now,
                "erased": False,
                "created_at": now,
                "updated_at": now,
            })

    # ── NOTIFICATION purge (→LOST / →CLOSED) ────────────────────────────
    if to_status in ("LOST", "CLOSED"):
        # Delete ALL notifications referencing this case
        await db.notifications.delete_many({"org_id": org_id, "ref_id": case_id})
        # Also delete notifications for fulfilments/prescriptions of this case
        fulfil_ids = []
        async for f in db.fulfilments.find({"case_id": case_id, "org_id": org_id}, {"_id": 1}):
            fulfil_ids.append(str(f["_id"]))
        for fid in fulfil_ids:
            await db.notifications.delete_many({"org_id": org_id, "ref_id": fid})
        presc_ids = []
        async for p in db.prescriptions.find({"case_id": case_id, "org_id": org_id}, {"_id": 1}):
            presc_ids.append(str(p["_id"]))
        for pid in presc_ids:
            await db.notifications.delete_many({"org_id": org_id, "ref_id": pid})

    # ── NOTIFICATION creation (various triggers) ─────────────────────────
    if to_status == "ACTIVE" and action == "fulfil" and not payload.get("no_repeat"):
        # Seed the next REPEAT_DUE reminder now (e.g. 28 days for a 30-day rx)
        base = _as_dt(payload.get("fulfilled_at")) or now
        duration_days = payload.get("duration_days", 30)
        notify_days = payload.get("notify_days", 2)
        due_at = base + timedelta(days=max(0, duration_days - notify_days))
        # Replace any still-unseen REPEAT_DUE for this case (previous cycle)
        await db.notifications.delete_many({
            "org_id": org_id, "ref_id": case_id, "kind": "REPEAT_DUE", "seen": False,
        })
        await db.notifications.insert_one({
            "org_id": org_id,
            "kind": "REPEAT_DUE",
            "ref_type": "case",
            "ref_id": case_id,
            "due_at": due_at,
            "seen": False,
            "dismissed": False,
            "created_at": now,
        })

    if to_status == "AT_RISK":
        await db.notifications.insert_one({
            "org_id": org_id,
            "kind": "AT_RISK",
            "ref_type": "case",
            "ref_id": case_id,
            "due_at": now,
            "seen": False,
            "dismissed": False,
            "created_at": now,
        })

    # ── AI MEMORY: every transition becomes an episode ──────────────────
    # Ο Ασκληπιός ξέρει έτσι όλη την πορεία κάθε περιστατικού.
    try:
        from backend.llm.memory import log_episode
        alias = case.get("alias") or "Περιστατικό"
        entities = [{"name": alias, "type": "case"}]
        if doctor_id:
            d = await db.doctors.find_one({"_id": ObjectId(doctor_id)}, {"name": 1})
            if d and d.get("name"):
                entities.append({"name": d["name"], "type": "doctor"})
        source = await db.sources.find_one({"_id": ObjectId(case["source_id"])}, {"name": 1}) if case.get("source_id") else None
        if source and source.get("name"):
            entities.append({"name": source["name"], "type": "source"})
        summary = f"Περιστατικό «{alias}»: {from_status} → {to_status}"
        if payload.get("note"):
            summary += f" — {str(payload['note'])[:150]}"
        await log_episode(
            db, org_id, "case_update", summary, entities,
            {"case_id": case_id, "from_status": from_status, "to_status": to_status, "action": action},
        )
    except Exception:
        logger.exception(f"Failed to log episode for case {case_id}")


# ── Query helpers ────────────────────────────────────────────────────────────

def get_allowed_actions(status: str) -> list[str]:
    """Return list of valid actions from a given status."""
    return [a for (s, a) in TRANSITIONS if s == status]


def get_status_target(action: str, from_status: str) -> Optional[str]:
    """Return the target status for a given action from a status, or None."""
    return TRANSITIONS.get((from_status, action))
