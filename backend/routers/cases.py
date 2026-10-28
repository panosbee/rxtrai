"""
RxTrail — Cases router: CRUD + transition endpoint (THE only way to change status).
"""
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr, Field

from backend.auth.deps import get_current_user, get_org
from backend.config import settings
from backend.core.state_machine import TransitionError, get_allowed_actions, transition
from backend.core.clocks import ensure_utc
from backend.db import get_db, scoped
from backend.models.case import CaseCreate, CaseTransitionRequest, CaseUpdate

router = APIRouter(prefix="/cases", tags=["cases"])


class HandoverRequest(BaseModel):
    target_user_email: EmailStr
    note: str = Field("", max_length=500)


async def _coll(request: Request, org_id: str):
    db = await get_db()
    return scoped(db.cases, org_id)


def _serialize(doc: dict) -> dict:
    if not doc:
        return doc
    d = dict(doc)
    if "_id" in d:
        d["_id"] = str(d["_id"])
    if "org_id" in d:
        d["org_id"] = str(d["org_id"]) if isinstance(d["org_id"], ObjectId) else d["org_id"]
    if "source_id" in d and isinstance(d.get("source_id"), ObjectId):
        d["source_id"] = str(d["source_id"])
    if "doctor_id" in d and isinstance(d.get("doctor_id"), ObjectId):
        d["doctor_id"] = str(d["doctor_id"])
    if "patient_id" in d and isinstance(d.get("patient_id"), ObjectId):
        d["patient_id"] = str(d["patient_id"])
    if "last_fulfilment_id" in d and isinstance(d.get("last_fulfilment_id"), ObjectId):
        d["last_fulfilment_id"] = str(d["last_fulfilment_id"])
    return d


@router.get("")
async def list_cases(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
    status: str = Query(""),
    source_id: str = Query(""),
    doctor_id: str = Query(""),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    sort: str = "-created_at",
):
    """List cases with optional filters."""
    db = await get_db()
    filt: dict = {"org_id": org_id}
    if status:
        filt["status"] = status
    if source_id:
        filt["source_id"] = ObjectId(source_id)
    if doctor_id:
        filt["doctor_id"] = ObjectId(doctor_id)
    q = request.query_params.get("q", "").strip()
    if q:
        import re as _re
        import unicodedata
        def _norm(s):
            return ''.join(ch for ch in unicodedata.normalize('NFD', s or '').lower()
                           if unicodedata.category(ch) != 'Mn')
        def _rx(s):
            acc = {'α': '[αά]', 'ε': '[εέ]', 'η': '[ηή]', 'ι': '[ιίϊΐ]', 'ο': '[οό]',
                   'υ': '[υύϋΰ]', 'ω': '[ωώ]', 'σ': '[σς]'}
            return ''.join(acc.get(ch, _re.escape(ch)) for ch in _norm(s))
        rx = _rx(q)
        filt["$or"] = [
            {"alias": {"$regex": rx, "$options": "i"}},
            {"amka": {"$regex": rx, "$options": "i"}},
        ]

    sort_field = sort.lstrip("-")
    sort_dir = -1 if sort.startswith("-") else 1

    cursor = db.cases.find(filt).sort(sort_field, sort_dir).skip(skip).limit(limit)
    items = [_serialize(d) async for d in cursor]
    total = await db.cases.count_documents(filt)
    return {"items": items, "total": total}


@router.get("/kanban")
async def cases_kanban(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Return cases grouped by status for kanban view. Limited to non-terminal statuses."""
    db = await get_db()
    statuses = ["NEW", "ASSESSED", "AT_DOCTOR", "PRESCRIBED", "ACTIVE", "AT_RISK"]
    result = {}
    for s in statuses:
        cursor = db.cases.find({"org_id": org_id, "status": s}).sort("created_at", -1).limit(30)
        result[s] = [_serialize(d) async for d in cursor]
    return result


@router.get("/{case_id}")
async def get_case(
    case_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Get a single case with full details."""
    db = await get_db()
    case = await db.cases.find_one({"_id": ObjectId(case_id), "org_id": org_id})
    if not case:
        raise HTTPException(404, "Case not found")

    result = _serialize(case)

    # Embed related entities
    if case.get("source_id"):
        src = await db.sources.find_one({"_id": ObjectId(case["source_id"]), "org_id": org_id})
        if src:
            src["_id"] = str(src["_id"])
            result["source"] = src

    if case.get("doctor_id"):
        doc = await db.doctors.find_one({"_id": ObjectId(case["doctor_id"]), "org_id": org_id})
        if doc:
            doc["_id"] = str(doc["_id"])
            result["doctor"] = doc

    if case.get("patient_id"):
        pat = await db.patients.find_one({"_id": ObjectId(case["patient_id"]), "org_id": org_id})
        if pat:
            pat["_id"] = str(pat["_id"])
            result["patient"] = pat

    # Prescriptions
    presc_cursor = db.prescriptions.find({"case_id": case_id, "org_id": org_id}).sort("issued_at", -1)
    result["prescriptions"] = [_serialize(p) async for p in presc_cursor]

    # Fulfilments
    fulf_cursor = db.fulfilments.find({"case_id": case_id, "org_id": org_id}).sort("fulfilled_at", -1)
    result["fulfilments"] = [_serialize(f) async for f in fulf_cursor]

    result["allowed_actions"] = get_allowed_actions(case["status"])

    return result


@router.post("", status_code=201)
async def create_case(
    data: CaseCreate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Create a new case (intake confirm). Status starts as NEW."""
    now = datetime.now(timezone.utc)
    db = await get_db()

    doc = {
        **data.model_dump(),
        "org_id": org_id,
        "status": "NEW",
        "created_by": str(current_user["_id"]),
        "created_at": now,
        "updated_at": now,
        "status_history": [{
            "from_status": None,
            "to": "NEW",
            "at": now,
            "by": str(current_user["_id"]),
        }],
    }
    result = await db.cases.insert_one(doc)
    doc["_id"] = str(result.inserted_id)

    # Update source metrics (αν υπάρχει πηγή)
    if data.source_id and ObjectId.is_valid(data.source_id):
        await db.sources.update_one(
            {"_id": ObjectId(data.source_id)},
            {"$inc": {"cases_brought": 1}, "$set": {"last_referral_at": now}},
        )

    # AI memory: new case episode
    try:
        from backend.llm.memory import log_episode
        alias = doc.get("alias") or "Περιστατικό"
        entities = [{"name": alias, "type": "case"}]
        if data.source_id and ObjectId.is_valid(data.source_id):
            src = await db.sources.find_one({"_id": ObjectId(data.source_id)}, {"name": 1})
            if src and src.get("name"):
                entities.append({"name": src["name"], "type": "source"})
        summary = f"Νέο περιστατικό «{alias}»"
        if doc.get("area"):
            summary += f" — περιοχή {doc['area']}"
        await log_episode(
            db, org_id, "case_created", summary, entities,
            {"case_id": str(result.inserted_id), "source_id": data.source_id or None},
        )
    except Exception:
        pass

    return _serialize(doc)


@router.patch("/{case_id}")
async def update_case(
    case_id: str,
    data: CaseUpdate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Update case fields (NOT status — use transition for that)."""
    db = await get_db()
    update_data = data.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(400, "No fields to update")
    update_data["updated_at"] = datetime.now(timezone.utc)

    result = await db.cases.update_one(
        {"_id": ObjectId(case_id), "org_id": org_id},
        {"$set": update_data},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Case not found")

    case = await db.cases.find_one({"_id": ObjectId(case_id), "org_id": org_id})
    return _serialize(case)


@router.post("/{case_id}/transition")
async def transition_case(
    case_id: str,
    data: CaseTransitionRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Execute a status transition. THE ONLY WAY to change case.status."""
    db = await get_db()
    try:
        updated = await transition(
            db=db,
            case_id=case_id,
            action=data.action,
            payload=data.payload,
            user_id=str(current_user["_id"]),
        )
        return updated
    except TransitionError as e:
        raise HTTPException(e.status_code, str(e))


# ── REQ-05: One-tap repeat fulfilment ───────────────────────────────────────

class RepeatRequest(BaseModel):
    force: bool = False
    idempotency_key: Optional[str] = None


@router.post("/{case_id}/repeat")
async def repeat_fulfilment(
    case_id: str,
    data: RepeatRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """One-tap repeat: copy last fulfilment, reset clock, cycle_number+1."""
    db = await get_db()
    user_id = str(current_user["_id"])
    now = datetime.now(timezone.utc)

    # Load case
    case = await db.cases.find_one({"_id": ObjectId(case_id), "org_id": org_id})
    if not case:
        raise HTTPException(404, "Case not found")

    if case["status"] not in ("ACTIVE", "AT_RISK"):
        raise HTTPException(400, "Repeat only allowed for ACTIVE or AT_RISK cases")

    if case.get("no_repeat"):
        raise HTTPException(400, "Cannot repeat a no_repeat (one-shot) case")

    # Find last fulfilment
    last_fulfil = await db.fulfilments.find_one(
        {"case_id": case_id, "org_id": org_id},
        sort=[("fulfilled_at", -1)],
    )
    if not last_fulfil:
        raise HTTPException(400, "No previous fulfilment found to repeat")

    # Idempotency check — if last fulfil was < REPEAT_MIN_GAP_HOURS ago
    if not data.force:
        last_at = ensure_utc(last_fulfil.get("fulfilled_at") or last_fulfil.get("created_at"))
        if last_at:
            gap_hours = (now - last_at).total_seconds() / 3600
            if gap_hours < settings.REPEAT_MIN_GAP_HOURS:
                raise HTTPException(
                    409,
                    f"Repeat already done {gap_hours:.1f}h ago. Min gap: {settings.REPEAT_MIN_GAP_HOURS}h. Use force=true."
                )

    # Create new fulfilment by copying last one
    duration_days = last_fulfil.get("duration_days", 30)
    new_fulfil = {
        "org_id": org_id,
        "case_id": case_id,
        "patient_id": case.get("patient_id"),
        "items": last_fulfil.get("items", []),
        "qty_total": last_fulfil.get("qty_total", 0),
        "duration_days": duration_days,
        "fulfilled_at": now,
        "cycle_number": case.get("cycle_number", 0) + 1,
        "created_by": user_id,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.fulfilments.insert_one(new_fulfil)
    new_fulfil["_id"] = str(result.inserted_id)

    # Transition: ACTIVE/AT_RISK → ACTIVE (repeat fulfil)
    try:
        updated = await transition(
            db=db,
            case_id=case_id,
            action="fulfil",
            payload={
                "fulfilment_id": str(result.inserted_id),
                "duration_days": duration_days,
                "no_repeat": case.get("no_repeat", False),
                "fulfilled_at": now,
                "force": data.force,
            },
            user_id=user_id,
        )
        return {**updated, "new_fulfilment": _serialize(new_fulfil)}
    except TransitionError as e:
        # Rollback fulfilment insert
        await db.fulfilments.delete_one({"_id": result.inserted_id})
        raise HTTPException(e.status_code, str(e))


# ── REQ-06: Contact attempt log ─────────────────────────────────────────────

class ContactAttemptRequest(BaseModel):
    channel: str = "CALL"
    outcome: str
    next_try_at: Optional[str] = None
    note: Optional[str] = None


@router.post("/{case_id}/contact")
async def log_contact_attempt(
    case_id: str,
    data: ContactAttemptRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Log a contact attempt — does NOT change case status."""
    db = await get_db()
    user_id = str(current_user["_id"])
    now = datetime.now(timezone.utc)

    case = await db.cases.find_one({"_id": ObjectId(case_id), "org_id": org_id})
    if not case:
        raise HTTPException(404, "Case not found")

    # Parse next_try_at
    next_try = None
    if data.next_try_at:
        try:
            next_try = datetime.fromisoformat(data.next_try_at)
        except ValueError:
            raise HTTPException(400, "Invalid next_try_at format. Use ISO 8601.")

    attempt = {
        "at": now,
        "channel": data.channel,
        "outcome": data.outcome,
        "next_try_at": next_try,
        "note": data.note,
        "by": user_id,
    }

    await db.cases.update_one(
        {"_id": ObjectId(case_id)},
        {"$push": {"contact_attempts": attempt}, "$set": {"updated_at": now}},
    )

    # Audit log
    await db.audit_log.insert_one({
        "org_id": org_id,
        "user_id": user_id,
        "action": "case.contact_attempt",
        "collection": "cases",
        "doc_id": case_id,
        "after": attempt,
        "at": now,
    })

    return {"ok": True, "attempt": attempt}


# ── Handover: παράδοση περιστατικού σε συνάδελφο ──────────────────────────

@router.post("/{case_id}/handover")
async def handover_case(
    case_id: str,
    data: HandoverRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Μεταφέρει το περιστατικό (και τα συνδεδεμένα δεδομένα του) σε άλλο χρήστη.

    Μετακινούνται: το case, ο γιατρός του, η πηγή του, οι συνταγές, τα έγγραφα,
    οι εκτελέσεις, οι επισκέψεις, οι ειδοποιήσεις και τα ιδιωτικά knowledge chunks.
    Ο παραλήπτης παίρνει ειδοποίηση.
    """
    db = await get_db()

    if not ObjectId.is_valid(case_id):
        raise HTTPException(404, "Case not found")
    case = await db.cases.find_one({"_id": ObjectId(case_id), "org_id": org_id})
    if not case:
        raise HTTPException(404, "Case not found")

    target = await db.users.find_one({"email": data.target_user_email.lower()})
    if not target or not target.get("is_active", False):
        raise HTTPException(404, "Δεν βρέθηκε ενεργός συνάδελφος με αυτό το email")
    if str(target["_id"]) == str(current_user["_id"]):
        raise HTTPException(400, "Δεν μπορείς να παραδώσεις το περιστατικό στον εαυτό σου")

    target_org = target.get("org_id", "")
    if not target_org:
        raise HTTPException(400, "Ο συνάδελφος δεν έχει οργανισμό")

    now = datetime.now(timezone.utc)
    moved = {}

    async def _move_org(collection_name: str, filt: dict):
        coll = getattr(db, collection_name, None)
        if coll is None:
            return
        res = await coll.update_many(filt, {"$set": {"org_id": target_org, "updated_at": now}})
        moved[collection_name] = res.modified_count

    # Case itself
    await db.cases.update_one(
        {"_id": ObjectId(case_id)},
        {"$set": {
            "org_id": target_org,
            "updated_at": now,
            "handed_over_by": str(current_user["_id"]),
            "handed_over_by_name": current_user.get("name") or current_user.get("email", ""),
            "handed_over_from_org": org_id,
            "handed_over_at": now,
            "handover_note": data.note,
        }},
    )
    moved["cases"] = 1

    # Γιατρός + πηγή (γίνονται μέρος του δικτύου του παραλήπτη)
    if case.get("doctor_id"):
        await db.doctors.update_many(
            {"_id": ObjectId(case["doctor_id"]), "org_id": org_id},
            {"$set": {"org_id": target_org, "updated_at": now}})
    if case.get("source_id"):
        await db.sources.update_many(
            {"_id": ObjectId(case["source_id"]), "org_id": org_id},
            {"$set": {"org_id": target_org, "updated_at": now}})

    # Συνδεδεμένα
    await _move_org("prescriptions", {"case_id": case_id, "org_id": org_id})
    await _move_org("documents", {"case_id": case_id, "org_id": org_id})
    await _move_org("fulfilments", {"case_id": case_id, "org_id": org_id})
    await _move_org("visits", {"case_id": case_id, "org_id": org_id})
    await _move_org("notifications", {"ref_id": case_id, "org_id": org_id})

    # Ιδιωτικά knowledge chunks των εγγράφων/συνταγών του case
    file_ids = []
    async for p in db.prescriptions.find({"case_id": case_id, "org_id": target_org}, {"_id": 1}):
        file_ids.append(f"rx-{p['_id']}")
    async for d in db.documents.find({"case_id": case_id, "org_id": target_org}, {"_id": 1}):
        file_ids.append(f"doc-{d['_id']}")
    if file_ids:
        await _move_org("knowledge_chunks", {"file_id": {"$in": file_ids}, "org_id": org_id})
        await db.knowledge_files.update_many(
            {"file_id": {"$in": file_ids}, "org_id": org_id},
            {"$set": {"org_id": target_org, "updated_at": now}})

    # Ειδοποίηση στον παραλήπτη
    await db.notifications.insert_one({
        "org_id": target_org,
        "kind": "HANDOVER_RECEIVED",
        "ref_type": "case",
        "ref_id": case_id,
        "due_at": now,
        "seen": False,
        "dismissed": False,
        "created_at": now,
    })

    # Audit
    await db.audit_log.insert_one({
        "org_id": org_id,
        "user_id": str(current_user["_id"]),
        "action": "case.handover",
        "collection": "cases",
        "doc_id": case_id,
        "after": {"to_user": data.target_user_email, "to_org": target_org, "note": data.note},
        "at": now,
    })

    return {
        "ok": True,
        "message": f"Το περιστατικό παραδόθηκε στον {data.target_user_email}. Ο συνάδελφος θα το δει στα περιστατικά του.",
        "moved": moved,
    }


# ── Σημειώσεις περιστατικού (επεισοδιακές, timestamped) ──────────────────

class CaseNoteRequest(BaseModel):
    note: str = Field(..., min_length=1, max_length=3000)


@router.post("/{case_id}/note")
async def add_case_note(
    case_id: str,
    data: CaseNoteRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Σημείωση σε περιστατικό — μπαίνει στο case_notes (χρονολογικά) + επεισοδιακή μνήμη."""
    db = await get_db()
    if not ObjectId.is_valid(case_id):
        raise HTTPException(404, "Case not found")
    case = await db.cases.find_one({"_id": ObjectId(case_id), "org_id": org_id})
    if not case:
        raise HTTPException(404, "Case not found")

    now = datetime.now(timezone.utc)
    entry = {
        "note": data.note.strip(),
        "at": now.isoformat(),
        "by": str(current_user["_id"]),
    }
    await db.cases.update_one(
        {"_id": ObjectId(case_id), "org_id": org_id},
        {"$push": {"case_notes": entry}, "$set": {"updated_at": now}},
    )
    try:
        from backend.llm.memory import log_episode
        alias = case.get("alias", "Περιστατικό")
        await log_episode(
            db, org_id, "case_note",
            f"Σημείωση περιστατικού «{alias}»: {data.note[:200]}",
            [{"name": alias, "type": "case"}],
            {"case_id": case_id, "note": data.note[:400]},
        )
    except Exception:
        pass
    return {"ok": True, "message": "Η σημείωση αποθηκεύτηκε."}


# ── Πολλαπλές γνωματεύσεις ανά περιστατικό ────────────────────────────────

class AddPrescriptionRequest(BaseModel):
    doctor_id: str = ""
    issued_at: str = ""
    note: str = Field("", max_length=1000)


@router.post("/{case_id}/add-prescription", status_code=201)
async def add_prescription_to_case(
    case_id: str,
    data: AddPrescriptionRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Προσθέτει 2η/3η/... γνωμάτευση στο ΙΔΙΟ περιστατικό — χωρίς αλλαγή κατάστασης.

    Το περιστατικό μπορεί να έχει πολλές γνωματεύσεις (π.χ. σήμερα ο γιατρός
    έγραψε νέα συνταγή για άλλο πρόβλημα). Η κατάσταση του case ΔΕΝ αλλάζει —
    η γνωμάτευση απλώς συνδέεται και φαίνεται στη λίστα του.
    """
    db = await get_db()
    if not ObjectId.is_valid(case_id):
        raise HTTPException(404, "Case not found")
    case = await db.cases.find_one({"_id": ObjectId(case_id), "org_id": org_id})
    if not case:
        raise HTTPException(404, "Case not found")

    now = datetime.now(timezone.utc)
    issued = None
    if data.issued_at:
        try:
            issued = datetime.fromisoformat(data.issued_at)
        except ValueError:
            issued = None

    doctor_id = None
    doctor_name = ""
    if data.doctor_id:
        try:
            d = await db.doctors.find_one({"_id": ObjectId(data.doctor_id), "org_id": org_id}, {"name": 1})
            if d:
                doctor_id = data.doctor_id
                doctor_name = d.get("name", "")
        except Exception:
            doctor_id = None

    presc = {
        "org_id": org_id,
        "case_id": case_id,
        "doctor_id": doctor_id,
        "doctor_name_raw": doctor_name,
        "issued_date": issued.strftime("%Y-%m-%d") if issued else now.strftime("%Y-%m-%d"),
        "issued_at": issued or now,
        "note": data.note.strip(),
        "status": "MANUAL",
        "created_by": str(current_user["_id"]),
        "created_at": now,
        "updated_at": now,
    }
    result = await db.prescriptions.insert_one(presc)
    pid = str(result.inserted_id)

    # Κράτα τη λίστα γνωματεύσεων στο case (συμβατό με το παλιό prescription_id)
    await db.cases.update_one(
        {"_id": ObjectId(case_id), "org_id": org_id},
        {"$addToSet": {"prescription_ids": pid},
         "$set": {"prescription_id": pid, "updated_at": now}},
    )

    try:
        from backend.llm.memory import log_episode
        alias = case.get("alias", "Περιστατικό")
        await log_episode(
            db, org_id, "prescription",
            f"Νέα γνωμάτευση στο «{alias}»"
            + (f" από {doctor_name}" if doctor_name else "")
            + (f" ({issued.strftime('%d/%m/%Y')})" if issued else ""),
            [{"name": alias, "type": "case"}] + ([{"name": doctor_name, "type": "doctor"}] if doctor_name else []),
            {"case_id": case_id, "prescription_id": pid},
        )
    except Exception:
        pass

    return {
        "ok": True,
        "id": pid,
        "message": f"Η γνωμάτευση προστέθηκε στο περιστατικό ({len(case.get('prescription_ids', [])) + 1} συνολικά).",
    }
