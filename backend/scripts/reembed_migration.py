"""
Re-embed migration: όλα τα παλιά Jina (1024-dim) vectors → τοπικό μοντέλο (384-dim).
Τρέχει ΜΙΑ φορά στον server μετά τη μετάβαση.
"""
import asyncio
import sys

from backend.llm.embeddings import _embed_sync

def reembed_texts(texts):
    if not texts:
        return []
    return _embed_sync(texts, "retrieval.passage")

async def main():
    from backend.db import get_db
    db = await get_db()

    # 1. knowledge_chunks
    n = 0
    batch = []
    ids = []
    async for k in db.knowledge_chunks.find({"embedding": {"$exists": True}}):
        txt = (k.get("chunk_text") or "").strip()
        if not txt:
            continue
        batch.append(txt)
        ids.append(k["_id"])
        if len(batch) >= 64:
            vecs = reembed_texts(batch)
            for i, v in zip(ids, vecs):
                await db.knowledge_chunks.update_one({"_id": i}, {"$set": {"embedding": v}})
            n += len(batch)
            batch, ids = [], []
    if batch:
        vecs = reembed_texts(batch)
        for i, v in zip(ids, vecs):
            await db.knowledge_chunks.update_one({"_id": i}, {"$set": {"embedding": v}})
        n += len(batch)
    print(f"knowledge_chunks re-embedded: {n}")

    # 2. skus.name_embedding
    n = 0
    batch, ids = [], []
    async for s in db.skus.find({"name_embedding": {"$exists": True}}):
        txt = " ".join(filter(None, [
            s.get("commercial_name", ""), s.get("category", ""), s.get("description", ""),
            f"EOPYY:{s.get('eopyy_code','')}" if s.get("eopyy_code") else "",
        ])).strip()
        if not txt:
            continue
        batch.append(txt)
        ids.append(s["_id"])
    if batch:
        vecs = reembed_texts(batch)
        for i, v in zip(ids, vecs):
            await db.skus.update_one({"_id": i}, {"$set": {"name_embedding": v}})
        n += len(batch)
    print(f"skus.name_embedding re-embedded: {n}")

    # 3. doctors.profile_embedding
    n = 0
    batch, ids = [], []
    async for d in db.doctors.find({"profile_embedding": {"$exists": True}}):
        txt = " ".join(filter(None, [d.get("name", ""), d.get("specialty", ""), d.get("notes", "")])).strip()
        if not txt:
            continue
        batch.append(txt)
        ids.append(d["_id"])
    if batch:
        vecs = reembed_texts(batch)
        for i, v in zip(ids, vecs):
            await db.doctors.update_one({"_id": i}, {"$set": {"profile_embedding": v}})
        n += len(batch)
    print(f"doctors.profile_embedding re-embedded: {n}")

    # 4. agent_memory.summary_embedding
    n = 0
    batch, ids = [], []
    async for m in db.agent_memory.find({"summary_embedding": {"$exists": True}}):
        txt = (m.get("summary") or m.get("content") or "").strip()
        if not txt:
            continue
        batch.append(txt[:2000])
        ids.append(m["_id"])
    if batch:
        vecs = reembed_texts(batch)
        for i, v in zip(ids, vecs):
            await db.agent_memory.update_one({"_id": i}, {"$set": {"summary_embedding": v}})
        n += len(batch)
    print(f"agent_memory.summary_embedding re-embedded: {n}")

    print("DONE")

asyncio.run(main())
