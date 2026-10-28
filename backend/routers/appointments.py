"""
RxTrail — Appointments router: CRUD + conflict detection + month/week views.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.auth.deps import get_current_user, get_org
from backend.db import get_db
from backend.models.appointment import AppointmentCreate, AppointmentUpdate

router = APIRouter(prefix="/appointments", tags=["appointments"])

CONFLICT_BUFFER_MINUTES = 15  # If appointments are within this window → conflict


class AppointmentNoteRequest(BaseModel):
    note: str = Field(..., min_length=1, max_length=3000)


def _serialize(doc: dict) -> dict:
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    # Ensure datetime fields serialize as ISO with Z (UTC)
    for f in ("at", "end_at", "created_at", "updated_at"):
        val = doc.get(f)
        if val and isinstance(val, datetime):
            if val.tzinfo is None:
                val = val.replace(tzinfo=timezone.utc)
            doc[f] = val.isoformat()
    return doc


@router.get("")
async def list_appointments(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
    from_date: str = Query(""),
    to_date: str = Query(""),
    kind: str = Query(""),
    doctor_id: str = Query(""),
    case_id: str = Query(""),
):
    """List appointments in a date range."""
    db = await get_db()
    filt: dict = {"org_id": org_id}

    if from_date:
        try: filt["at"] = {"$gte": datetime.fromisoformat(from_date)}
        except ValueError: pass
    if to_date:
        try:
            to_dt = datetime.fromisoformat(to_date)
            filt.setdefault("at", {})["$lte"] = to_dt
        except ValueError: pass
    if kind:
        filt["kind"] = kind
    if doctor_id:
        filt["doctor_id"] = doctor_id
    if case_id:
        filt["case_id"] = case_id

    cursor = db.appointments.find(filt).sort("at", 1)
    items = [_serialize(d) async for d in cursor]
    return {"items": items, "total": len(items)}


@router.get("/month/{year}/{month}")
async def month_view(
    year: int, month: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Get all appointments for a specific month."""
    db = await get_db()
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)

    cursor = db.appointments.find({
        "org_id": org_id,
        "at": {"$gte": start, "$lt": end},
    }).sort("at", 1)

    items = [_serialize(d) async for d in cursor]
    return {"items": items, "total": len(items), "month": month, "year": year}


@router.get("/week")
async def week_view(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
    date: str = Query("", description="Any date in the target week (ISO format)"),
):
    """Get all appointments for the week containing the given date."""
    db = await get_db()
    try:
        dt = datetime.fromisoformat(date) if date else datetime.now(timezone.utc)
    except ValueError:
        dt = datetime.now(timezone.utc)

    monday = dt - timedelta(days=dt.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    sunday = monday + timedelta(days=7)

    cursor = db.appointments.find({
        "org_id": org_id,
        "at": {"$gte": monday, "$lt": sunday},
    }).sort("at", 1)

    items = [_serialize(d) async for d in cursor]

    # Group by day
    days = {0: [], 1: [], 2: [], 3: [], 4: [], 5: [], 6: []}
    for item in items:
        at = item.get("at")
        if isinstance(at, str): at = datetime.fromisoformat(at)
        if at:
            days[at.weekday()].append(item)

    return {
        "items": items,
        "by_day": {str(k): v for k, v in days.items()},
        "week_start": monday.isoformat(),
    }


@router.get("/{appointment_id}")
async def get_appointment(
    appointment_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    db = await get_db()
    doc = await db.appointments.find_one({"_id": ObjectId(appointment_id), "org_id": org_id})
    if not doc: raise HTTPException(404, "Appointment not found")
    return _serialize(doc)


@router.post("", status_code=201)
async def create_appointment(
    data: AppointmentCreate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Create an appointment. Checks for conflicts automatically."""
    now = datetime.now(timezone.utc)
    db = await get_db()

    # Conflict detection
    conflicts = await _find_conflicts(db, org_id, data.at, data.end_at, data.doctor_id)
    has_conflict = len(conflicts) > 0

    end_at = data.end_at or (data.at + timedelta(minutes=30))

    # Ensure at/end_at are UTC-aware for MongoDB
    at_val = data.at if data.at.tzinfo else data.at.replace(tzinfo=timezone.utc)
    end_val = end_at if end_at.tzinfo else end_at.replace(tzinfo=timezone.utc)

    doc = {
        **data.model_dump(),
        "at": at_val,
        "end_at": end_val,
        "org_id": org_id,
        "created_by": str(current_user["_id"]),
        "created_at": now,
        "updated_at": now,
    }
    if doc.get("notes"):
        doc["note_history"] = [{
            "note": doc["notes"],
            "at": now.isoformat(),
            "by": str(current_user["_id"]),
        }]
    result = await db.appointments.insert_one(doc)
    doc["_id"] = str(result.inserted_id)

    # Log episode for AI memory (so strategy can reference past appointments)
    if doc.get("doctor_id") and doc.get("notes"):
        try:
            from backend.llm.memory import log_episode
            doctor = await db.doctors.find_one({"_id": ObjectId(doc["doctor_id"]), "org_id": org_id})
            if doctor:
                await log_episode(
                    db, org_id, "appointment_note",
                    f"Ραντεβού με {doctor.get('name')} ({str(doc.get('at',''))[:10]}): {doc['notes'][:200]}",
                    [{"name": doctor.get("name"), "type": "doctor"}],
                    {"appointment_id": str(result.inserted_id), "note": doc["notes"][:300]},
                )
        except Exception:
            pass

    return {
        **_serialize(doc),
        "conflicts": [_serialize(c) for c in conflicts],
        "has_conflict": has_conflict,
    }


@router.patch("/{appointment_id}")
async def update_appointment(
    appointment_id: str,
    data: AppointmentUpdate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    db = await get_db()
    update_data = data.model_dump(exclude_unset=True)
    if not update_data: raise HTTPException(400, "No fields to update")

    # If time is changing, check conflicts
    if "at" in update_data or "end_at" in update_data:
        existing = await db.appointments.find_one({"_id": ObjectId(appointment_id), "org_id": org_id})
        if existing:
            new_at = update_data.get("at", existing.get("at"))
            new_end = update_data.get("end_at", existing.get("end_at"))
            doctor_id = update_data.get("doctor_id", existing.get("doctor_id"))
            conflicts = await _find_conflicts(db, org_id, new_at, new_end, doctor_id, exclude_id=appointment_id)
            if conflicts:
                update_data["_conflicts"] = [_serialize(c) for c in conflicts]

    update_data["updated_at"] = datetime.now(timezone.utc)

    r = await db.appointments.update_one(
        {"_id": ObjectId(appointment_id), "org_id": org_id},
        {"$set": update_data},
    )
    if r.matched_count == 0: raise HTTPException(404, "Appointment not found")

    # ── Chronological note history ──
    # If notes changed, append to note_history (timestamped) so the AI can read
    # the evolution of the relationship over time.
    if "notes" in update_data:
        now_utc = datetime.now(timezone.utc)
        await db.appointments.update_one(
            {"_id": ObjectId(appointment_id), "org_id": org_id},
            {"$push": {"note_history": {
                "note": update_data["notes"],
                "at": now_utc.isoformat(),
                "by": str(current_user["_id"]),
            }}},
        )
        # Log to episodic memory so the agent can find it by doctor name
        try:
            existing = await db.appointments.find_one({"_id": ObjectId(appointment_id), "org_id": org_id})
            if existing and existing.get("doctor_id"):
                from backend.llm.memory import log_episode
                from backend.db import get_db as _get_db
                doctor = await db.doctors.find_one({"_id": ObjectId(existing["doctor_id"]), "org_id": org_id})
                if doctor:
                    await log_episode(
                        db, org_id, "appointment_note",
                        f"Σημείωση ραντεβού με {doctor.get('name')}: {update_data['notes'][:200]}",
                        [{"name": doctor.get("name"), "type": "doctor"}],
                        {"appointment_id": appointment_id, "note": update_data["notes"][:300]},
                    )
        except Exception:
            pass

    doc = await db.appointments.find_one({"_id": ObjectId(appointment_id), "org_id": org_id})
    result = _serialize(doc)
    if "_conflicts" in update_data:
        result["conflicts"] = update_data["_conflicts"]
        result["has_conflict"] = True
    return result


@router.delete("/{appointment_id}", status_code=204)
async def delete_appointment(
    appointment_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    db = await get_db()
    r = await db.appointments.delete_one({"_id": ObjectId(appointment_id), "org_id": org_id})
    if r.deleted_count == 0: raise HTTPException(404, "Appointment not found")


@router.post("/{appointment_id}/note")
async def add_appointment_note(
    appointment_id: str,
    data: AppointmentNoteRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Προσθήκη σημείωσης σε ραντεβού — επεισοδιακή μνήμη με timestamp.

    Κάθε σημείωση μπαίνει στο note_history (χρονολογικά) και στην επεισοδιακή
    μνήμη του agent, δεμένη με τον γιατρό του ραντεβού — έτσι ο Ασκληπιός
    ξέρει «τι ειπώθηκε στις 24/8» και το χρησιμοποιεί στο επόμενο ραντεβού.
    """
    db = await get_db()
    if not ObjectId.is_valid(appointment_id):
        raise HTTPException(404, "Appointment not found")
    appt = await db.appointments.find_one({"_id": ObjectId(appointment_id), "org_id": org_id})
    if not appt:
        raise HTTPException(404, "Appointment not found")

    note_text = data.note.strip()
    now = datetime.now(timezone.utc)

    # Ημερομηνία αναφοράς = η ημερομηνία του ραντεβού (τι έμαθα ΕΚΕΙΝΗ τη μέρα)
    appt_at = appt.get("at")
    if isinstance(appt_at, str):
        try:
            appt_at = datetime.fromisoformat(appt_at)
        except ValueError:
            appt_at = now
    note_date = (appt_at or now).strftime("%Y-%m-%d %H:%M")

    entry = {
        "note": note_text,
        "at": now.isoformat(),
        "note_date": note_date,
        "by": str(current_user["_id"]),
    }
    await db.appointments.update_one(
        {"_id": ObjectId(appointment_id), "org_id": org_id},
        {"$push": {"note_history": entry},
         "$set": {"notes": note_text, "updated_at": now}},
    )

    # Επεισοδιακή μνήμη — δεμένη με τον γιατρό (ή το case)
    doctor = None
    if appt.get("doctor_id"):
        try:
            doctor = await db.doctors.find_one({"_id": ObjectId(appt["doctor_id"]), "org_id": org_id})
        except Exception:
            doctor = None
    from backend.llm.memory import log_episode
    if doctor:
        await log_episode(
            db, org_id, "appointment_note",
            f"Σημείωση ραντεβού ({note_date}) με {doctor.get('name')}: {note_text[:200]}",
            [{"name": doctor.get("name"), "type": "doctor"}],
            {"appointment_id": appointment_id, "note": note_text[:400]},
        )
    else:
        await log_episode(
            db, org_id, "appointment_note",
            f"Σημείωση ραντεβού ({note_date}): {note_text[:200]}",
            [{"name": appt.get("title", "ραντεβού"), "type": "appointment"}],
            {"appointment_id": appointment_id, "note": note_text[:400]},
        )

    return {"ok": True, "message": "Η σημείωση αποθηκεύτηκε — τη γνωρίζει και ο Ασκληπιός.", "note_date": note_date}


@router.post("/check-conflicts")
async def check_conflicts(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Check for conflicts at a given time without creating."""
    body = await request.json()
    at = body.get("at")
    end_at = body.get("end_at")
    doctor_id = body.get("doctor_id")

    if not at: raise HTTPException(400, "Missing 'at'")
    try: at_dt = datetime.fromisoformat(at)
    except ValueError: raise HTTPException(400, "Invalid 'at' format")

    end_dt = datetime.fromisoformat(end_at) if end_at else None
    db = await get_db()
    conflicts = await _find_conflicts(db, org_id, at_dt, end_dt, doctor_id)
    return {"conflicts": [_serialize(c) for c in conflicts], "has_conflict": len(conflicts) > 0}


# ── Conflict detection ───────────────────────────────────────────────────────

async def _find_conflicts(
    db, org_id: str, at: datetime, end_at: Optional[datetime] = None,
    doctor_id: Optional[str] = None, exclude_id: Optional[str] = None,
) -> list[dict]:
    """Find appointments that conflict with the given time range."""
    if not end_at:
        end_at = at + timedelta(minutes=30)

    # Expand search window by buffer
    search_start = at - timedelta(minutes=CONFLICT_BUFFER_MINUTES)
    search_end = end_at + timedelta(minutes=CONFLICT_BUFFER_MINUTES)

    query: dict = {
        "org_id": org_id,
        "at": {"$lte": search_end},
    }
    # End time overlaps
    query["$or"] = [
        {"end_at": {"$gte": search_start}},
        {"end_at": None, "at": {"$gte": search_start}},
    ]

    if doctor_id:
        query["doctor_id"] = doctor_id

    if exclude_id:
        query["_id"] = {"$ne": ObjectId(exclude_id)}

    cursor = db.appointments.find(query).sort("at", 1)
    conflicts = []
    async for doc in cursor:
        doc_at = doc.get("at")
        doc_end = doc.get("end_at") or (doc_at + timedelta(minutes=30))
        if doc_at and doc_end:
            # Check actual overlap (not just buffer)
            if doc_at < end_at and doc_end > at:
                conflicts.append(doc)

    return conflicts
