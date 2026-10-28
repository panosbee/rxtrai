"""
RxTrail — AI Search router: Natural language search with DeepSeek + Jina embeddings.
POST /search/ai — ask a question, get relevant products + AI explanation.
POST /search/embed — (re)build the embedding cache for all SKUs.
"""
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.auth.deps import get_current_user, get_org
from backend.config import settings
from backend.db import get_db

router = APIRouter(prefix="/search", tags=["search"])

# ── In-memory embedding cache (rebuild on /embed or on startup) ──────────
_embedding_cache: list[dict] = []  # [{_id, product_id, commercial_name, embedding, ...}]
_cache_built_at: Optional[datetime] = None


class SearchQuery(BaseModel):
    q: str
    top_k: int = 5
    talk: bool = False  # if True, also return AI explanation


class SearchResult(BaseModel):
    items: list[dict]
    ai_answer: str = ""
    total: int


@router.post("/ai", response_model=SearchResult)
async def ai_search(
    data: SearchQuery,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Natural language search across all SKUs using AI + embeddings."""
    db = await get_db()
    query = data.q.strip()
    if not query:
        raise HTTPException(400, "Empty query")

    # ── Try embedding-based search first ─────────────────────────────────
    items = []
    if _embedding_cache:
        try:
            query_emb = await _embed_text(query)
            items = _cosine_search(query_emb, data.top_k)
        except Exception:
            pass  # Fall back to regex

    # ── Fallback: regex search ──────────────────────────────────────────
    if not items:
        escaped = query.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        filt = {
            "org_id": org_id,
            "$or": [
                {"commercial_name": {"$regex": escaped, "$options": "i"}},
                {"product_id": {"$regex": escaped, "$options": "i"}},
                {"description": {"$regex": escaped, "$options": "i"}},
                {"category": {"$regex": escaped, "$options": "i"}},
            ],
        }
        cursor = db.skus.find(filt).limit(data.top_k)
        items = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            items.append(doc)

    # ── AI "talk" mode — DeepSeek explains results ──────────────────────
    ai_answer = ""
    if data.talk and items:
        try:
            ai_answer = await _ai_explain(query, items[:5])
        except Exception:
            ai_answer = ""

    total = len(items)
    return SearchResult(items=items[:data.top_k], ai_answer=ai_answer, total=total)


@router.post("/embed")
async def build_embeddings(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """(Re)build the embedding cache for all active SKUs in the org."""
    global _embedding_cache, _cache_built_at
    db = await get_db()

    docs = await db.skus.find({"org_id": org_id, "active": True}).to_list(length=5000)
    if not docs:
        return {"ok": True, "count": 0, "message": "No SKUs found"}

    # Build text representations
    texts = []
    for doc in docs:
        text = f"{doc.get('commercial_name', '')} {doc.get('description', '')} {doc.get('category', '')} {doc.get('product_id', '')}".strip()
        texts.append(text)

    # Batch embed via Jina
    embeddings = await _embed_batch(texts)

    # Update cache
    new_cache = []
    for doc, emb in zip(docs, embeddings):
        new_cache.append({
            "_id": str(doc["_id"]),
            "product_id": doc.get("product_id", ""),
            "commercial_name": doc.get("commercial_name", ""),
            "description": doc.get("description", ""),
            "category": doc.get("category", ""),
            "reimb_price": doc.get("reimb_price", 0),
            "embedding": emb,
        })

    _embedding_cache = new_cache
    _cache_built_at = datetime.now(timezone.utc)

    return {"ok": True, "count": len(new_cache), "built_at": _cache_built_at.isoformat()}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _cosine_search(query_emb: list[float], top_k: int = 5) -> list[dict]:
    """Find top_k nearest neighbors by cosine similarity."""
    results = []
    for item in _embedding_cache:
        sim = _cosine_sim(query_emb, item["embedding"])
        results.append((sim, item))

    results.sort(key=lambda x: -x[0])
    top = results[:top_k]

    return [{
        "_id": item["_id"],
        "product_id": item["product_id"],
        "commercial_name": item["commercial_name"],
        "description": item["description"],
        "category": item["category"],
        "reimb_price": item["reimb_price"],
        "_score": round(sim, 3),
    } for sim, item in top if sim > 0.3]


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x ** 2 for x in a) ** 0.5
    norm_b = sum(x ** 2 for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def _embed_text(text: str) -> list[float]:
    """Embed a single text — τοπικά (δωρεάν), όχι Jina."""
    from backend.llm.embeddings import embed_text
    return await embed_text(text, task="retrieval.query")


async def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts — τοπικά."""
    from backend.llm.embeddings import embed_batch
    return await embed_batch(texts, task="retrieval.passage")


async def _ai_explain(query: str, items: list[dict]) -> str:
    """Use DeepSeek to explain search results in natural Greek."""
    if not settings.deepseek_api_key:
        return ""

    context = "\n".join([
        f"- {i.get('commercial_name', '?')} (Κωδ: {i.get('product_id', '—')}, "
        f"Κατηγορία: {i.get('category', '—')}, Τιμή: {i.get('reimb_price', 0)}€)"
        for i in items[:5]
    ])

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{settings.deepseek_base_url}/v1/chat/completions",
            json={
                "model": settings.deepseek_model,
                "messages": [
                    {"role": "system", "content": "Είσαι βοηθός φαρμακευτικού CRM. Απαντάς αναλυτικά στα Ελληνικά. Παρέχεις πλήρεις πληροφορίες για κάθε προϊόν: όνομα, κωδικό, κατηγορία, τιμή, χρήση. Αν υπάρχουν πολλά προϊόντα, τα περιγράφεις όλα. Είσαι κατατοπιστικός και εξυπηρετικός."},
                    {"role": "user", "content": f"Ο χρήστης ρώτησε: «{query}».\n\nΒρέθηκαν αυτά τα προϊόντα:\n{context}\n\nΔώσε μια αναλυτική απάντηση στα Ελληνικά που εξηγεί τι βρέθηκε, περιγράφει κάθε προϊόν ξεχωριστά, και προτείνει το πιο σχετικό για την ερώτηση."},
                ],
                "max_tokens": 600,
            },
            headers={
                "Authorization": f"Bearer {settings.deepseek_api_key}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
