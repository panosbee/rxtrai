"""
RxTrail — Patients router: CRUD (ΜΟΝΟ για ενεργούς πελάτες).
"""
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.auth.deps import get_current_user, get_org
from backend.db import get_db, scoped
from backend.models.patient import PatientCreate, PatientUpdate

router = APIRouter(prefix="/patients", tags=["patients"])


def _serialize(doc: dict) -> dict:
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


@router.get("")
async def list_patients(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: str = Query(""),
):
    db = await get_db()
    filt: dict = {"org_id": org_id, "erased": False}
    if search:
        filt["name"] = {"$regex": search, "$options": "i"}

    cursor = db.patients.find(filt).sort("created_at", -1).skip(skip).limit(limit)
    items = [_serialize(d) async for d in cursor]
    total = await db.patients.count_documents(filt)
    return {"items": items, "total": total}


@router.get("/{patient_id}")
async def get_patient(
    patient_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    db = await get_db()
    pat = await db.patients.find_one({"_id": ObjectId(patient_id), "org_id": org_id})
    if not pat:
        raise HTTPException(404, "Patient not found")
    return _serialize(pat)


@router.patch("/{patient_id}")
async def update_patient(
    patient_id: str,
    data: PatientUpdate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    db = await get_db()
    update_data = data.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(400, "No fields to update")
    update_data["updated_at"] = datetime.now(timezone.utc)

    result = await db.patients.update_one(
        {"_id": ObjectId(patient_id), "org_id": org_id},
        {"$set": update_data},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Patient not found")

    pat = await db.patients.find_one({"_id": ObjectId(patient_id), "org_id": org_id})
    return _serialize(pat)


@router.delete("/{patient_id}", status_code=204)
async def erase_patient(
    patient_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Right-to-erasure: soft-delete with anonymization."""
    db = await get_db()
    result = await db.patients.update_one(
        {"_id": ObjectId(patient_id), "org_id": org_id},
        {"$set": {
            "erased": True,
            "name": "[ERASED]",
            "phone": "[ERASED]",
            "address": "[ERASED]",
            "caregiver_name": "[ERASED]",
            "caregiver_phone": "[ERASED]",
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Patient not found")
