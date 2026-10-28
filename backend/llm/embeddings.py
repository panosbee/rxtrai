"""
RxTrail — Embeddings client: LOCAL sentence-transformers (δωρεάν, χωρίς API).

Το Jina είναι out-of-credits → τοπικό μοντέλο `intfloat/multilingual-e5-small`
(384-dim, multilingual με ελληνικά, τρέχει στον CPU του server).
Fallback: Jina API αν οριστεί κλειδί και αποτύχει το τοπικό μοντέλο.

Χρήσεις:
  1. Knowledge uploads (chunks → embeddings)
  2. Semantic search over knowledge (agent RAG)
  3. Fuzzy SKU matching
  4. Vector memory (name_embedding κλπ.)
"""
import asyncio
import logging
from functools import lru_cache

from backend.config import settings

logger = logging.getLogger("rxtrail.embeddings")

_model = None
_model_load_error = None

# e5 μοντέλα θέλουν prefixes για σωστή σημασιολογική ποιότητα
TASK_PREFIX = {
    "retrieval.query": "query: ",
    "retrieval.passage": "passage: ",
}


def _load_local_model():
    """Φορτώνει το τοπικό μοντέλο μία φορά (CPU)."""
    global _model, _model_load_error
    if _model is not None or _model_load_error is not None:
        return _model
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(settings.local_embeddings_model, device="cpu")
        logger.info(f"Local embeddings model loaded: {settings.local_embeddings_model}")
    except Exception as e:
        _model_load_error = str(e)
        logger.error(f"Local embeddings model failed to load: {e}")
    return _model


def _embed_sync(texts: list[str], task: str) -> list[list[float]]:
    """Synchronous local embedding (τρέχει σε executor)."""
    model = _load_local_model()
    if model is None:
        # Fallback: Jina (αν υπάρχει κλειδί)
        if settings.jina_api_key:
            raise JinaFallbackSignal()
        raise RuntimeError(
            f"Δεν υπάρχει διαθέσιμο μοντέλο embeddings ({_model_load_error or 'not installed'}). "
            f"Εγκατέστησε sentence-transformers στον server."
        )
    prefix = TASK_PREFIX.get(task, "")
    texts_prefixed = [prefix + t for t in texts]
    vecs = model.encode(
        texts_prefixed,
        batch_size=32,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return [v.tolist() for v in vecs]


class JinaFallbackSignal(Exception):
    """Εσωτερικό σήμα: το τοπικό μοντέλο δεν υπάρχει — δοκίμασε Jina."""


async def _jina_embed(texts: list[str], task: str) -> list[list[float]]:
    import httpx
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            settings.jina_embeddings_url,
            headers={
                "Authorization": f"Bearer {settings.jina_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.jina_embeddings_model,
                "task": task,
                "input": texts,
            },
        )
        response.raise_for_status()
        data = response.json()
        return [d["embedding"] for d in data["data"]]


async def embed_text(text: str, task: str = "retrieval.passage") -> list[float]:
    """Embedding ενός κειμένου — τοπικά (δωρεάν), με Jina fallback."""
    if not text or not text.strip():
        return []
    return (await embed_batch([text], task))[0]


async def embed_batch(texts: list[str], task: str = "retrieval.passage") -> list[list[float]]:
    """Batch embeddings — τοπικά, με Jina fallback."""
    if not texts:
        return []
    non_empty = [t for t in texts if t and t.strip()]
    if not non_empty:
        return [[] for _ in texts]

    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, _embed_sync, non_empty, task)
    except JinaFallbackSignal:
        pass
    except RuntimeError:
        raise
    except Exception as e:
        logger.error(f"Local embedding failed: {e}")

    # Fallback: Jina (αν υπάρχει κλειδί και δεν είναι άδειο)
    if settings.jina_api_key:
        try:
            return await _jina_embed(non_empty, task)
        except Exception as je:
            logger.error(f"Jina fallback failed: {je}")
    raise RuntimeError(
        "Τα embeddings δεν είναι διαθέσιμα — το τοπικό μοντέλο δεν φόρτωσε. "
        "Έλεγξε ότι το sentence-transformers είναι εγκατεστημένο και το HF_HOME είναι εγγράψιμο."
    )


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(x * y for x, y in zip(a[:n], b[:n]))
    norm_a = sum(x * x for x in a[:n]) ** 0.5
    norm_b = sum(y * y for y in b[:n]) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def warmup_embeddings_model() -> bool:
    """Προθέρμανση: φόρτωσε το μοντέλο στο πρώτο request (καλείται από lifespan)."""
    try:
        _load_local_model()
        return _model is not None
    except Exception:
        return False


async def search_similar_skus(
    query_text: str,
    sku_candidates: list[dict],
    top_k: int = 10,
    min_score: float = 0.3,
) -> list[dict]:
    """Semantic search over SKU candidates using embeddings."""
    if not sku_candidates:
        return []

    # Embed query
    query_vec = await embed_text(query_text, task="retrieval.query")

    # Score each candidate
    results = []
    for sku in sku_candidates:
        stored_vec = sku.get("name_embedding")
        if stored_vec:
            score = cosine_similarity(query_vec, stored_vec)
        else:
            # Fallback: embed commercial_name on-the-fly (slower, use batch for production)
            name_vec = await embed_text(sku.get("commercial_name", ""), task="retrieval.passage")
            score = cosine_similarity(query_vec, name_vec)

        if score >= min_score:
            results.append({**sku, "_score": round(score, 3)})

    results.sort(key=lambda x: x["_score"], reverse=True)
    return results[:top_k]


async def search_similar_sources(
    query_name: str,
    sources: list[dict],
    top_k: int = 5,
    min_score: float = 0.5,
) -> list[dict]:
    """Fuzzy match source names using embeddings."""
    if not sources or not query_name:
        return []

    query_vec = await embed_text(query_name, task="retrieval.query")

    results = []
    for src in sources:
        src_name = src.get("name", "")
        if not src_name:
            continue
        src_vec = await embed_text(src_name, task="retrieval.passage")
        score = cosine_similarity(query_vec, src_vec)
        if score >= min_score:
            results.append({**src, "_score": round(score, 3)})

    results.sort(key=lambda x: x["_score"], reverse=True)
    return results[:top_k]


async def rank_documents(query: str, docs: list[dict]) -> list[dict]:
    """Τοπικό reranking: cosine similarity query-vs-embedding (αντικαθιστά το Jina reranker).

    docs: λίστα με {"text": str, "embedding": list[float]} — επιστρέφει τα ίδια
    ταξινομημένα με "_score".
    """
    if not docs:
        return []
    query_vec = await embed_text(query, task="retrieval.query")
    scored = []
    for d in docs:
        emb = d.get("embedding") or []
        if not emb:
            continue
        scored.append((cosine_similarity(query_vec, emb), d))
    scored.sort(key=lambda x: -x[0])
    return [d for s, d in scored if s > 0.0][:8]
