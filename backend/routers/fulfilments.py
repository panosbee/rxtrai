"""
RxTrail — Fulfilments router: CRUD (ground truth revenue events).
"""
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.auth.deps import get_current_user, get_org
from backend.db import get_db
from backend.models.fulfilment import FulfilmentCreate

router = APIRouter(prefix="/fulfilments", tags=["fulfilments"])


def _serialize(doc: dict) -> dict:
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


@router.get("")
async def list_fulfilments(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
    case_id: str = Query(""),
    prescription_id: str = Query(""),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    db = await get_db()
    filt: dict = {"org_id": org_id}
    if case_id:
        filt["case_id"] = case_id
    if prescription_id:
        filt["prescription_id"] = prescription_id

    cursor = db.fulfilments.find(filt).sort("fulfilled_at", -1).skip(skip).limit(limit)
    items = [_serialize(d) async for d in cursor]
    total = await db.fulfilments.count_documents(filt)
    return {"items": items, "total": total}


@router.get("/{fulfilment_id}")
async def get_fulfilment(
    fulfilment_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    db = await get_db()
    doc = await db.fulfilments.find_one({"_id": ObjectId(fulfilment_id), "org_id": org_id})
    if not doc:
        raise HTTPException(404, "Fulfilment not found")
    return _serialize(doc)


@router.delete("/{fulfilment_id}")
async def delete_fulfilment(
    fulfilment_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Delete a fulfilment (used for rollback when a transition fails)."""
    if not ObjectId.is_valid(fulfilment_id):
        raise HTTPException(404, "Fulfilment not found")
    db = await get_db()
    result = await db.fulfilments.delete_one({"_id": ObjectId(fulfilment_id), "org_id": org_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Fulfilment not found")
    return {"ok": True}


@router.post("", status_code=201)
async def create_fulfilment(
    data: FulfilmentCreate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Create a fulfilment. Use the state machine's 'fulfil' transition to link to a case."""
    now = datetime.now(timezone.utc)
    db = await get_db()

    # Determine cycle number
    if data.case_id:
        last = await db.fulfilments.find_one(
            {"case_id": data.case_id, "org_id": org_id},
            sort=[("cycle_number", -1)],
        )
        cycle_number = (last["cycle_number"] + 1) if last else 1
    else:
        cycle_number = 1

    doc = {
        **data.model_dump(),
        "org_id": org_id,
        "cycle_number": cycle_number,
        "fulfilled_at": data.fulfilled_at or now,
        "created_by": str(current_user["_id"]),
        "created_at": now,
        "updated_at": now,
    }
    result = await db.fulfilments.insert_one(doc)
    doc["_id"] = str(result.inserted_id)
    return _serialize(doc)
