"""
RxTrail — Shared Space (Κοινός Χώρος): cross-tenant file sharing with folders & versioning.

Every authenticated user can browse/upload. Rename/delete: only creator or owner-role user.
Versioning: same filename in same folder → new version (not duplicate).
Optional: add file to Knowledge (RAG, is_shared=True).
Deliberately NO org_id — this is the shared area, separate from CRM data.
"""
import os, uuid, re
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from backend.auth.deps import get_current_user
from backend.config import settings
from backend.db import get_db

router = APIRouter(prefix="/share", tags=["share"])

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".doc", ".docx",
               ".xls", ".xlsx", ".ppt", ".pptx", ".md", ".json", ".txt", ".csv"}

# Root admins — can rename/delete ANY folder/file. Add emails here.
ADMIN_EMAILS = {"skouras@infosphereco.com"}


def _serialize_folder(f):
    return {
        "_id": str(f["_id"]), "name": f.get("name", ""), "parent_id": f.get("parent_id"),
        "created_by": str(f.get("created_by", "")),
        "created_at": f.get("created_at"),
    }


def _serialize_file(f):
    versions = f.get("versions", [])
    current = versions[-1] if versions else {}
    return {
        "_id": str(f["_id"]), "folder_id": f.get("folder_id"), "name": f.get("name"),
        "size": current.get("size", 0), "mime": current.get("mime", ""),
        "file_path": current.get("file_path", ""),
        "uploaded_by": str(current.get("uploaded_by", "")),
        "uploaded_by_name": current.get("uploaded_by_name", ""),
        "version_count": len(versions),
        "versions": [{"size": v.get("size", 0), "mime": v.get("mime", ""),
                      "uploaded_by_name": v.get("uploaded_by_name", ""),
                      "created_at": v.get("created_at")} for v in versions],
        "knowledge_added": f.get("knowledge_added", False),
        "created_at": f.get("created_at"),
    }


def _is_admin(user: dict) -> bool:
    """Admin = email in ADMIN_EMAILS (role owner alone is NOT admin — every user is an owner)."""
    return (user.get("email") or "").lower() in {e.lower() for e in ADMIN_EMAILS}


async def _can_modify(db, user: dict, doc: dict) -> bool:
    """Rename/delete allowed for creator, last uploader, or root admin."""
    if _is_admin(user):
        return True
    uploader = doc.get("uploaded_by") or doc.get("created_by")
    return str(uploader) == str(user["_id"])


# ── Folders ──────────────────────────────────────────────────────────────

@router.get("/folders")
async def list_folders(
    parent_id: str = Query("", description="Parent folder id (empty = root)"),
    current_user: dict = Depends(get_current_user),
):
    db = await get_db()
    filt = {}
    if parent_id:
        filt["parent_id"] = parent_id
    else:
        filt["parent_id"] = None
    cursor = db.share_folders.find(filt).sort("name", 1)
    return {"items": [_serialize_folder(f) async for f in cursor]}


@router.post("/folders", status_code=201)
async def create_folder(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = await get_db()
    body = await request.json()
    name = (body.get("name") or "").strip()
    parent_id = body.get("parent_id") or None
    if not name:
        raise HTTPException(400, "Name required")
    now = datetime.now(timezone.utc)
    doc = {"name": name, "parent_id": parent_id, "created_by": str(current_user["_id"]),
           "created_at": now, "updated_at": now}
    result = await db.share_folders.insert_one(doc)
    return _serialize_folder({**doc, "_id": result.inserted_id})


@router.patch("/folders/{folder_id}")
async def rename_folder(
    folder_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = await get_db()
    if not ObjectId.is_valid(folder_id):
        raise HTTPException(404, "Folder not found")
    folder = await db.share_folders.find_one({"_id": ObjectId(folder_id)})
    if not folder:
        raise HTTPException(404, "Folder not found")
    if not await _can_modify(db, current_user, folder):
        raise HTTPException(403, "Only the creator or admin can rename this folder")
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Name required")
    await db.share_folders.update_one({"_id": ObjectId(folder_id)},
                                      {"$set": {"name": name, "updated_at": datetime.now(timezone.utc)}})
    folder["name"] = name
    return _serialize_folder(folder)


async def _delete_folder_recursive(db, folder_id: str):
    # Delete files in this folder
    await db.share_files.delete_many({"folder_id": folder_id})
    # Find and delete subfolders recursively
    subfolders = []
    async for sf in db.share_folders.find({"parent_id": folder_id}, {"_id": 1}):
        subfolders.append(str(sf["_id"]))
    for sfid in subfolders:
        await _delete_folder_recursive(db, sfid)
    await db.share_folders.delete_one({"_id": ObjectId(folder_id)})


@router.delete("/folders/{folder_id}", status_code=204)
async def delete_folder(
    folder_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = await get_db()
    if not ObjectId.is_valid(folder_id):
        raise HTTPException(404, "Folder not found")
    folder = await db.share_folders.find_one({"_id": ObjectId(folder_id)})
    if not folder:
        raise HTTPException(404, "Folder not found")
    if not await _can_modify(db, current_user, folder):
        raise HTTPException(403, "Only the creator or admin can delete this folder")
    await _delete_folder_recursive(db, folder_id)


# ── Files ────────────────────────────────────────────────────────────────

@router.get("/files")
async def list_files(
    folder_id: str = Query("", description="Folder id (empty = root)"),
    current_user: dict = Depends(get_current_user),
):
    db = await get_db()
    filt = {}
    if folder_id:
        filt["folder_id"] = folder_id
    else:
        filt["folder_id"] = None
    cursor = db.share_files.find(filt).sort("name", 1)
    return {"items": [_serialize_file(f) async for f in cursor]}


@router.post("/upload", status_code=201)
async def upload_file(
    file: UploadFile = File(...),
    folder_id: str = Form("", description="Target folder (empty = root)"),
    name: str = Form("", description="Display name (defaults to filename)"),
    current_user: dict = Depends(get_current_user),
):
    db = await get_db()
    now = datetime.now(timezone.utc)

    filename = file.filename or "αρχείο"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Μη επιτρεπτός τύπος αρχείου: {ext}")

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(400, f"Το αρχείο υπερβαίνει τα {MAX_FILE_SIZE // 1024 // 1024}MB")

    display_name = (name or filename).strip()
    mime = file.content_type or "application/octet-stream"

    # Save physical file
    share_dir = settings.upload_path / "sharing"
    share_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}{ext}"
    file_path = share_dir / safe_name
    file_path.write_bytes(contents)

    # Check existing file with same name in same folder → new version
    folder_id_val = folder_id or None
    existing = await db.share_files.find_one({"folder_id": folder_id_val, "name": display_name})
    if existing:
        await db.share_files.update_one(
            {"_id": existing["_id"]},
            {"$push": {"versions": {
                "file_path": str(file_path), "size": len(contents), "mime": mime,
                "uploaded_by": str(current_user["_id"]),
                "uploaded_by_name": current_user.get("name", ""),
                "created_at": now.isoformat(),
            }}, "$set": {"updated_at": now, "uploaded_by": str(current_user["_id"]),
                          "uploaded_by_name": current_user.get("name", "")}},
        )
        updated = await db.share_files.find_one({"_id": existing["_id"]})
        return _serialize_file(updated)

    # New file
    doc = {
        "folder_id": folder_id_val, "name": display_name,
        "versions": [{
            "file_path": str(file_path), "size": len(contents), "mime": mime,
            "uploaded_by": str(current_user["_id"]),
            "uploaded_by_name": current_user.get("name", ""),
            "created_at": now.isoformat(),
        }],
        "uploaded_by": str(current_user["_id"]),
        "uploaded_by_name": current_user.get("name", ""),
        "knowledge_added": False,
        "created_at": now, "updated_at": now,
    }
    result = await db.share_files.insert_one(doc)
    return _serialize_file({**doc, "_id": result.inserted_id})


async def _get_file(db, file_id: str) -> dict:
    if not ObjectId.is_valid(file_id):
        raise HTTPException(404, "File not found")
    f = await db.share_files.find_one({"_id": ObjectId(file_id)})
    if not f:
        raise HTTPException(404, "File not found")
    return f


@router.get("/files/{file_id}/file")
async def get_file_content(
    file_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = await get_db()
    f = await _get_file(db, file_id)
    versions = f.get("versions", [])
    if not versions:
        raise HTTPException(404, "No versions")
    current = versions[-1]
    path = current.get("file_path", "")
    if not path or not os.path.exists(path):
        raise HTTPException(404, "File missing on disk")
    download = request.query_params.get("download") == "1"
    return FileResponse(
        path,
        media_type=current.get("mime", "application/octet-stream"),
        filename=f.get("name", "file") if download else None,
        content_disposition_type="attachment" if download else "inline",
    )


@router.get("/files/{file_id}/versions/{v_idx}/file")
async def get_version_content(
    file_id: str,
    v_idx: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = await get_db()
    f = await _get_file(db, file_id)
    versions = f.get("versions", [])
    if v_idx < 0 or v_idx >= len(versions):
        raise HTTPException(404, "Version not found")
    v = versions[v_idx]
    path = v.get("file_path", "")
    if not path or not os.path.exists(path):
        raise HTTPException(404, "File missing on disk")
    download = request.query_params.get("download") == "1"
    return FileResponse(
        path,
        media_type=v.get("mime", "application/octet-stream"),
        filename=f.get("name", "file") if download else None,
        content_disposition_type="attachment" if download else "inline",
    )


@router.patch("/files/{file_id}")
async def rename_file(
    file_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    db = await get_db()
    f = await _get_file(db, file_id)
    if not await _can_modify(db, current_user, f):
        raise HTTPException(403, "Only the creator or admin can rename this file")
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Name required")
    await db.share_files.update_one({"_id": ObjectId(file_id)},
                                    {"$set": {"name": name, "updated_at": datetime.now(timezone.utc)}})
    f["name"] = name
    return _serialize_file(f)


@router.delete("/files/{file_id}", status_code=204)
async def delete_file(
    file_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = await get_db()
    f = await _get_file(db, file_id)
    if not await _can_modify(db, current_user, f):
        raise HTTPException(403, "Only the creator or admin can delete this file")
    for v in f.get("versions", []):
        path = v.get("file_path", "")
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    await db.share_files.delete_one({"_id": ObjectId(file_id)})


# ── Add to Knowledge (RAG) ───────────────────────────────────────────────

@router.post("/files/{file_id}/to-knowledge")
async def add_to_knowledge(
    file_id: str,
    current_user: dict = Depends(get_current_user),
):
    db = await get_db()
    f = await _get_file(db, file_id)
    versions = f.get("versions", [])
    if not versions:
        raise HTTPException(400, "No versions")
    current = versions[-1]
    path = current.get("file_path", "")
    if not path or not os.path.exists(path):
        raise HTTPException(404, "File missing on disk")

    ext = os.path.splitext(f.get("name", ""))[1].lower()
    from backend.llm.embeddings import embed_batch

    # Extract text chunks per file type
    texts = []
    try:
        if ext == ".json":
            import json
            data = json.load(open(path, encoding="utf-8"))
            if isinstance(data, list):
                texts = [str(x) if not isinstance(x, dict) else json.dumps(x, ensure_ascii=False) for x in data[:100]]
            elif isinstance(data, dict):
                texts = [f"{k}: {json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v}" for k, v in data.items()]
        elif ext == ".md" or ext == ".txt":
            raw = open(path, encoding="utf-8").read()
            texts = [s.strip() for s in raw.split("\n\n") if len(s.strip()) > 30][:100] or [raw[:4000]]
        elif ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(path)
            page_texts = [page.extract_text() or "" for page in reader.pages]
            raw = "\n\n".join(page_texts)
            texts = [s.strip() for s in raw.split("\n\n") if len(s.strip()) > 30][:100]
            if not texts and raw.strip():
                texts = [raw[:4000]]
        elif ext in (".xlsx", ".xls"):
            from backend.llm.normalizer import normalize
            tmp_path = str(path)
            docs = normalize(tmp_path)
            texts = [d["text"][:4000] for d in docs[:100]]
        elif ext in (".doc", ".docx"):
            raise HTTPException(400, "Word files δεν υποστηρίζονται για Γνώση — ανέβασε ως PDF/MD/JSON")
        else:
            raise HTTPException(400, f"Ο τύπος {ext} δεν υποστηρίζεται για Γνώση — χρησιμοποίησε PDF, MD, JSON ή Excel")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Δεν μπόρεσα να διαβάσω το αρχείο: {e}")

    texts = [t for t in texts if t and t.strip()][:100]
    if not texts:
        raise HTTPException(400, "Δεν βρέθηκε κείμενο στο αρχείο")

    # Embed in batches of 20
    all_embs = []
    for i in range(0, len(texts), 20):
        all_embs.extend(await embed_batch(texts[i:i+20], task="retrieval.passage"))

    now = datetime.now(timezone.utc)
    filename = f.get("name", f"share-{file_id}")
    file_id_for_chunk = f"share-{file_id}"
    chunks = []
    for i, (txt, emb) in enumerate(zip(texts, all_embs)):
        chunks.append({
            "org_id": str(current_user["_id"]),  # placeholder, but is_shared=True makes it global
            "file_id": file_id_for_chunk, "filename": filename,
            "chunk_id": f"{file_id_for_chunk}-{i}", "chunk_text": txt[:4000],
            "tab_name": "Κοινός Χώρος",
            "tags": ["shared", os.path.splitext(filename)[1].lstrip(".")],
            "metadata": {"type": "shared_file", "share_file_id": file_id, "source": "shared_space"},
            "embedding": emb, "created_at": now,
            "is_shared": True,
        })
    if chunks:
        await db.knowledge_chunks.delete_many({"file_id": file_id_for_chunk})
        await db.knowledge_chunks.insert_many(chunks)

    await db.share_files.update_one({"_id": ObjectId(file_id)},
                                    {"$set": {"knowledge_added": True, "updated_at": now}})

    return {"ok": True, "chunks": len(chunks),
            "message": f"Το «{filename}» προστέθηκε στη Γνώση ({len(chunks)} chunks). Ο Ασκληπιός μπορεί να το διαβάσει."}


# ── Search ───────────────────────────────────────────────────────────────

@router.get("/search")
async def search_shared(
    q: str = Query("", description="Search term"),
    current_user: dict = Depends(get_current_user),
):
    db = await get_db()
    if not q:
        return {"items": [], "total": 0}
    escaped = q.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    rx = {"$regex": escaped, "$options": "i"}

    folders = []
    async for f in db.share_folders.find({"name": rx}).sort("name", 1).limit(20):
        folders.append({**_serialize_folder(f), "kind": "folder"})

    files = []
    async for f in db.share_files.find({"name": rx}).sort("name", 1).limit(30):
        files.append({**_serialize_file(f), "kind": "file"})

    return {"items": folders + files, "total": len(folders) + len(files)}


# ── Ομαδικό chat (Κοινός Χώρος) ──────────────────────────────────────────
# Live συνομιλία μεταξύ χρηστών με ιστορικό: ΟΛΑ τα μηνύματα είναι κοινά
# (καμία απομόνωση org). Το frontend κάνει polling κάθε λίγα δευτερόλεπτα.

CHAT_HISTORY_MAX = 500  # κρατάμε τα τελευταία 500 μηνύματα


@router.get("/chat/messages")
async def list_chat_messages(
    after: str = Query("", description="ISO timestamp — επιστρέφει μόνο νεότερα μηνύματα"),
    limit: int = Query(100, ge=1, le=300),
    current_user: dict = Depends(get_current_user),
):
    """Ιστορικό μηνυμάτων. Με ?after=<iso> παίρνεις μόνο τα νέα (για live polling)."""
    db = await get_db()
    filt = {}
    if after:
        try:
            filt["created_at"] = {"$gt": datetime.fromisoformat(after)}
        except ValueError:
            pass
    items = []
    async for m in db.share_chat.find(filt).sort("created_at", -1).limit(limit):
        items.append({
            "id": str(m["_id"]),
            "user_id": m.get("user_id", ""),
            "user_name": m.get("user_name", ""),
            "text": m.get("text", ""),
            "created_at": m.get("created_at"),
        })
    items.reverse()
    return {"items": items, "total": len(items)}


@router.post("/chat/messages", status_code=201)
async def post_chat_message(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Νέο μήνυμα — ορατό σε όλους (και σε αυτούς που θα μπουν αργότερα)."""
    db = await get_db()
    body = await request.json()
    text = (body.get("text") or "").strip()[:2000]
    if not text:
        raise HTTPException(400, "Κενό μήνυμα")

    now = datetime.now(timezone.utc)
    doc = {
        "user_id": str(current_user["_id"]),
        "user_name": current_user.get("name") or current_user.get("email", ""),
        "text": text,
        "created_at": now,
    }
    result = await db.share_chat.insert_one(doc)

    # Περικοπή ιστορικού: κρατάμε μόνο τα τελευταία 500 μηνύματα
    total = await db.share_chat.count_documents({})
    if total > CHAT_HISTORY_MAX:
        overflow = total - CHAT_HISTORY_MAX
        old_ids = []
        async for m in db.share_chat.find({}, {"_id": 1}).sort("created_at", 1).limit(overflow):
            old_ids.append(m["_id"])
        if old_ids:
            await db.share_chat.delete_many({"_id": {"$in": old_ids}})

    return {
        "id": str(result.inserted_id),
        "user_id": doc["user_id"],
        "user_name": doc["user_name"],
        "text": doc["text"],
        "created_at": now,
    }
