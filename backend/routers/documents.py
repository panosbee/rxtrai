"""
RxTrail — Documents router: upload, list, preview, delete files per case.

Files are stored in uploads/cases/{case_id}/ directory.
Supports: PDF, images (JPEG, PNG), and common document formats.

Every uploaded document is ALSO ingested into the AI knowledge base (private
to the org): PDF → pypdf text extraction, images → local Tesseract OCR, then
PII scrub → chunk → Jina embeddings → knowledge_chunks. The agent (Ασκληπιός)
can then read case documents via RAG.
"""
import asyncio
import os
import mimetypes
from datetime import datetime, timezone
from uuid import uuid4

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from backend.auth.deps import get_current_user, get_org
from backend.config import settings
from backend.db import get_db

router = APIRouter(prefix="/documents", tags=["documents"])

ALLOWED_MIMES = {
    "application/pdf",
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB


def _serialize(doc: dict) -> dict:
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


@router.get("/by-case/{case_id}")
async def list_documents(
    case_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """List all documents attached to a case."""
    db = await get_db()
    cursor = db.documents.find(
        {"org_id": org_id, "case_id": case_id}
    ).sort("created_at", -1)

    docs = []
    async for d in cursor:
        d["_id"] = str(d["_id"])
        # Generate preview URL
        d["url"] = f"/documents/{d['_id']}/file"
        docs.append(d)

    return {"items": docs, "total": len(docs)}


@router.post("/upload/{case_id}", status_code=201)
async def upload_document(
    case_id: str,
    file: UploadFile = File(...),
    name: str = Form("", description="Όνομα αρχείου (π.χ. Βεβαίωση Ιατρού)"),
    doc_type: str = Form("OTHER", description="DOCUMENT|PHOTO|PRESCRIPTION|CERTIFICATE|OTHER"),
    request: Request = ...,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Upload a document/photo attached to a case."""
    # Validate case exists
    db = await get_db()
    case = await db.cases.find_one({"_id": ObjectId(case_id), "org_id": org_id})
    if not case:
        raise HTTPException(404, "Case not found")

    # Validate mime type
    mime = file.content_type or "application/octet-stream"
    if mime not in ALLOWED_MIMES and not mime.startswith("image/"):
        raise HTTPException(400, f"Μη επιτρεπτός τύπος αρχείου: {mime}")

    # Read file
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(400, "Το αρχείο υπερβαίνει τα 20MB")

    # Generate safe filename
    ext = os.path.splitext(file.filename or ".bin")[1] or ".bin"
    safe_name = f"{uuid4().hex}{ext}"

    # Create case directory
    case_dir = settings.upload_path / "cases" / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    file_path = case_dir / safe_name
    file_path.write_bytes(contents)

    # Default name from original filename if not provided
    doc_name = name.strip() if name.strip() else (file.filename or "Αρχείο")

    now = datetime.now(timezone.utc)
    doc = {
        "org_id": org_id,
        "case_id": case_id,
        "name": doc_name,
        "type": doc_type,
        "file_name": file.filename or safe_name,
        "file_path": str(file_path),
        "file_size": len(contents),
        "mime_type": mime,
        "created_by": str(current_user["_id"]),
        "created_at": now,
        "updated_at": now,
    }
    result = await db.documents.insert_one(doc)
    doc["_id"] = str(result.inserted_id)
    doc["url"] = f"/documents/{doc['_id']}/file"

    # AI: extract text (PDF/OCR) and ingest into the knowledge base (fire-and-forget)
    asyncio.ensure_future(_index_document_background(
        db, org_id, str(current_user["_id"]), doc, case,
    ))

    return _serialize(doc)


@router.get("/{document_id}/file")
async def get_document_file(
    document_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Serve the actual file for preview/download."""
    db = await get_db()
    doc = await db.documents.find_one({"_id": ObjectId(document_id), "org_id": org_id})
    if not doc:
        raise HTTPException(404, "Document not found")

    file_path = doc.get("file_path", "")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(404, "File not found on disk")

    # Inline preview by default; ?download=1 forces attachment download
    download = request.query_params.get("download") == "1"
    return FileResponse(
        file_path,
        media_type=doc.get("mime_type", "application/octet-stream"),
        filename=doc.get("file_name", "file") if download else None,
        content_disposition_type="attachment" if download else "inline",
    )


@router.get("/{document_id}")
async def get_document(
    document_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Get document metadata."""
    db = await get_db()
    doc = await db.documents.find_one({"_id": ObjectId(document_id), "org_id": org_id})
    if not doc:
        raise HTTPException(404, "Document not found")
    doc["_id"] = str(doc["_id"])
    doc["url"] = f"/documents/{doc['_id']}/file"
    return doc


@router.patch("/{document_id}")
async def update_document(
    document_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Update document name/type."""
    body = await request.json()
    db = await get_db()

    updates = {"updated_at": datetime.now(timezone.utc)}
    if "name" in body and body["name"]:
        updates["name"] = body["name"]
    if "type" in body and body["type"]:
        updates["type"] = body["type"]

    if len(updates) <= 1:
        raise HTTPException(400, "No fields to update")

    result = await db.documents.update_one(
        {"_id": ObjectId(document_id), "org_id": org_id},
        {"$set": updates},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Document not found")

    doc = await db.documents.find_one({"_id": ObjectId(document_id), "org_id": org_id})
    doc["_id"] = str(doc["_id"])
    doc["url"] = f"/documents/{doc['_id']}/file"
    return doc


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Delete a document (file + DB record + its knowledge chunks)."""
    db = await get_db()
    doc = await db.documents.find_one({"_id": ObjectId(document_id), "org_id": org_id})
    if not doc:
        raise HTTPException(404, "Document not found")

    # Delete physical file
    file_path = doc.get("file_path", "")
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError:
            pass

    await db.documents.delete_one({"_id": ObjectId(document_id), "org_id": org_id})

    # Remove its knowledge chunks so the AI forgets it too
    try:
        from backend.llm.case_knowledge import remove_case_knowledge
        await remove_case_knowledge(db, org_id, f"doc-{document_id}")
    except Exception:
        pass


# ── AI ingestion helpers ──────────────────────────────────────────────────────

async def _extract_document_text(doc: dict) -> str:
    """Extract raw text from a stored document (PDF via pypdf, images via OCR)."""
    path = doc.get("file_path", "")
    if not path or not os.path.exists(path):
        return ""
    mime = doc.get("mime_type", "") or ""

    if mime == "application/pdf":
        def _pdf_text() -> str:
            try:
                from pypdf import PdfReader
                reader = PdfReader(path)
                text = "\n".join((page.extract_text() or "") for page in reader.pages)
                if len(text.strip()) >= 40:
                    return text
            except Exception:
                pass
            # Scanned/image-only PDF → OCR its pages
            return _ocr_scanned_pdf(path)
        return await asyncio.get_event_loop().run_in_executor(None, _pdf_text)

    if mime.startswith("image/"):
        try:
            from backend.ocr.engine import run_ocr
            return await run_ocr(path)
        except Exception:
            return ""

    # Word/other formats: no text extraction for now
    return ""


def _ocr_scanned_pdf(path: str, max_pages: int = 6) -> str:
    """OCR an image-only PDF: pdftoppm renders pages to PNG, tesseract reads them."""
    import shutil
    import subprocess
    import tempfile

    if not shutil.which("pdftoppm"):
        return ""
    try:
        from backend.ocr.engine import _find_tesseract
        tesseract_cmd = _find_tesseract()
    except Exception:
        return ""

    try:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = os.path.join(tmp, "page")
            subprocess.run(
                ["pdftoppm", "-png", "-r", "200", "-f", "1", "-l", str(max_pages), path, prefix],
                capture_output=True, timeout=120, check=True,
            )
            parts = []
            for f in sorted(os.listdir(tmp)):
                if not f.endswith(".png"):
                    continue
                out = subprocess.run(
                    [tesseract_cmd, os.path.join(tmp, f), "stdout", "-l", "ell+eng", "--psm", "6"],
                    capture_output=True, text=True, timeout=120,
                )
                if out.returncode == 0 and out.stdout.strip():
                    parts.append(out.stdout.strip())
            return "\n\n".join(parts)
    except Exception:
        return ""


async def _index_document_background(db, org_id: str, user_id: str, doc: dict, case: dict):
    """Background task: extract text → index into the private knowledge base."""
    try:
        text = await _extract_document_text(doc)
        status = "no_text"
        chunks = 0
        if text and len(text.strip()) > 20:
            from backend.llm.case_knowledge import index_text_as_knowledge
            result = await index_text_as_knowledge(
                db, org_id, user_id, text,
                file_id=f"doc-{doc['_id']}",
                filename=doc.get("name") or "Αρχείο",
                tab_name=doc.get("name") or "Αρχείο",
                case_id=str(doc.get("case_id", "")),
                doc_type=doc.get("type", "DOCUMENT"),
                tags=["case_document", (doc.get("type") or "DOCUMENT").lower()],
            )
            status = "indexed" if result.get("status") == "ok" else "failed"
            chunks = result.get("chunks", 0)

        await db.documents.update_one(
            {"_id": ObjectId(doc["_id"])},
            {"$set": {
                "extracted_text": (text or "")[:20000],
                "knowledge_status": status,
                "knowledge_chunks": chunks,
                "updated_at": datetime.now(timezone.utc),
            }},
        )
    except Exception:
        try:
            await db.documents.update_one(
                {"_id": ObjectId(doc["_id"])},
                {"$set": {
                    "knowledge_status": "failed",
                    "updated_at": datetime.now(timezone.utc),
                }},
            )
        except Exception:
            pass
