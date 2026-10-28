"""
RxTrail — Knowledge Engine: RAG over uploaded Excel files.

Architecture:
  1. User uploads Excel → parse ALL tabs via openpyxl
  2. Each row → natural-language chunk (tab name + all fields)
  3. Chunks embedded via Jina v3 → stored in MongoDB with embedding vector
  4. Agent queries knowledge via semantic search → top-k chunks injected into prompt
  5. Multi-tab: each tab preserved as metadata (category grouping)
"""
import io
import json
import logging
import re as _re
from datetime import datetime, timezone
from typing import Optional

import openpyxl
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.llm.embeddings import embed_batch, embed_text
from backend.config import settings

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Chunk Builder: Excel row → natural language text
# ═══════════════════════════════════════════════════════════════════

def _row_to_chunk_text(tab_name: str, headers: list[str], row: list) -> str:
    """Convert a spreadsheet row into a rich natural-language chunk.
    
    Preserves ALL information — no field left behind.
    Tab name provides category context.
    """
    parts = [f"[Tab: {tab_name}]"]
    
    for i, header in enumerate(headers):
        if i >= len(row):
            break
        val = row[i]
        if val is None or str(val).strip() == "":
            continue
        val_str = str(val).strip()
        parts.append(f"{header}: {val_str}")
    
    return " | ".join(parts)


def _is_header_row(values: list, tab_name: str) -> bool:
    """Heuristic to detect if a row is a header or sub-header (not a data row)."""
    non_empty = [str(v).strip() for v in values if v is not None and str(v).strip()]
    if not non_empty:
        return True  # empty row = skip
    
    # If first cell looks like a section title (ALL CAPS, no data-like patterns)
    first = non_empty[0] if non_empty else ""
    # Headers typically have short labels like "Κωδικός", "Προϊόν", etc.
    # Data rows have codes like "NM-5601", actual names, numbers
    # Skip rows that are ALL CAPS and short (like section headers)
    if len(first) > 3 and first.isupper() and len(non_empty) <= 2:
        return True  # section sub-header like "ΓΑΣΤΡΕΝΤΕΡΟΛΟΓΙΚΑ ΣΚΕΥΑΣΜΑΤΑ"
    
    return False


def _parse_excel_all_tabs(file_bytes: bytes, filename: str) -> list[dict]:
    """Parse ALL tabs of an Excel file into chunks.
    
    Returns: list of {"chunk_text": str, "tab_name": str, "product_code": str, "row_index": int}
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    all_chunks = []
    
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        
        # First row = headers (column names)
        headers = [str(h).strip() if h else f"Col_{i}" for i, h in enumerate(rows[0])]
        
        for row_idx, row in enumerate(rows[1:], start=1):
            values = list(row) if row else []
            
            # Skip empty/header rows
            if _is_header_row(values, sheet_name):
                continue
            
            chunk_text = _row_to_chunk_text(sheet_name, headers, values)
            if not chunk_text or chunk_text == f"[Tab: {sheet_name}]":
                continue  # completely empty row
            
            # Try to extract product code (look for ID-like column)
            product_code = ""
            for i, h in enumerate(headers):
                hl = h.lower()
                if any(kw in hl for kw in ['κωδικός', 'κωδ', 'code', 'id', 'eopyy', 'barcode']):
                    if i < len(values) and values[i]:
                        product_code = str(values[i]).strip()
                        break
            if not product_code:
                # Use first cell as fallback identifier
                if values and values[0]:
                    product_code = str(values[0]).strip()
            
            all_chunks.append({
                "chunk_text": chunk_text,
                "tab_name": sheet_name,
                "product_code": product_code,
                "row_index": row_idx,
            })
    
    wb.close()
    return all_chunks


# ═══════════════════════════════════════════════════════════════════
# Embed + Store
# ═══════════════════════════════════════════════════════════════════

async def _compute_cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors (MongoDB fallback)."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def upload_knowledge(
    db: AsyncIOMotorDatabase,
    org_id: str,
    user_id: str,
    file_bytes: bytes,
    filename: str,
    file_id: str,  # pre-generated stable ID for this upload
) -> dict:
    """Upload Excel, parse ALL tabs, embed ALL chunks, store in MongoDB.
    
    If the same file_id exists, replaces old chunks (update semantics).
    """
    now = datetime.now(timezone.utc)
    
    # 1. Delete old chunks for this file (if re-upload)
    await db.knowledge_chunks.delete_many({"org_id": org_id, "file_id": file_id})
    
    # 2. Parse all tabs
    chunks = _parse_excel_all_tabs(file_bytes, filename)
    if not chunks:
        return {"status": "empty", "message": "Το αρχείο δεν περιέχει δεδομένα προς ευρετηρίαση.", "chunks": 0, "tabs": 0}
    
    # 3. Embed in batches of 20 (Jina batch limit safety)
    texts = [c["chunk_text"][:4000] for c in chunks]
    all_embeddings = []
    for i in range(0, len(texts), 20):
        all_embeddings.extend(await embed_batch(texts[i:i+20], task="retrieval.passage"))

    # 4. Build MongoDB documents
    docs = []
    for i, chunk in enumerate(chunks):
        docs.append({
            "org_id": org_id,
            "file_id": file_id,
            "filename": filename,
            "tab_name": chunk["tab_name"],
            "product_code": chunk["product_code"],
            "chunk_text": chunk["chunk_text"][:4000],
            "embedding": all_embeddings[i] if i < len(all_embeddings) else [],
            "row_index": chunk["row_index"],
            "created_at": now,
            "uploaded_by": user_id,
            "is_shared": False,  # user's own knowledge — visible only to their org
        })
    
    # 5. Insert all
    if docs:
        await db.knowledge_chunks.insert_many(docs)
    
    tabs = list(set(c["tab_name"] for c in chunks))
    
    # 6. Store file metadata
    await db.knowledge_files.update_one(
        {"org_id": org_id, "file_id": file_id},
        {"$set": {
            "org_id": org_id,
            "file_id": file_id,
            "filename": filename,
            "tab_names": tabs,
            "chunk_count": len(docs),
            "uploaded_by": user_id,
            "created_at": now,
            "updated_at": now,
        }},
        upsert=True,
    )
    
    logger.info(f"Knowledge upload: {filename} → {len(docs)} chunks across {len(tabs)} tabs (org={org_id})")
    return {
        "status": "ok",
        "file_id": file_id,
        "filename": filename,
        "chunks": len(docs),
        "tabs": len(tabs),
        "tab_names": tabs,
    }


# ═══════════════════════════════════════════════════════════════════
# Search / Retrieve
# ═══════════════════════════════════════════════════════════════════

async def search_knowledge(
    db: AsyncIOMotorDatabase,
    org_id: str,
    query: str,
    top_k: int = 5,
    tab_filter: Optional[str] = None,
    exclude_case: bool = False,
) -> list[dict]:
    """Semantic search over knowledge chunks.
    
    1. Embed the query
    2. Cosine similarity against all chunks (or use MongoDB Atlas Search if available)
    3. Return top-k chunks with metadata

    If exclude_case=True, case-private knowledge (case documents / prescription
    OCR) is skipped — used by the general Knowledge panel.
    """
    # 1. Embed query
    query_embedding = await embed_text(query, task="retrieval.query")
    if not query_embedding:
        return []
    
    # 2. Fetch chunks with embeddings (shared default + this org) with optional tab filter
    filt = {"$or": [{"is_shared": True}, {"org_id": org_id}]}
    if tab_filter:
        filt["tab_name"] = {"$regex": tab_filter, "$options": "i"}
    if exclude_case:
        case_ids = await db.knowledge_files.distinct("file_id", {"org_id": org_id, "source": "case"})
        if case_ids:
            filt["file_id"] = {"$nin": case_ids}
    
    # 3. Compute cosine on-the-fly (fast enough for <10K chunks per org)
    # For production at scale (>50K), use MongoDB Atlas Vector Search
    scored = []
    cursor = db.knowledge_chunks.find(filt)
    async for doc in cursor:
        emb = doc.get("embedding", [])
        if not emb:
            continue
        score = await _compute_cosine(query_embedding, emb)
        scored.append({
            "chunk_text": doc.get("chunk_text", ""),
            "tab_name": doc.get("tab_name", ""),
            "product_code": doc.get("product_code", ""),
            "filename": doc.get("filename", ""),
            "file_id": doc.get("file_id", ""),
            "score": round(score, 4),
        })
    
    # 4. Sort by score desc, take top_k
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


async def build_knowledge_context(
    db: AsyncIOMotorDatabase,
    org_id: str,
    query: str,
    top_k: int = 3,
) -> str:
    """Build a context string for the agent prompt from knowledge base.
    
    Returns empty string if no chunks found or no knowledge exists.
    """
    results = await search_knowledge(db, org_id, query, top_k=top_k)
    if not results:
        return ""
    
    lines = ["ΑΚΡΙΒΕΙΣ ΟΔΗΓΙΕΣ ΑΠΟ ΤΗ ΒΑΣΗ ΓΝΩΣΗΣ (RAG):"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. [{r['tab_name']}] {r['chunk_text']} (συνάφεια: {r['score']})")
    lines.append("ΧΡΗΣΙΜΟΠΟΙΗΣΕ ΜΟΝΟ ΤΙΣ ΠΑΡΑΠΑΝΩ ΟΔΗΓΙΕΣ ΓΙΑ ΝΑ ΑΠΑΝΤΗΣΕΙΣ. ΜΗΝ ΕΠΙΝΟΕΙΣ ΤΙΠΟΤΑ.")
    
    return "\n".join(lines)


async def list_knowledge_files(db: AsyncIOMotorDatabase, org_id: str) -> list[dict]:
    """List uploaded knowledge files for an org.

    Excludes case-private knowledge (source="case" — case documents and
    prescription OCR texts). Those are visible only inside their case,
    not in the general Knowledge panel.
    """
    cursor = db.knowledge_files.find(
        {"org_id": org_id,
         "$or": [{"source": {"$ne": "case"}}, {"source": {"$exists": False}}]},
        {"file_id": 1, "filename": 1, "tab_names": 1, "chunk_count": 1, "created_at": 1, "_id": 0}
    ).sort("created_at", -1)
    files = await cursor.to_list(length=100)
    for f in files:
        f["created_at"] = str(f.get("created_at", ""))[:19] if f.get("created_at") else ""
    return files


async def delete_knowledge_file(db: AsyncIOMotorDatabase, org_id: str, file_id: str) -> dict:
    """Delete an uploaded knowledge file and all its chunks.

    Case-private knowledge (source="case") cannot be deleted from the
    general Knowledge panel — it is managed through its case/prescription.
    """
    file_doc = await db.knowledge_files.find_one({"org_id": org_id, "file_id": file_id})
    if not file_doc:
        return {"deleted": False, "chunks_removed": 0, "file_id": file_id}
    if file_doc.get("source") == "case":
        return {
            "deleted": False,
            "chunks_removed": 0,
            "file_id": file_id,
            "error": "Η γνώση αυτή ανήκει σε περιστατικό/συνταγή — διαγράφεται από εκεί, όχι από τη Βάση Γνώσης.",
        }
    chunk_result = await db.knowledge_chunks.delete_many({"org_id": org_id, "file_id": file_id})
    file_result = await db.knowledge_files.delete_one({"org_id": org_id, "file_id": file_id})
    return {
        "deleted": file_result.deleted_count > 0,
        "chunks_removed": chunk_result.deleted_count,
        "file_id": file_id,
    }
