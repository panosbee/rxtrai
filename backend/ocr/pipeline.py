"""
RxTrail — OCR Pipeline: photo → OCR → PII scrub → LLM structuring → confirm payload.

This is the full prescription processing pipeline:
  1. Upload photo → save locally
  2. Queue OCR job (local EasyOCR)
  3. Scrub PII from OCR text
  4. Send scrubbed text to DeepSeek for structuring
  5. Return structured JSON for user confirmation
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.llm.client import structured_call, LLMError
from backend.llm.prompts import PRESCRIPTION_OCR_V1
from backend.llm.schemas import PrescriptionOCRResult
from backend.llm.scrubber import scrub_text_sync
from backend.ocr.engine import submit_ocr_job, get_ocr_job, start_ocr_worker

logger = logging.getLogger("rxtrail.ocr.pipeline")


async def process_prescription_photo(
    image_path: str,
    prescription_id: str,
    db: AsyncIOMotorDatabase,
    org_id: str,
    user_id: str,
) -> dict:
    """Full pipeline: submit OCR → wait → scrub → LLM structure → save to DB.

    Args:
        image_path: Local path to the uploaded photo
        prescription_id: MongoDB _id of the prescription document
        db: MongoDB handle
        org_id: Organization ID
        user_id: User ID

    Returns:
        Dict with status and structured result (or error)
    """
    # ── Step 1: Queue OCR ───────────────────────────────────────────────
    job = await submit_ocr_job(prescription_id, image_path)

    # ── Step 2: Poll until done ─────────────────────────────────────────
    max_wait = 60  # seconds
    waited = 0
    while job.status in ("PENDING", "PROCESSING") and waited < max_wait:
        import asyncio
        await asyncio.sleep(1)
        waited += 1
        job = await get_ocr_job(prescription_id)
        if not job:
            break

    if job.status == "FAILED":
        await db.prescriptions.update_one(
            {"_id": ObjectId(prescription_id)},
            {"$set": {"status": "OCR_FAILED", "ocr_error": job.error, "updated_at": datetime.now(timezone.utc)}},
        )
        return {"status": "OCR_FAILED", "error": job.error}

    if job.status != "DONE" or not job.result_text:
        await db.prescriptions.update_one(
            {"_id": ObjectId(prescription_id)},
            {"$set": {"status": "OCR_TIMEOUT", "updated_at": datetime.now(timezone.utc)}},
        )
        return {"status": "OCR_TIMEOUT", "error": "OCR did not complete within timeout"}

    ocr_text = job.result_text
    ocr_confidence = job.confidence or 0.0

    # ── Step 3: Scrub PII ──────────────────────────────────────────────
    scrubbed = scrub_text_sync(ocr_text)

    # ── Step 4: LLM structuring ─────────────────────────────────────────
    prompt = PRESCRIPTION_OCR_V1.format(ocr_text=scrubbed)

    try:
        llm_result = await structured_call(
            prompt=prompt,
            schema=PrescriptionOCRResult,
            db=db,
            org_id=org_id,
            user_id=user_id,
            temperature=0.05,
        )
    except LLMError as e:
        await db.prescriptions.update_one(
            {"_id": ObjectId(prescription_id)},
            {"$set": {
                "status": "LLM_FAILED",
                "ocr_text": ocr_text,
                "ocr_confidence": ocr_confidence,
                "llm_error": str(e),
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        return {"status": "LLM_FAILED", "error": str(e), "ocr_text": ocr_text}

    # ── Step 5: Save to DB ─────────────────────────────────────────────
    await db.prescriptions.update_one(
        {"_id": ObjectId(prescription_id)},
        {"$set": {
            "status": "OCR_DONE",
            "ocr_text": ocr_text,
            "ocr_confidence": ocr_confidence,
            "lines": llm_result.get("lines", []),
            "doctor_name_raw": llm_result.get("doctor_name_raw", ""),
            "issued_date": llm_result.get("issued_date", ""),
            "llm_result": llm_result,
            "updated_at": datetime.now(timezone.utc),
        }},
    )

    # ── Step 6: Feed the AI knowledge base (private to the org) ─────────
    # Ο Ασκληπιός διαβάζει το (scrubbed) κείμενο της γνωμάτευσης μέσω RAG.
    try:
        from backend.llm.case_knowledge import index_text_as_knowledge
        presc_doc = await db.prescriptions.find_one(
            {"_id": ObjectId(prescription_id)}, {"case_id": 1}
        )
        await index_text_as_knowledge(
            db, org_id, user_id, scrubbed or ocr_text,
            file_id=f"rx-{prescription_id}",
            filename=f"Γνωμάτευση {prescription_id[:8]}",
            tab_name="Γνωμάτευση OCR",
            case_id=str(presc_doc.get("case_id") or "") if presc_doc else "",
            doc_type="PRESCRIPTION",
            tags=["prescription", "ocr"],
        )
    except Exception:
        logger.exception(f"Failed to index prescription {prescription_id} into knowledge")

    return {
        "status": "OCR_DONE",
        "ocr_text": ocr_text,
        "ocr_confidence": ocr_confidence,
        "structured": llm_result,
    }


async def process_ocr_async(
    image_path: str,
    prescription_id: str,
    db: AsyncIOMotorDatabase,
    org_id: str,
    user_id: str,
):
    """Fire-and-forget async OCR processing. Updates DB when done."""
    try:
        result = await process_prescription_photo(
            image_path=image_path,
            prescription_id=prescription_id,
            db=db,
            org_id=org_id,
            user_id=user_id,
        )
        logger.info(f"OCR pipeline complete for {prescription_id}: {result.get('status')}")
    except Exception as e:
        logger.exception(f"OCR pipeline failed for {prescription_id}")
        await db.prescriptions.update_one(
            {"_id": ObjectId(prescription_id)},
            {"$set": {
                "status": "OCR_FAILED",
                "ocr_error": str(e),
                "updated_at": datetime.now(timezone.utc),
            }},
        )
