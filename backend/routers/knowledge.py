"""
RxTrail — Knowledge Router: Upload/List/Delete knowledge files.
Supports: .xlsx, .xls, .json, .md
Uses normalizer.py for JSON/MD -> uniform chunk format.
"""
import os, tempfile, traceback, uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, UploadFile, File
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.auth.deps import get_current_user, get_org
from backend.db import get_db
from backend.llm.knowledge import list_knowledge_files, delete_knowledge_file, search_knowledge

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
ALLOWED_EXT = {".json", ".md", ".xlsx", ".xls"}
MAX_BYTES = 10 * 1024 * 1024


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        return JSONResponse(status_code=400, content={"ok": False, "error": f"Not allowed: {ext}"})

    data = await file.read()
    if len(data) > MAX_BYTES:
        return JSONResponse(status_code=413, content={"ok": False, "error": "File too large (max 10MB)"})

    filename = file.filename or "unknown"
    file_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{org_id}:{filename}"))
    now = datetime.now(timezone.utc)

    # Excel: use existing pipeline
    if ext in (".xlsx", ".xls"):
        try:
            from backend.llm.knowledge import upload_knowledge
            result = await upload_knowledge(db, org_id, str(current_user["_id"]), data, filename, file_id)
            return result
        except Exception as e:
            traceback.print_exc()
            return JSONResponse(status_code=500, content={"ok": False, "error": str(e), "type": type(e).__name__})

    # JSON / Markdown: normalize, embed, store
    return await _upload_normalized(ext, data, filename, file_id, db, org_id, str(current_user["_id"]), now)


async def _upload_normalized(ext, data, filename, file_id, db, org_id, user_id, now):
    from backend.llm.normalizer import normalize
    from backend.llm.embeddings import embed_batch

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    try:
        tmp.write(data)
        tmp.close()

        docs = normalize(tmp.name)
        if not docs:
            return JSONResponse(status_code=400, content={"ok": False, "error": "No valid chunks found"})

        # Delete old chunks for this file
        await db.knowledge_chunks.delete_many({"org_id": org_id, "file_id": file_id})
        await db.knowledge_files.delete_one({"org_id": org_id, "file_id": file_id})

        # Embed in batches of 20
        texts = [d["text"][:4000] for d in docs]
        all_embs = []
        for i in range(0, len(texts), 20):
            embs = await embed_batch(texts[i:i+20], task="retrieval.passage")
            all_embs.extend(embs)

        # Store RAG chunks
        chunks = []
        for doc, emb in zip(docs, all_embs):
            chunks.append({
                "org_id": org_id, "file_id": file_id, "filename": filename,
                "chunk_id": doc["id"], "chunk_text": doc["text"][:4000],
                "tab_name": filename.rsplit(".", 1)[0],
                "tags": doc["tags"], "metadata": doc["metadata"],
                "embedding": emb, "created_at": now,
                "is_shared": False,  # user's own knowledge — visible only to their org
            })
        if chunks:
            await db.knowledge_chunks.insert_many(chunks)

        # Store lookup entries for SKUs (exact search, not semantic)
        lookup_count = 0
        for doc in docs:
            if doc["metadata"].get("type") == "sku" and doc["metadata"].get("sku"):
                await db.knowledge_lookup.update_one(
                    {"org_id": org_id, "sku": doc["metadata"]["sku"]},
                    {"$set": {
                        "org_id": org_id, "sku": doc["metadata"]["sku"],
                        "title": doc["title"], "text": doc["text"][:5000],
                        "metadata": doc["metadata"], "updated_at": now,
                        "is_shared": False,
                    }},
                    upsert=True,
                )
                lookup_count += 1

        await db.knowledge_files.insert_one({
            "org_id": org_id, "file_id": file_id, "filename": filename,
            "tab_names": [filename.rsplit(".", 1)[0]],
            "chunk_count": len(chunks),
            "uploaded_by": user_id,
            "created_at": now, "updated_at": now,
        })

        return {"ok": True, "filename": filename, "chunks": len(chunks),
                "tabs": 1, "tab_names": [filename.rsplit(".", 1)[0]],
                "lookup_entries": lookup_count,
                "message": f"Uploaded {len(chunks)} chunks, {lookup_count} SKU lookup entries"}

    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e), "type": type(e).__name__})
    finally:
        try: os.unlink(tmp.name)
        except: pass


@router.get("/files")
async def list_files(
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await list_knowledge_files(db, org_id)


@router.delete("/{file_id}")
async def delete_file(
    file_id: str,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    result = await delete_knowledge_file(db, org_id, file_id)
    if not result["deleted"]:
        return JSONResponse(status_code=404, content={"ok": False, "error": result.get("error") or "File not found"})
    return result


@router.get("/tabs")
async def list_tabs(
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    case_ids = await db.knowledge_files.distinct("file_id", {"org_id": org_id, "source": "case"})
    filt = {"org_id": org_id}
    if case_ids:
        filt["file_id"] = {"$nin": case_ids}
    tabs = await db.knowledge_chunks.distinct("tab_name", filt)
    return {"tabs": sorted(tabs)}


@router.get("/lookup/{sku}")
async def lookup_sku(
    sku: str,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Exact SKU lookup — returns prescribing rules, price, ICD, etc."""
    doc = await db.knowledge_lookup.find_one({"org_id": org_id, "sku": sku})
    if not doc:
        return JSONResponse(status_code=404, content={"ok": False, "error": f"SKU {sku} not found"})
    doc["_id"] = str(doc["_id"])
    return {"ok": True, "sku": sku, "data": doc}


@router.get("/search")
async def search_debug(
    q: str,
    top_k: int = 5,
    tab: str = "",
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    tab_filter = tab if tab else None
    results = await search_knowledge(db, org_id, q, top_k=top_k, tab_filter=tab_filter, exclude_case=True)
    return {"query": q, "results": results, "count": len(results)}
