"""
RxTrail — Imports router: Excel upload → column mapping (LLM v2) →
preview → commit with diff report.
"""
import asyncio
import io
import json
import os
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

import httpx
from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from openpyxl import load_workbook
from pydantic import BaseModel, Field

from backend.auth.deps import get_current_user, get_org
from backend.config import settings
from backend.db import get_db

router = APIRouter(prefix="/imports", tags=["imports"])

MAX_UPLOAD_MB = 15
ALLOWED_EXTENSIONS = {".xlsx"}


class MappingRequest(BaseModel):
    """Column mapping submitted by user after reviewing preview."""
    mapping: dict[str, str] = Field(..., description="Excel header → field name")
    import_job_id: str


class CommitRequest(BaseModel):
    import_job_id: str


@router.post("/analyze")
async def analyze_upload(
    file: UploadFile = File(...),
    kind: str = Query("SKU", description="SKU|STRUCTURES|DOCTORS|CONTACTS"),
    sheet_name: str = Query("", description="Sheet name (if empty, uses active sheet)"),
    request: Request = ...,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Upload Excel → extract headers + first 5 rows → create import_job in MAPPING state."""
    # Validate file type
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only .xlsx files are accepted")

    # Read and validate size
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"File exceeds {MAX_UPLOAD_MB}MB")

    # Parse with openpyxl — first pass to get sheet names
    wb = load_workbook(filename=io.BytesIO(contents), read_only=True, data_only=True)
    sheet_names = wb.sheetnames

    # Select sheet
    if sheet_name and sheet_name in sheet_names:
        ws = wb[sheet_name]
    else:
        ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if not rows:
        raise HTTPException(status_code=400, detail="Empty file")

    headers = [str(h) if h is not None else f"Col_{i}" for i, h in enumerate(rows[0])]
    sample_rows = []
    all_rows = []  # Store ALL data rows for commit
    for row in rows[1:6]:  # First 5 data rows
        sample_rows.append([str(c) if c is not None else "" for c in row])
    for row in rows[1:]:  # ALL data rows
        all_rows.append([str(c) if c is not None else "" for c in row])

    # Detected field suggestions based on kind (with sample data for LLM context)
    suggested_mapping = _suggest_mapping(headers, kind, sample_rows[0] if sample_rows else [])

    # Create import job — now includes all rows for commit
    db = await get_db()
    job = {
        "org_id": org_id,
        "kind": kind,
        "filename": file.filename,
        "status": "MAPPING",
        "headers": headers,
        "sample_rows": sample_rows,
        "all_rows": all_rows,
        "total_rows": len(rows) - 1,
        "suggested_mapping": suggested_mapping,
        "column_mapping": {},
        "created_by": str(current_user["_id"]),
        "created_at": datetime.now(timezone.utc),
    }
    result = await db.import_jobs.insert_one(job)
    job["_id"] = str(result.inserted_id)

    return {
        "import_job_id": str(result.inserted_id),
        "headers": headers,
        "sample_rows": sample_rows,
        "total_rows": len(rows) - 1,
        "suggested_mapping": suggested_mapping,
        "sheet_names": sheet_names,
        "selected_sheet": ws.title,
    }


@router.post("/preview")
async def preview_import(
    data: MappingRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Apply column mapping → preview transformed data → move job to PREVIEW."""
    db = await get_db()
    job = await db.import_jobs.find_one({"_id": ObjectId(data.import_job_id), "org_id": org_id})
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")

    # Store mapping
    await db.import_jobs.update_one(
        {"_id": ObjectId(data.import_job_id)},
        {"$set": {"column_mapping": data.mapping, "status": "PREVIEW", "updated_at": datetime.now(timezone.utc)}},
    )

    # Re-read the file to apply mapping (in production, store rows on job creation)
    # For v1: return the mapping for the UI to display preview
    return {
        "import_job_id": data.import_job_id,
        "mapping": data.mapping,
        "status": "PREVIEW",
    }


@router.post("/commit")
async def commit_import(
    data: CommitRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Commit import: apply mapping, insert rows into target collection."""
    db = await get_db()
    job = await db.import_jobs.find_one({"_id": ObjectId(data.import_job_id), "org_id": org_id})
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")
    if job["status"] not in ("PREVIEW", "MAPPING"):
        raise HTTPException(status_code=400, detail=f"Job is in {job['status']} state, cannot commit")

    mapping = job.get("column_mapping", {})
    headers = job.get("headers", [])
    all_rows = job.get("all_rows", [])
    kind = job.get("kind", "SKU")

    if not mapping or not all_rows:
        raise HTTPException(status_code=400, detail="No mapping or rows to import")

    # Resolve target collection
    collection_map = {
        "SKU": db.skus,
        "STRUCTURES": db.structures,
        "DOCTORS": db.doctors,
        "CONTACTS": db.contacts,
    }
    target = collection_map.get(kind)
    if target is None:
        raise HTTPException(status_code=400, detail=f"Unknown kind: {kind}")

    # Build header index: header_name → column_index
    header_idx = {h: i for i, h in enumerate(headers)}

    now = datetime.now(timezone.utc)
    inserted = 0
    updated = 0
    skipped = 0
    errors = []

    for row_idx, row in enumerate(all_rows):
        try:
            # Apply mapping: field_name → cell value
            # FIRST non-empty value wins (multiple headers can map to same field)
            doc = {"org_id": org_id, "created_at": now, "updated_at": now}

            # Set active=True for entities that have active filtering
            if kind == "SKU":
                doc["active"] = True

            for header, field in mapping.items():
                # Skip if this field already has a value (first non-empty wins)
                if field in doc:
                    continue
                col_idx = header_idx.get(header)
                if col_idx is not None and col_idx < len(row):
                    val = row[col_idx].strip()
                    if val:
                        # Type coercion
                        if field == "reimb_price":
                            try: doc[field] = float(val.replace(",", "."))
                            except ValueError: doc[field] = 0.0
                        elif field == "patient_copay_pct":
                            try: doc[field] = float(val.replace(",", "."))
                            except ValueError: doc[field] = 0.0
                        elif field in ("max_qty_month", "duration_days_default"):
                            try: doc[field] = int(float(val.replace(",", ".")))
                            except ValueError: doc[field] = 0
                        else:
                            doc[field] = val

            # For doctors: add default fields
            if kind == "DOCTORS":
                doc.setdefault("phone", "")
                doc.setdefault("notes", "")
                doc.setdefault("structure_ids", [])
                doc.setdefault("rx_count_90d", 0)
                doc.setdefault("avg_pickup_days", None)
                doc.setdefault("open_cases_count", 0)
                doc.setdefault("specialty", "OTHER")

            # For contacts: add default
            if kind == "CONTACTS":
                doc.setdefault("phone", "")

            # Check if we should update by unique key
            # Priority: product_id > eopyy_code > commercial_name (for SKU), name (for others)
            existing = None
            if kind == "SKU":
                if "product_id" in doc:
                    existing = await target.find_one({"org_id": org_id, "product_id": doc["product_id"]})
                if not existing and "eopyy_code" in doc:
                    existing = await target.find_one({"org_id": org_id, "eopyy_code": doc["eopyy_code"]})
                # Fallback: match by commercial_name when no codes available
                if not existing and "commercial_name" in doc:
                    existing = await target.find_one({"org_id": org_id, "commercial_name": doc["commercial_name"]})
            elif kind == "STRUCTURES" and "name" in doc:
                existing = await target.find_one({"org_id": org_id, "name": doc["name"]})
            elif kind == "DOCTORS" and "name" in doc:
                existing = await target.find_one({"org_id": org_id, "name": doc["name"]})

            if existing:
                # Update
                doc.pop("created_at", None)
                doc["updated_at"] = now
                await target.update_one({"_id": existing["_id"]}, {"$set": doc})
                updated += 1
            else:
                # Insert
                await target.insert_one(doc)
                inserted += 1

        except Exception as e:
            errors.append({"row": row_idx + 1, "error": str(e)})

    # Update job
    report = {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }
    await db.import_jobs.update_one(
        {"_id": ObjectId(data.import_job_id)},
        {"$set": {
            "status": "DONE",
            "report": report,
            "updated_at": now,
        }},
    )

    return {
        "import_job_id": data.import_job_id,
        "status": "DONE",
        "report": report,
    }


@router.get("")
async def list_imports(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
    kind: str = Query(""),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    db = await get_db()
    filt: dict = {"org_id": org_id}
    if kind:
        filt["kind"] = kind

    cursor = db.import_jobs.find(filt).sort("created_at", -1).skip(skip).limit(limit)
    jobs = []
    async for j in cursor:
        j["_id"] = str(j["_id"])
        jobs.append(j)
    total = await db.import_jobs.count_documents(filt)
    return {"items": jobs, "total": total}


@router.get("/{job_id}")
async def get_import(job_id: str, request: Request, current_user: dict = Depends(get_current_user), org_id: str = Depends(get_org)):
    db = await get_db()
    job = await db.import_jobs.find_one({"_id": ObjectId(job_id), "org_id": org_id})
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")
    job["_id"] = str(job["_id"])
    return job


# ── Helpers ──────────────────────────────────────────────────────────────────

def _suggest_mapping(headers: list[str], kind: str, sample_row: list[str] = None) -> dict:
    """Header→field suggestion using LLM (DeepSeek) for intelligent mapping.
    Merges LLM results with keyword fallback — LLM takes priority."""
    
    # Always get keyword suggestions as base
    keyword_map = _keyword_suggest_mapping(headers, kind)
    
    # Try LLM for smarter mapping (overrides keyword matches)
    try:
        llm_map = _llm_suggest_mapping(headers, kind, sample_row)
        if llm_map:
            # LLM wins where it has suggestions, keyword fills gaps
            merged = dict(keyword_map)
            merged.update(llm_map)
            return merged
    except Exception:
        pass
    
    return keyword_map


def _llm_suggest_mapping(headers: list[str], kind: str, sample_row: list[str] = None) -> dict:
    """Use DeepSeek to intelligently map Excel headers to database fields."""
    
    if not settings.deepseek_api_key:
        return {}
    
    field_descriptions = {
        "SKU": {
            "product_id": "Κωδικός προϊόντος (SKU/Product ID) - μοναδικό αναγνωριστικό",
            "eopyy_code": "Κωδικός ΕΟΠΥΥ - αριθμός αποζημίωσης από τον ΕΟΠΥΥ",
            "commercial_name": "Εμπορική ονομασία προϊόντος",
            "description": "Περιγραφή προϊόντος",
            "category": "Κατηγορία προϊόντος",
            "icd10_code": "Κωδικός ICD-10 της νόσου",
            "reimb_price": "Τιμή αποζημίωσης σε €",
            "patient_copay_pct": "Ποσοστό συμμετοχής ασθενούς (%)",
            "max_qty_month": "Μέγιστη ποσότητα ανά μήνα",
            "duration_days_default": "Προεπιλεγμένη διάρκεια σε ημέρες",
        },
        "STRUCTURES": {
            "name": "Όνομα δομής/νοσοκομείου",
            "type": "Τύπος (HOSPITAL, CLINIC, HEALTH_CENTER, NURSING_HOME, OTHER)",
            "city": "Πόλη",
            "prefecture": "Νομός",
            "address": "Διεύθυνση",
            "phone": "Τηλέφωνο",
        },
        "DOCTORS": {
            "name": "Ονοματεπώνυμο γιατρού",
            "specialty": "Ειδικότητα",
            "phone": "Τηλέφωνο",
            "notes": "Σημειώσεις",
        },
        "CONTACTS": {
            "name": "Όνομα επαφής",
            "role_title": "Ρόλος/θέση",
            "phone": "Τηλέφωνο",
            "structure_id": "Όνομα δομής που ανήκει",
        },
    }
    
    fields = field_descriptions.get(kind, {})
    if not fields:
        return {}
    
    field_list = "\n".join([f"- {k}: {v}" for k, v in fields.items()])
    headers_list = "\n".join([f'- "{h}"' for h in headers])
    
    # Add sample row for context
    sample_text = ""
    if sample_row:
        pairs = [f'  "{h}": "{sample_row[i] if i < len(sample_row) else ""}"' for i, h in enumerate(headers)]
        sample_text = "\nΔΕΙΓΜΑ ΠΡΩΤΗΣ ΓΡΑΜΜΗΣ:\n" + "\n".join(pairs[:15])  # Limit to 15 cols
    
    prompt = f"""Αντιστοίχισε τις στήλες του Excel στα πεδία της βάσης δεδομένων.

ΤΥΠΟΣ: {kind}

ΔΙΑΘΕΣΙΜΑ ΠΕΔΙΑ:
{field_list}

ΕΠΙΚΕΦΑΛΙΔΕΣ EXCEL:
{headers_list}
{sample_text}

ΚΑΝΟΝΕΣ ΑΝΤΙΣΤΟΙΧΙΣΗΣ (ΤΗΡΗΣΕ ΤΟΥΣ ΑΥΣΤΗΡΑ):
1. "Προϊόν", "Περιγραφή", "Εμπορική ονομασία", "Brand" → commercial_name
2. "Κωδικός (ID)", "Product ID", "SKU", "Κωδικός προϊόντος" → product_id (εσωτερικός κωδικός)
3. "ΚΩΔ. ΕΟΠΥΥ", "Κωδικός ΕΟΠΥΥ", "EOPYY Code" → eopyy_code (κωδικός αποζημίωσης ΕΟΠΥΥ)
4. "Κατηγορία" → category
5. "Τιμή", "Τιμή Αποζημ.", "Λιανική" → reimb_price (τιμή σε €)
6. "ICD-10", "ICD10", "Διάγνωση" → icd10_code
7. "Ποσότητα", "Τεμάχια", "Τεμ./Συσκ." → max_qty_month
8. Στήλες "URL", "Πηγή", "Αποζημ." (Ναι/Όχι), "Col_*" → ΑΓΝΟΗΣΕ
9. ΜΗΝ αντιστοιχίσεις τον τίτλο του φύλλου — μόνο τις πραγματικές στήλες

ΠΑΡΑΔΕΙΓΜΑ ΣΩΣΤΗΣ ΑΝΤΙΣΤΟΙΧΙΣΗΣ:
{{"Προϊόν": "commercial_name", "Κωδικός (ID)": "product_id", "ΚΩΔ. ΕΟΠΥΥ": "eopyy_code", "Κατηγορία": "category", "Τιμή Αποζημ. (€)": "reimb_price", "ICD-10 (ενδεικτ.)": "icd10_code"}}

Απάντησε ΜΟΝΟ με JSON: {{"ακριβής επικεφαλίδα": "πεδίο"}}"""

    try:
        resp = httpx.post(
            f"{settings.deepseek_base_url}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.deepseek_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.deepseek_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 500,
                "response_format": {"type": "json_object"},
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception:
        return {}


def _keyword_suggest_mapping(headers: list[str], kind: str) -> dict:
    """Fallback: keyword-based header→field suggestion."""
    suggestions: dict[str, str] = {}
    lower_map = {h.lower(): h for h in headers}

    if kind == "SKU":
        patterns = {
            # eopyy_code — MUST come first to match "ΚΩΔ. ΕΟΠΥΥ" before generic "κωδ" patterns
            "εοπυυ": "eopyy_code",
            # product_id — the product's own identifier
            "product id": "product_id", "product": "product_id", "sku": "product_id",
            "κωδικοσ προϊοντοσ": "product_id", "κωδ. προϊόντος": "product_id",
            # commercial_name — product name
            "προϊόν": "commercial_name", "προϊ": "commercial_name",
            "εμπορική ονομασία": "commercial_name", "εμπορ": "commercial_name",
            "ονομασία": "commercial_name", "brand": "commercial_name",
            # icd10_code
            "icd-10": "icd10_code", "icd10": "icd10_code", "icd": "icd10_code",
            "νόσο": "icd10_code", "νοσο": "icd10_code", "διάγνωσ": "icd10_code",
            # category
            "κατηγορ": "category",
            # price
            "τιμή": "reimb_price", "τιμ": "reimb_price", "αποζημ": "reimb_price", "λιαν": "reimb_price",
            # description
            "περιγραφ": "description",
            # copay
            "συμμετ": "patient_copay_pct", "copay": "patient_copay_pct",
            # quantity
            "ποσοτ": "max_qty_month", "τεμαχ": "max_qty_month", "max_qty": "max_qty_month",
            # duration
            "διάρκ": "duration_days_default", "ημερ": "duration_days_default",
        }
    elif kind == "STRUCTURES":
        patterns = {
            "ονομα": "name", "επωνυμ": "name",
            "τύπ": "type", "τυπο": "type", "κατηγορ": "type",
            "πολ": "city", "περιοχ": "prefecture", "νομ": "prefecture",
            "διεύθ": "address", "τηλ": "phone",
        }
    elif kind == "DOCTORS":
        patterns = {
            "ονομα": "name", "γιατρ": "name",
            "ειδικ": "specialty", "τηλ": "phone",
            "δομ": "structure_ids", "νοσοκ": "structure_ids",
        }
    elif kind == "CONTACTS":
        patterns = {
            "ονομα": "name", "ρόλ": "role_title", "θεσ": "role_title",
            "τηλ": "phone", "δομ": "structure_id",
        }
    else:
        patterns = {}

    for h_lower, original in lower_map.items():
        for keyword, field in patterns.items():
            if keyword in h_lower:
                suggestions[original] = field
                break

    return suggestions
