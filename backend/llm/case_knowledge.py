"""
RxTrail — Case Knowledge: ingest case documents & prescription texts into the
org's PRIVATE knowledge base so the agent (Ασκληπιός) can read them via RAG.

Rules:
  - Text is PII-scrubbed (ΑΜΚΑ, phones, dates) BEFORE embedding, because the
    embedding API is external (GDPR firewall — spec 01 §8).
  - Chunks are private to the org: is_shared=False.
  - file_id is deterministic per document ("doc-{id}") / prescription ("rx-{id}")
    so re-indexing replaces old chunks.
"""
import logging
from datetime import datetime, timezone

from backend.llm.embeddings import embed_batch
from backend.llm.scrubber import scrub_text_sync

logger = logging.getLogger("rxtrail.case_knowledge")

MAX_CHUNK_CHARS = 2000
MAX_CHUNKS = 30


def chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split text into chunks at line boundaries, capped in size and count."""
    text = (text or "").strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    if not paragraphs:
        return []

    chunks = []
    current = ""
    for p in paragraphs:
        if len(current) + len(p) + 1 > max_chars and current:
            chunks.append(current)
            current = p
        else:
            current = f"{current}\n{p}" if current else p
    if current:
        chunks.append(current)

    return [c[: max_chars * 2] for c in chunks[:MAX_CHUNKS]]


async def index_text_as_knowledge(
    db,
    org_id: str,
    user_id: str,
    text: str,
    *,
    file_id: str,
    filename: str,
    tab_name: str,
    case_id: str = "",
    doc_type: str = "DOCUMENT",
    tags: list | None = None,
) -> dict:
    """Scrub → chunk → embed → upsert into knowledge_chunks + knowledge_files.

    Returns {"status": "ok"|"empty"|"error", "chunks": n}
    """
    now = datetime.now(timezone.utc)
    try:
        scrubbed = scrub_text_sync(text)
        chunks = chunk_text(scrubbed)
        if not chunks:
            return {"status": "empty", "chunks": 0}

        embeddings = []
        for i in range(0, len(chunks), 20):
            embeddings.extend(await embed_batch(chunks[i:i+20], task="retrieval.passage"))

        docs = []
        for i, c in enumerate(chunks):
            docs.append({
                "org_id": org_id,
                "file_id": file_id,
                "filename": filename,
                "tab_name": tab_name,
                "product_code": "",
                "chunk_text": c,
                "embedding": embeddings[i] if i < len(embeddings) else [],
                "row_index": i,
                "tags": tags or [],
                "is_shared": False,
                "metadata": {"case_id": case_id, "doc_type": doc_type},
                "created_at": now,
                "uploaded_by": user_id,
            })

        # Re-index semantics: replace any previous chunks for this file_id
        await db.knowledge_chunks.delete_many({"org_id": org_id, "file_id": file_id})
        if docs:
            await db.knowledge_chunks.insert_many(docs)

        await db.knowledge_files.update_one(
            {"org_id": org_id, "file_id": file_id},
            {"$set": {
                "org_id": org_id,
                "file_id": file_id,
                "filename": filename,
                "tab_names": [tab_name],
                "chunk_count": len(docs),
                "source": "case",
                "case_id": case_id,
                "uploaded_by": user_id,
                "created_at": now,
                "updated_at": now,
            }},
            upsert=True,
        )
        logger.info(f"Case knowledge indexed: {filename} → {len(docs)} chunks (org={org_id})")
        return {"status": "ok", "chunks": len(docs)}
    except Exception as e:
        logger.warning(f"Case knowledge indexing failed for {filename}: {e}")
        return {"status": "error", "chunks": 0, "error": str(e)}


async def remove_case_knowledge(db, org_id: str, file_id: str):
    """Delete knowledge chunks + file entry (e.g. when a document is deleted)."""
    try:
        await db.knowledge_chunks.delete_many({"org_id": org_id, "file_id": file_id})
        await db.knowledge_files.delete_one({"org_id": org_id, "file_id": file_id})
        logger.info(f"Case knowledge removed: {file_id} (org={org_id})")
    except Exception:
        logger.exception(f"Failed to remove case knowledge for {file_id}")
