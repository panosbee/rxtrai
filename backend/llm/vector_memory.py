"""
RxTrail — Vector Memory: Semantic Search via Jina Embeddings.

Phase 2 of the Multi-Tier Memory System.

Capabilities:
  1. Index SKUs, doctors, and episodes into vector space
  2. Semantic search: "find products like X for orthopedic use"
  3. Memory search: "what did I discuss with Dr. X last time?"
  4. Cross-entity search: "which doctors are similar to Dr. X?"

Storage:
  - Embeddings stored in MongoDB alongside documents
  - `name_embedding` on SKUs
  - `profile_embedding` on doctors  
  - `summary_embedding` on episodes (agent_memory)

Uses Jina Embeddings API (jina-embeddings-v3, 1024-dim vectors).
"""

import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.config import settings
from backend.llm.embeddings import embed_text, embed_batch, cosine_similarity

logger = logging.getLogger(__name__)

# ── Indexing ──────────────────────────────────────────────────────────────────

async def index_all_skus(db: AsyncIOMotorDatabase, org_id: str) -> int:
    """Embed all SKUs for an org that don't have embeddings yet."""
    cursor = db.skus.find({
        "org_id": org_id,
        "active": True,
        "name_embedding": {"$exists": False},
    })
    
    batch = []
    ids = []
    async for sku in cursor:
        # Build rich text for embedding: name + category + description + codes
        text_parts = [
            sku.get("commercial_name", ""),
            sku.get("category", ""),
            sku.get("description", ""),
            f"EOPYY:{sku.get('eopyy_code', '')}" if sku.get("eopyy_code") else "",
            f"ICD10:{sku.get('icd10_code', '')}" if sku.get("icd10_code") else "",
        ]
        text = " ".join(filter(None, text_parts))
        if text.strip():
            batch.append(text)
            ids.append(sku["_id"])
    
    if not batch:
        logger.info(f"No new SKUs to index for org {org_id}")
        return 0
    
    logger.info(f"Embedding {len(batch)} SKUs for org {org_id}...")
    embeddings = await embed_batch(batch, task="retrieval.passage")
    
    # Store embeddings back
    indexed = 0
    for obj_id, emb in zip(ids, embeddings):
        await db.skus.update_one(
            {"_id": obj_id},
            {"$set": {
                "name_embedding": emb,
                "name_embedding_at": datetime.now(timezone.utc),
            }}
        )
        indexed += 1
    
    logger.info(f"Indexed {indexed} SKUs for org {org_id}")
    return indexed


async def index_all_doctors(db: AsyncIOMotorDatabase, org_id: str) -> int:
    """Embed all doctors for an org that don't have profile embeddings."""
    cursor = db.doctors.find({
        "org_id": org_id,
        "profile_embedding": {"$exists": False},
    })
    
    batch = []
    ids = []
    async for doc in cursor:
        # Rich profile text: name + specialty + notes + stats
        text_parts = [
            doc.get("name", ""),
            f"Specialty: {doc.get('specialty', 'OTHER')}",
            doc.get("notes", ""),
            f"Rx count (90d): {doc.get('rx_count_90d', 0)}",
            f"Open cases: {doc.get('open_cases_count', 0)}",
        ]
        text = " ".join(filter(None, text_parts))
        if text.strip():
            batch.append(text)
            ids.append(doc["_id"])
    
    if not batch:
        logger.info(f"No new doctors to index for org {org_id}")
        return 0
    
    logger.info(f"Embedding {len(batch)} doctors for org {org_id}...")
    embeddings = await embed_batch(batch, task="retrieval.passage")
    
    indexed = 0
    for obj_id, emb in zip(ids, embeddings):
        await db.doctors.update_one(
            {"_id": obj_id},
            {"$set": {
                "profile_embedding": emb,
                "profile_embedding_at": datetime.now(timezone.utc),
            }}
        )
        indexed += 1
    
    logger.info(f"Indexed {indexed} doctors for org {org_id}")
    return indexed


async def index_recent_episodes(db: AsyncIOMotorDatabase, org_id: str, days: int = 90) -> int:
    """Embed recent episodic memories (last N days)."""
    from datetime import timedelta
    since = datetime.now(timezone.utc) - timedelta(days=days)
    
    cursor = db.agent_memory.find({
        "org_id": org_id,
        "type": "episodic",
        "at": {"$gte": since},
        "summary_embedding": {"$exists": False},
    })
    
    batch = []
    ids = []
    async for ep in cursor:
        text = ep.get("summary", "")
        if text.strip():
            batch.append(text)
            ids.append(ep["_id"])
    
    if not batch:
        return 0
    
    logger.info(f"Embedding {len(batch)} episodes for org {org_id}...")
    embeddings = await embed_batch(batch, task="retrieval.passage")
    
    indexed = 0
    for obj_id, emb in zip(ids, embeddings):
        await db.agent_memory.update_one(
            {"_id": obj_id},
            {"$set": {"summary_embedding": emb}}
        )
        indexed += 1
    
    logger.info(f"Indexed {indexed} episodes for org {org_id}")
    return indexed


async def index_all(db: AsyncIOMotorDatabase, org_id: str) -> dict:
    """Run all indexing for an org. Called on startup or via admin trigger."""
    results = {}
    try:
        results["skus"] = await index_all_skus(db, org_id)
    except Exception as e:
        results["skus_error"] = str(e)
        logger.error(f"SKU indexing failed: {e}")
    
    try:
        results["doctors"] = await index_all_doctors(db, org_id)
    except Exception as e:
        results["doctors_error"] = str(e)
        logger.error(f"Doctor indexing failed: {e}")
    
    try:
        results["episodes"] = await index_recent_episodes(db, org_id)
    except Exception as e:
        results["episodes_error"] = str(e)
        logger.error(f"Episode indexing failed: {e}")
    
    return results


# ── Semantic Search ───────────────────────────────────────────────────────────

async def search_skus_semantic(
    db: AsyncIOMotorDatabase,
    org_id: str,
    query: str,
    top_k: int = 10,
    min_score: float = 0.3,
) -> list[dict]:
    """Semantic search over SKUs using vector similarity."""
    # Get all active SKUs with embeddings
    cursor = db.skus.find({
        "org_id": org_id,
        "active": True,
        "name_embedding": {"$exists": True},
    })
    
    skus = []
    async for s in cursor:
        skus.append(s)
    
    if not skus:
        return []
    
    # Embed query
    query_vec = await embed_text(query, task="retrieval.query")
    
    # Score each
    results = []
    for sku in skus:
        score = cosine_similarity(query_vec, sku["name_embedding"])
        if score >= min_score:
            results.append({
                "id": str(sku["_id"]),
                "name": sku.get("commercial_name", ""),
                "product_id": sku.get("product_id", ""),
                "category": sku.get("category", ""),
                "price": sku.get("reimb_price", 0),
                "eopyy_code": sku.get("eopyy_code", ""),
                "description": sku.get("description", "")[:200],
                "_score": round(score, 3),
            })
    
    results.sort(key=lambda x: -x["_score"])
    return results[:top_k]


async def search_doctors_semantic(
    db: AsyncIOMotorDatabase,
    org_id: str,
    query: str,
    top_k: int = 10,
    min_score: float = 0.3,
) -> list[dict]:
    """Semantic search over doctors."""
    cursor = db.doctors.find({
        "org_id": org_id,
        "profile_embedding": {"$exists": True},
    })
    
    doctors = []
    async for d in cursor:
        doctors.append(d)
    
    if not doctors:
        return []
    
    query_vec = await embed_text(query, task="retrieval.query")
    
    results = []
    for doc in doctors:
        score = cosine_similarity(query_vec, doc["profile_embedding"])
        if score >= min_score:
            results.append({
                "id": str(doc["_id"]),
                "name": doc.get("name", ""),
                "specialty": doc.get("specialty", ""),
                "notes": (doc.get("notes", "") or "")[:200],
                "phone": doc.get("phone", ""),
                "rx_count_90d": doc.get("rx_count_90d", 0),
                "_score": round(score, 3),
            })
    
    results.sort(key=lambda x: -x["_score"])
    return results[:top_k]


async def search_episodes_semantic(
    db: AsyncIOMotorDatabase,
    org_id: str,
    query: str,
    top_k: int = 10,
    min_score: float = 0.3,
    days: int = 90,
) -> list[dict]:
    """Semantic search over past episodes (conversations, events)."""
    from datetime import timedelta
    since = datetime.now(timezone.utc) - timedelta(days=days)
    
    cursor = db.agent_memory.find({
        "org_id": org_id,
        "type": "episodic",
        "at": {"$gte": since},
        "summary_embedding": {"$exists": True},
    })
    
    episodes = []
    async for ep in cursor:
        episodes.append(ep)
    
    if not episodes:
        return []
    
    query_vec = await embed_text(query, task="retrieval.query")
    
    results = []
    for ep in episodes:
        score = cosine_similarity(query_vec, ep["summary_embedding"])
        if score >= min_score:
            results.append({
                "event_type": ep.get("event_type", ""),
                "summary": ep.get("summary", "")[:300],
                "at": ep.get("at").isoformat() if ep.get("at") else "",
                "entities": ep.get("entities", []),
                "_score": round(score, 3),
            })
    
    results.sort(key=lambda x: -x["_score"])
    return results[:top_k]


async def search_all_memory(
    db: AsyncIOMotorDatabase,
    org_id: str,
    query: str,
    top_k: int = 15,
) -> dict:
    """Cross-entity semantic search: SKUs + doctors + episodes."""
    sku_results = await search_skus_semantic(db, org_id, query, top_k=5)
    doctor_results = await search_doctors_semantic(db, org_id, query, top_k=5)
    episode_results = await search_episodes_semantic(db, org_id, query, top_k=5)
    
    return {
        "query": query,
        "products": sku_results,
        "doctors": doctor_results,
        "past_events": episode_results,
        "total_found": len(sku_results) + len(doctor_results) + len(episode_results),
    }
