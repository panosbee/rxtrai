"""
RxTrail — Audit log writer.

Called by the state machine (and manually for critical mutations) to record
who did what, when, with before/after snapshots.
"""
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase


async def write_audit(
    db: AsyncIOMotorDatabase,
    org_id: str,
    user_id: str,
    action: str,
    collection: str,
    doc_id: str,
    before: dict | None = None,
    after: dict | None = None,
):
    """Write a single audit log entry."""
    await db.audit_log.insert_one({
        "org_id": org_id,
        "user_id": user_id,
        "action": action,
        "collection": collection,
        "doc_id": doc_id,
        "before": before,
        "after": after,
        "at": datetime.now(timezone.utc),
    })
