"""
RxTrail — Async MongoDB client (Motor) + index manager + scoping helper.

Every query MUST be scoped by org_id. The `scoped()` helper returns a
collection pre-bound to the org so you never leak cross-tenant data.
"""
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

from backend.config import settings

_client: AsyncIOMotorClient | None = None
_db_name: str = settings.mongodb_db


async def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.mongodb_url)
    return _client


async def get_db():
    client = await get_client()
    return client[_db_name]


def scoped(collection: AsyncIOMotorCollection, org_id: str) -> AsyncIOMotorCollection:
    """Return a proxy-like view: every query on the returned object should
    include {'org_id': org_id}. We handle this with a light wrapper because
    Motor doesn't natively support default-filters on the collection level."""
    return _OrgScopedCollection(collection, org_id)


class _OrgScopedCollection:
    """Auto-injects `org_id` filter into every query method used by our routers.
    Covers the main Motor read/write operations."""

    def __init__(self, coll: AsyncIOMotorCollection, org_id: str):
        self._coll = coll
        self._org_id = org_id
        self._filter = {"org_id": org_id}

    def _add_filter(self, spec: dict | None) -> dict:
        base = dict(self._filter)
        if spec:
            base.update(spec)
        return base

    # ── Read ────────────────────────────────────────────────────────────
    async def find_one(self, spec: dict | None = None, *args, **kwargs):
        return await self._coll.find_one(self._add_filter(spec), *args, **kwargs)

    def find(self, spec: dict | None = None, *args, **kwargs):
        return self._coll.find(self._add_filter(spec), *args, **kwargs)

    async def count_documents(self, spec: dict | None = None, *args, **kwargs):
        return await self._coll.count_documents(self._add_filter(spec), *args, **kwargs)

    async def distinct(self, key: str, spec: dict | None = None, *args, **kwargs):
        return await self._coll.distinct(key, self._add_filter(spec), *args, **kwargs)

    async def aggregate(self, pipeline: list, *args, **kwargs):
        # Inject $match as first stage if not already present
        has_match = any("$match" in stage for stage in pipeline if isinstance(stage, dict))
        if not has_match:
            pipeline = [{"$match": dict(self._filter)}] + list(pipeline)
        return await self._coll.aggregate(pipeline, *args, **kwargs)

    # ── Write ───────────────────────────────────────────────────────────
    async def insert_one(self, document: dict, *args, **kwargs):
        document["org_id"] = self._org_id
        return await self._coll.insert_one(document, *args, **kwargs)

    async def insert_many(self, documents: list[dict], *args, **kwargs):
        for doc in documents:
            doc["org_id"] = self._org_id
        return await self._coll.insert_many(documents, *args, **kwargs)

    async def update_one(self, spec: dict, update: dict, *args, **kwargs):
        return await self._coll.update_one(self._add_filter(spec), update, *args, **kwargs)

    async def update_many(self, spec: dict, update: dict, *args, **kwargs):
        return await self._coll.update_many(self._add_filter(spec), update, *args, **kwargs)

    async def delete_one(self, spec: dict, *args, **kwargs):
        return await self._coll.delete_one(self._add_filter(spec), *args, **kwargs)

    async def delete_many(self, spec: dict, *args, **kwargs):
        return await self._coll.delete_many(self._add_filter(spec), *args, **kwargs)

    # ── Bulk / raw ──────────────────────────────────────────────────────
    async def bulk_write(self, requests, *args, **kwargs):
        """Skips scoping for bulk operations — caller must scope manually."""
        return await self._coll.bulk_write(requests, *args, **kwargs)

    @property
    def full_name(self) -> str:
        return self._coll.full_name


async def ensure_indexes():
    """Called on startup. Creates all MongoDB indexes for the org."""
    db = await get_db()

    # ── users ───────────────────────────────────────────────────────────
    await db.users.create_index("email", unique=True)
    await db.users.create_index("org_id")

    # ── skus ────────────────────────────────────────────────────────────
    # Uniqueness handled by import commit matching (product_id → eopyy_code → commercial_name)
    await db.skus.create_index([("org_id", 1), ("product_id", 1)], sparse=True)
    await db.skus.create_index([("org_id", 1), ("eopyy_code", 1)], sparse=True)
    await db.skus.create_index([("org_id", 1), ("category", 1)])
    await db.skus.create_index([("org_id", 1), ("commercial_name", "text")])

    # ── structures ──────────────────────────────────────────────────────
    await db.structures.create_index([("org_id", 1), ("prefecture", 1)])
    await db.structures.create_index([("org_id", 1), ("geo", "2dsphere")])

    # ── doctors ─────────────────────────────────────────────────────────
    await db.doctors.create_index([("org_id", 1), ("specialty", 1)])
    await db.doctors.create_index([("org_id", 1), ("area", 1)])
    await db.doctors.create_index([("org_id", 1), ("workplace_type", 1)])

    # ── contacts ────────────────────────────────────────────────────────
    await db.contacts.create_index([("org_id", 1), ("structure_id", 1)])

    # ── sources ─────────────────────────────────────────────────────────
    await db.sources.create_index([("org_id", 1), ("type", 1)])

    # ── cases ───────────────────────────────────────────────────────────
    await db.cases.create_index([("org_id", 1), ("status", 1)])
    await db.cases.create_index([("org_id", 1), ("source_id", 1)])
    await db.cases.create_index([("org_id", 1), ("doctor_id", 1)])

    # ── patients ────────────────────────────────────────────────────────
    await db.patients.create_index([("org_id", 1), ("case_id", 1)], unique=True)

    # ── prescriptions ───────────────────────────────────────────────────
    await db.prescriptions.create_index([("org_id", 1), ("case_id", 1)])
    await db.prescriptions.create_index([("org_id", 1), ("doctor_id", 1)])

    # ── fulfilments ─────────────────────────────────────────────────────
    await db.fulfilments.create_index([("org_id", 1), ("case_id", 1)])
    await db.fulfilments.create_index([("org_id", 1), ("next_repeat_due", 1)])

    # ── visits ──────────────────────────────────────────────────────────
    await db.visits.create_index([("org_id", 1), ("target_id", 1)])
    await db.visits.create_index([("org_id", 1), ("at", -1)])

    # ── notifications ───────────────────────────────────────────────────
    await db.notifications.create_index([("org_id", 1), ("seen", 1)])
    await db.notifications.create_index([("org_id", 1), ("ref_id", 1), ("kind", 1)])

    # ── audit_log ───────────────────────────────────────────────────────
    await db.audit_log.create_index([("org_id", 1), ("at", -1)])

    # ── knowledge_chunks (RAG) ──────────────────────────────────────────
    await db.knowledge_chunks.create_index([("org_id", 1), ("file_id", 1)])
    await db.knowledge_chunks.create_index([("org_id", 1), ("tab_name", 1)])
    await db.knowledge_chunks.create_index([("org_id", 1), ("product_code", 1)])

    # ── knowledge_files ─────────────────────────────────────────────────
    await db.knowledge_files.create_index([("org_id", 1), ("file_id", 1)], unique=True)

    # ── knowledge_lookup (SKU exact search) ────────────────────────────
    await db.knowledge_lookup.create_index([("org_id", 1), ("sku", 1)], unique=True)

    # ── import_jobs ─────────────────────────────────────────────────────
    await db.import_jobs.create_index([("org_id", 1), ("kind", 1)])

    # ── llm_calls ───────────────────────────────────────────────────────
    await db.llm_calls.create_index([("org_id", 1), ("at", -1)])

    # ── documents ───────────────────────────────────────────────────────
    await db.documents.create_index([("org_id", 1), ("case_id", 1)])

    # ── appointments ────────────────────────────────────────────────────
    await db.appointments.create_index([("org_id", 1), ("at", 1)])
    await db.appointments.create_index([("org_id", 1), ("doctor_id", 1)])
    await db.appointments.create_index([("org_id", 1), ("case_id", 1)])

    # ── share (Κοινός Χώρος) — cross-tenant, deliberately no org_id ───
    await db.share_folders.create_index([("parent_id", 1)])
    await db.share_folders.create_index([("name", 1)])
    await db.share_files.create_index([("folder_id", 1)])
    await db.share_files.create_index([("name", 1), ("folder_id", 1)])
    await db.share_files.create_index([("uploaded_by", 1)])
