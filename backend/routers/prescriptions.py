"""
RxTrail — Prescriptions router: CRUD + photo upload (OCR pipeline stub).
"""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile

from backend.auth.deps import get_current_user, get_org
from backend.config import settings
from backend.db import get_db
from backend.models.prescription import PrescriptionCreate, PrescriptionUpdate

router = APIRouter(prefix="/prescriptions", tags=["prescriptions"])


def _serialize(doc: dict) -> dict:
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


@router.get("")
async def list_prescriptions(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
    case_id: str = Query(""),
    doctor_id: str = Query(""),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    db = await get_db()
    filt: dict = {"org_id": org_id}
    if case_id:
        filt["case_id"] = case_id
    if doctor_id:
        filt["doctor_id"] = ObjectId(doctor_id)

    cursor = db.prescriptions.find(filt).sort("issued_at", -1).skip(skip).limit(limit)
    items = [_serialize(d) async for d in cursor]
    total = await db.prescriptions.count_documents(filt)
    return {"items": items, "total": total}


@router.get("/{prescription_id}")
async def get_prescription(
    prescription_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    db = await get_db()
    doc = await db.prescriptions.find_one({"_id": ObjectId(prescription_id), "org_id": org_id})
    if not doc:
        raise HTTPException(404, "Prescription not found")
    return _serialize(doc)


@router.post("", status_code=201)
async def create_prescription(
    data: PrescriptionCreate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Create a prescription manually (without photo/OCR)."""
    now = datetime.now(timezone.utc)
    db = await get_db()

    doc = {
        **data.model_dump(),
        "org_id": org_id,
        "issued_at": data.issued_at or now,
        "created_by": str(current_user["_id"]),
        "created_at": now,
        "updated_at": now,
        "confirmed_by_user": True,
    }
    result = await db.prescriptions.insert_one(doc)
    doc["_id"] = str(result.inserted_id)
    return _serialize(doc)


@router.post("/from-photo")
async def upload_prescription_photo(
    file: UploadFile = File(...),
    case_id: str = Query(""),
    request: Request = ...,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Upload a prescription photo. OCR pipeline (M5) — v1 saves file, returns job_id."""
    # Validate file type
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "Only image files accepted")

    contents = await file.read()
    if len(contents) > 15 * 1024 * 1024:
        raise HTTPException(400, "File exceeds 15MB")

    # Save to uploads directory
    upload_dir = settings.upload_path / "rx_photos"
    upload_dir.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(file.filename or ".jpg")[1] or ".jpg"
    filename = f"{uuid4().hex}{ext}"
    file_path = upload_dir / filename
    file_path.write_bytes(contents)

    now = datetime.now(timezone.utc)
    db = await get_db()

    # Create a pending prescription record
    doc = {
        "org_id": org_id,
        "case_id": case_id if case_id else None,
        "status": "OCR_PENDING",
        "photo_path": str(file_path),
        "photo_delete_at": now + timedelta(days=settings.photo_retention_days),
        "created_by": str(current_user["_id"]),
        "created_at": now,
        "updated_at": now,
    }
    result = await db.prescriptions.insert_one(doc)

    # ── Start async OCR processing (fire-and-forget) ───────────────────
    import asyncio
    from backend.ocr.pipeline import process_ocr_async
    asyncio.ensure_future(process_ocr_async(
        image_path=str(file_path),
        prescription_id=str(result.inserted_id),
        db=db,
        org_id=org_id,
        user_id=str(current_user["_id"]),
    ))

    return {
        "prescription_id": str(result.inserted_id),
        "status": "OCR_PENDING",
        "message": "Photo saved. OCR processing started.",
    }


@router.patch("/{prescription_id}")
async def update_prescription(
    prescription_id: str,
    data: PrescriptionUpdate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Link a prescription to a case / doctor (used by the prescribe form)."""
    if not ObjectId.is_valid(prescription_id):
        raise HTTPException(404, "Prescription not found")
    db = await get_db()
    doc = await db.prescriptions.find_one({"_id": ObjectId(prescription_id), "org_id": org_id})
    if not doc:
        raise HTTPException(404, "Prescription not found")

    updates = {k: v for k, v in data.model_dump(exclude_unset=True).items()}
    if not updates:
        return _serialize(doc)

    if "doctor_id" in updates and updates["doctor_id"] and ObjectId.is_valid(updates["doctor_id"]):
        updates["doctor_id"] = ObjectId(updates["doctor_id"])
    updates["updated_at"] = datetime.now(timezone.utc)
    await db.prescriptions.update_one(
        {"_id": ObjectId(prescription_id), "org_id": org_id},
        {"$set": updates},
    )
    doc = await db.prescriptions.find_one({"_id": ObjectId(prescription_id), "org_id": org_id})
    return _serialize(doc)


@router.get("/ocr-status/{job_id}")
async def ocr_status(
    job_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Poll OCR job status or get structured result."""
    db = await get_db()

    # Check if prescription has been processed
    presc = await db.prescriptions.find_one({"_id": ObjectId(job_id), "org_id": org_id})
    if not presc:
        from backend.ocr.engine import get_ocr_job
        job = await get_ocr_job(job_id)
        if job:
            return job.to_dict()
        raise HTTPException(404, "Job not found")

    status = presc.get("status", "PENDING")
    result = {
        "job_id": job_id,
        "status": status,
    }

    if status == "OCR_DONE":
        result.update({
            "ocr_text": presc.get("ocr_text", ""),
            "ocr_confidence": presc.get("ocr_confidence"),
            "lines": presc.get("lines", []),
            "doctor_name_raw": presc.get("doctor_name_raw", ""),
            "issued_date": presc.get("issued_date", ""),
        })
    elif status in ("OCR_FAILED", "LLM_FAILED", "OCR_TIMEOUT"):
        result["error"] = presc.get("ocr_error") or presc.get("llm_error", "Unknown error")

    return result


@router.post("/{prescription_id}/create-case")
async def create_case_from_prescription(
    prescription_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Auto-create a case from an OCR'd prescription."""
    db = await get_db()
    if not ObjectId.is_valid(prescription_id):
        raise HTTPException(404, "Prescription not found")
    presc = await db.prescriptions.find_one({"_id": ObjectId(prescription_id), "org_id": org_id})
    if not presc:
        raise HTTPException(404, "Prescription not found")
    if presc.get("status") != "OCR_DONE":
        raise HTTPException(400, "OCR not finished yet")

    now = datetime.now(timezone.utc)

    # Find doctor by name (fuzzy via LLM-free partial match)
    doctor_id = None
    doctor_name = (presc.get("doctor_name_raw") or "").strip()
    if doctor_name:
        parts = [p for p in doctor_name.split() if len(p) >= 3]
        or_clauses = [{"name": {"$regex": p, "$options": "i"}} for p in parts]
        d = await db.doctors.find_one({"org_id": org_id, "$or": or_clauses})
        if d:
            doctor_id = str(d["_id"])

    # Determine area from doctor or leave empty
    area = ""
    if doctor_id:
        d = await db.doctors.find_one({"_id": ObjectId(doctor_id)})
        if d: area = d.get("area", "")

    # Build need categories from SKU lines if they match catalog
    need_categories = []
    for line in (presc.get("lines") or []):
        code = (line.get("eopyy_code_raw") or "").strip()
        if code:
            sku = await db.skus.find_one({"org_id": org_id, "eopyy_code": code})
            if sku and sku.get("category") and sku["category"] not in need_categories:
                need_categories.append(sku["category"])

    alias = f"Γνωμάτευση {presc.get('issued_date','') or '—'} {doctor_name}"[:120]

    case_doc = {
        "org_id": org_id,
        "alias": alias,
        "status": "NEW",
        "area": area,
        "need_categories": need_categories,
        "needs_freehand": f"Αυτόματη δημιουργία από φωτογραφία γνωμάτευσης. Γιατρός: {doctor_name}",
        "flags": [],
        "created_by": str(current_user["_id"]),
        "created_at": now,
        "updated_at": now,
        "status_history": [{"from_status": None, "to": "NEW", "at": now, "by": str(current_user["_id"])}],
    }
    result = await db.cases.insert_one(case_doc)
    case_id = str(result.inserted_id)

    # Link prescription to case + doctor
    await db.prescriptions.update_one(
        {"_id": ObjectId(prescription_id)},
        {"$set": {"case_id": case_id, "doctor_id": ObjectId(doctor_id) if doctor_id else None, "updated_at": now}},
    )

    # If doctor found, set case doctor_id too
    if doctor_id:
        await db.cases.update_one({"_id": result.inserted_id}, {"$set": {"doctor_id": doctor_id}})

    return {
        "ok": True,
        "case_id": case_id,
        "alias": alias,
        "doctor_id": doctor_id,
        "doctor_name": doctor_name,
        "need_categories": need_categories,
        "message": f"Περιστατικό «{alias}» δημιουργήθηκε.",
    }
