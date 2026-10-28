"""
RxTrail — Visits router: CRUD for Channel 2 (classic doctor/contact/structure visits).
"""
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.auth.deps import get_current_user, get_org
from backend.db import get_db
from backend.models.visit import VisitCreate, VisitUpdate

router = APIRouter(prefix="/visits", tags=["visits"])


def _serialize(doc: dict) -> dict:
    if not doc:
        return doc
    d = dict(doc)
    if "_id" in d:
        d["_id"] = str(d["_id"])
    return d


@router.get("")
async def list_visits(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
    target_type: str = Query(""),
    target_id: str = Query(""),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """List visits, optionally filtered by target."""
    db = await get_db()
    filt: dict = {"org_id": org_id}
    if target_type:
        filt["target_type"] = target_type
    if target_id:
        filt["target_id"] = target_id

    cursor = db.visits.find(filt).sort("at", -1).skip(skip).limit(limit)
    items = [_serialize(d) async for d in cursor]
    total = await db.visits.count_documents(filt)
    return {"items": items, "total": total}


@router.get("/{visit_id}")
async def get_visit(
    visit_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    db = await get_db()
    doc = await db.visits.find_one({"_id": ObjectId(visit_id), "org_id": org_id})
    if not doc:
        raise HTTPException(404, "Visit not found")
    return _serialize(doc)


@router.post("", status_code=201)
async def create_visit(
    data: VisitCreate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Log a visit. Updates doctor/contact metrics. Auto-extracts actions from voice notes."""
    now = datetime.now(timezone.utc)
    db = await get_db()

    # Auto-extract follow-up actions from voice note via LLM
    extracted_actions = []
    if data.voice_note_text and data.voice_note_text.strip():
        try:
            import logging
            logging.basicConfig(level=logging.INFO)
            logger = logging.getLogger("rxtrail.visit_extract")
            from backend.llm.client import simple_call
            prompt = (
                "Από τη φωνητική σημείωση επίσκεψης ενός ιατρικού επισκέπτη, εξήγαγε τις ΕΚΚΡΕΜΕΙΣ ΕΝΕΡΓΕΙΕΣ "
                "(follow-up tasks) που πρέπει να γίνουν. Παραδείγματα: 'θέλει δείγματα σε 2 βδομάδες' → "
                '{"text": "Φέρε δείγματα", "days": 14}. "να τον ξαναπάρω σε 1 μήνα" → {"text": "Ξαναπήγαινε", "days": 30}.\n'
                'Απάντησε ΜΟΝΟ με JSON array: [{"text": "...", "days": N}]. Αν δεν υπάρχουν ενέργειες, '
                "απάντησε ΜΟΝΟ []\n\nΣημείωση: " + data.voice_note_text[:1500]
            )
            raw = (await simple_call(prompt, temperature=0, max_tokens=300)).strip()
            logger.info(f"Visit extract raw: {raw[:300]}")
            import json, re
            # Try full parse first, then fall back to first [..] block
            parsed = None
            try:
                parsed = json.loads(raw)
            except Exception:
                m = re.search(r'\[.*\]', raw, re.DOTALL)
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                    except Exception:
                        parsed = None
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and item.get("text"):
                        days = int(item.get("days", 7))
                        from datetime import timedelta
                        extracted_actions.append({
                            "text": item["text"][:200],
                            "due": (now + timedelta(days=days)).isoformat(),
                            "confirmed": False,
                        })
        except Exception as e:
            logger.error(f"Visit extract FAILED: {e}")
            extracted_actions = []

    doc = {
        **data.model_dump(),
        "org_id": org_id,
        "created_by": str(current_user["_id"]),
        "at": data.at or now,
        "created_at": now,
        "updated_at": now,
    }
    if extracted_actions:
        doc["extracted_actions"] = extracted_actions
    result = await db.visits.insert_one(doc)
    doc["_id"] = str(result.inserted_id)

    # Update target's last_visit_at
    if data.target_type == "DOCTOR":
        await db.doctors.update_one(
            {"_id": ObjectId(data.target_id), "org_id": org_id},
            {"$set": {"last_visit_at": now, "updated_at": now}},
        )
    elif data.target_type == "CONTACT":
        # Contacts don't have last_visit_at in the schema, but we can add it
        await db.contacts.update_one(
            {"_id": ObjectId(data.target_id), "org_id": org_id},
            {"$set": {"updated_at": now}},
        )

    # If outcome is PRESCRIBED, update doctor rx_count
    if data.outcome == "PRESCRIBED" and data.target_type == "DOCTOR":
        await db.doctors.update_one(
            {"_id": ObjectId(data.target_id), "org_id": org_id},
            {"$inc": {"rx_count_90d": 1}, "$set": {"updated_at": now}},
        )

    return _serialize(doc)


@router.patch("/{visit_id}")
async def update_visit(
    visit_id: str,
    data: VisitUpdate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    db = await get_db()
    update_data = data.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(400, "No fields to update")
    update_data["updated_at"] = datetime.now(timezone.utc)

    result = await db.visits.update_one(
        {"_id": ObjectId(visit_id), "org_id": org_id},
        {"$set": update_data},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Visit not found")

    doc = await db.visits.find_one({"_id": ObjectId(visit_id), "org_id": org_id})
    return _serialize(doc)


@router.get("/stats/doctor/{doctor_id}")
async def doctor_visit_stats(
    doctor_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Get visit statistics for a specific doctor."""
    db = await get_db()
    now = datetime.now(timezone.utc)

    # Total visits
    total = await db.visits.count_documents({
        "org_id": org_id, "target_type": "DOCTOR", "target_id": doctor_id,
    })

    # Visits in last 90 days
    recent = await db.visits.count_documents({
        "org_id": org_id, "target_type": "DOCTOR", "target_id": doctor_id,
        "at": {"$gte": now.replace(hour=0, minute=0, second=0, microsecond=0)},
    })

    # Outcomes breakdown
    pipeline = [
        {"$match": {"org_id": org_id, "target_type": "DOCTOR", "target_id": doctor_id}},
        {"$group": {"_id": "$outcome", "count": {"$sum": 1}}},
    ]
    outcomes = {}
    async for r in db.visits.aggregate(pipeline):
        outcomes[r["_id"]] = r["count"]

    return {"total": total, "recent_90d": recent, "outcomes": outcomes}
