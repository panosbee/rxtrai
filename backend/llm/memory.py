"""
RxTrail — Multi-Tier Memory System for AI Agent.

Memory Types (inspired by human cognitive architecture):
  1. WORKING MEMORY  — Current conversation (persisted to MongoDB)
  2. EPISODIC MEMORY — Past events, appointments, case transitions
  3. SEMANTIC MEMORY — Facts about the business (org-level knowledge)
  4. PROCEDURAL MEMORY — How-to workflows (future phase)
  5. VECTOR MEMORY   — Semantic search via Jina embeddings (future phase)

Proactive Logic:
  - On first interaction of the day, scan the system for today's appointments,
    pending cases, and important reminders
  - Greet the user with contextual information
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# ── Working Memory ────────────────────────────────────────────────────────────
# Persisted conversation history per org. Survives server restarts.

MAX_WORKING_MEMORY = 30  # Messages per org


async def get_working_memory(db: AsyncIOMotorDatabase, org_id: str) -> list[dict]:
    """Retrieve recent conversation history for an org."""
    doc = await db.agent_memory.find_one({"org_id": org_id, "type": "working"})
    if not doc:
        return []
    return doc.get("messages", [])[-MAX_WORKING_MEMORY:]


async def add_to_working_memory(
    db: AsyncIOMotorDatabase, org_id: str, role: str, content: str,
    metadata: Optional[dict] = None,
):
    """Append a message to working memory."""
    msg = {
        "role": role,
        "content": content,
        "at": datetime.now(timezone.utc),
    }
    if metadata:
        msg["meta"] = metadata

    await db.agent_memory.update_one(
        {"org_id": org_id, "type": "working"},
        {
            "$push": {"messages": msg},
            "$setOnInsert": {"org_id": org_id, "type": "working", "created_at": datetime.now(timezone.utc)},
        },
        upsert=True,
    )
    # Trim to max size
    doc = await db.agent_memory.find_one({"org_id": org_id, "type": "working"})
    if doc and len(doc.get("messages", [])) > MAX_WORKING_MEMORY:
        trimmed = doc["messages"][-MAX_WORKING_MEMORY:]
        await db.agent_memory.update_one(
            {"org_id": org_id, "type": "working"},
            {"$set": {"messages": trimmed}},
        )


async def clear_working_memory(db: AsyncIOMotorDatabase, org_id: str):
    """Clear conversation history (e.g., on logout or reset)."""
    await db.agent_memory.delete_one({"org_id": org_id, "type": "working"})


# ── Episodic Memory ──────────────────────────────────────────────────────────
# Significant events: appointments, case transitions, visits, prescriptions.

async def log_episode(
    db: AsyncIOMotorDatabase,
    org_id: str,
    event_type: str,  # "appointment", "case_update", "visit", "prescription", "note"
    summary: str,
    entities: Optional[list[dict]] = None,
    metadata: Optional[dict] = None,
):
    """Log a significant event to episodic memory."""
    episode = {
        "org_id": org_id,
        "type": "episodic",
        "event_type": event_type,
        "summary": summary,
        "entities": entities or [],
        "metadata": metadata or {},
        "at": datetime.now(timezone.utc),
    }
    await db.agent_memory.insert_one(episode)


async def get_recent_episodes(
    db: AsyncIOMotorDatabase,
    org_id: str,
    days: int = 30,
    event_type: Optional[str] = None,
    entity_name: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """Retrieve recent episodes, optionally filtered."""
    filt: dict = {"org_id": org_id, "type": "episodic"}
    if event_type:
        filt["event_type"] = event_type
    if entity_name:
        filt["entities.name"] = {"$regex": entity_name, "$options": "i"}

    since = datetime.now(timezone.utc) - timedelta(days=days)
    filt["at"] = {"$gte": since}

    cursor = db.agent_memory.find(filt).sort("at", -1).limit(limit)
    episodes = []
    async for ep in cursor:
        episodes.append({
            "event_type": ep.get("event_type"),
            "summary": ep.get("summary"),
            "at": ep.get("at").isoformat() if ep.get("at") else "",
            "entities": ep.get("entities", []),
        })
    return episodes


async def get_last_episode_for_doctor(
    db: AsyncIOMotorDatabase, org_id: str, doctor_name: str,
) -> Optional[dict]:
    """Get the most recent episode involving a specific doctor."""
    episodes = await get_recent_episodes(
        db, org_id, days=90, entity_name=doctor_name, limit=1,
    )
    return episodes[0] if episodes else None


# ── Semantic Memory ───────────────────────────────────────────────────────────
# Key-value facts about the organization, extracted or manually set.

async def set_fact(
    db: AsyncIOMotorDatabase, org_id: str, key: str, value: Any,
):
    """Store a semantic fact about the organization."""
    await db.agent_memory.update_one(
        {"org_id": org_id, "type": "semantic", "key": key},
        {"$set": {"value": value, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


async def get_fact(
    db: AsyncIOMotorDatabase, org_id: str, key: str,
) -> Optional[Any]:
    """Retrieve a semantic fact."""
    doc = await db.agent_memory.find_one(
        {"org_id": org_id, "type": "semantic", "key": key},
    )
    return doc.get("value") if doc else None


async def get_all_facts(
    db: AsyncIOMotorDatabase, org_id: str,
) -> dict[str, Any]:
    """Get all semantic facts for the org."""
    cursor = db.agent_memory.find({"org_id": org_id, "type": "semantic"})
    facts = {}
    async for doc in cursor:
        facts[doc["key"]] = doc.get("value")
    return facts


async def auto_extract_facts(db: AsyncIOMotorDatabase, org_id: str):
    """Auto-extract basic facts about the org from the database."""
    # Count entities
    doctor_count = await db.doctors.count_documents({"org_id": org_id})
    sku_count = await db.skus.count_documents({"org_id": org_id})
    structure_count = await db.structures.count_documents({"org_id": org_id})
    case_count = await db.cases.count_documents({"org_id": org_id})

    # Top categories
    pipeline = [
        {"$match": {"org_id": org_id, "active": True}},
        {"$group": {"_id": "$category", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 5},
    ]
    top_cats = []
    async for doc in db.skus.aggregate(pipeline):
        if doc["_id"]:
            top_cats.append(f"{doc['_id']} ({doc['count']})")

    # Top doctors by rx_count
    top_docs = []
    cursor = db.doctors.find({"org_id": org_id}).sort("rx_count_90d", -1).limit(3)
    async for d in cursor:
        top_docs.append(f"{d.get('name', '?')} ({d.get('specialty', '?')}, {d.get('rx_count_90d', 0)} Rx)")

    await set_fact(db, org_id, "entity_counts", {
        "doctors": doctor_count,
        "products": sku_count,
        "structures": structure_count,
        "cases": case_count,
    })
    await set_fact(db, org_id, "top_categories", top_cats)
    await set_fact(db, org_id, "top_doctors", top_docs)
    await set_fact(db, org_id, "last_fact_extraction", datetime.now(timezone.utc).isoformat())

    logger.info(f"Auto-extracted facts for org {org_id}: {doctor_count} doctors, {sku_count} SKUs")


# ── Proactive Context Builder ─────────────────────────────────────────────────
# Scans the system and builds a proactive greeting for the user.

async def build_proactive_context(
    db: AsyncIOMotorDatabase, org_id: str,
) -> str:
    """
    Build a proactive context string for the agent.
    Includes: today's appointments, pending cases, recent episodes, reminders.
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    parts = []

    # ── Today's appointments ──
    appt_cursor = db.appointments.find({
        "org_id": org_id,
        "at": {"$gte": today_start, "$lte": today_end + timedelta(days=1)},
    }).sort("at", 1).limit(10)

    appts = []
    async for a in appt_cursor:
        doctor_name = ""
        if a.get("doctor_id"):
            try:
                from bson import ObjectId
                d = await db.doctors.find_one({"_id": ObjectId(a["doctor_id"]), "org_id": org_id})
                if d:
                    doctor_name = d.get("name", "")
            except Exception:
                pass
        appt_time = a.get("at")
        if appt_time:
            time_str = appt_time.strftime("%H:%M") if hasattr(appt_time, 'strftime') else str(appt_time)[11:16]
            appts.append(f"  • {time_str} — {a.get('title', 'Ραντεβού')} με {doctor_name or '?'}")

    if appts:
        parts.append("ΣΗΜΕΡΙΝΑ ΡΑΝΤΕΒΟΥ:\n" + "\n".join(appts))
    else:
        parts.append("ΣΗΜΕΡΙΝΑ ΡΑΝΤΕΒΟΥ: Κανένα προγραμματισμένο.")

    # ── Pending / active cases ──
    active_cases = await db.cases.count_documents({"org_id": org_id, "status": "ACTIVE"})
    at_risk = await db.cases.count_documents({"org_id": org_id, "status": "AT_RISK"})
    pending = await db.cases.count_documents({"org_id": org_id, "status": "PENDING"})

    if active_cases or at_risk or pending:
        parts.append(f"ΕΚΚΡΕΜΗ ΠΕΡΙΣΤΑΤΙΚΑ: {active_cases} ενεργά, {at_risk} σε κίνδυνο, {pending} εκκρεμή")

    # ── Recent episodes (last 7 days) ──
    recent = await get_recent_episodes(db, org_id, days=7, limit=5)
    if recent:
        ep_lines = ["ΠΡΟΣΦΑΤΑ ΓΕΓΟΝΟΤΑ (7 ημέρες):"]
        for ep in recent:
            at_str = ep.get("at", "")[:10]
            ep_lines.append(f"  • {at_str} — [{ep.get('event_type', '?')}] {ep.get('summary', '')[:150]}")
        parts.append("\n".join(ep_lines))

    # ── Semantic facts ──
    facts = await get_all_facts(db, org_id)
    if facts:
        fact_lines = ["ΓΝΩΣΕΙΣ ΟΡΓΑΝΙΣΜΟΥ:"]
        entity_counts = facts.get("entity_counts", {})
        if entity_counts:
            fact_lines.append(f"  • {entity_counts.get('doctors', 0)} γιατροί, {entity_counts.get('products', 0)} προϊόντα, {entity_counts.get('structures', 0)} δομές, {entity_counts.get('cases', 0)} περιστατικά")
        top_cats = facts.get("top_categories", [])
        if top_cats:
            fact_lines.append(f"  • Κορυφαίες κατηγορίες: {', '.join(top_cats)}")
        top_docs = facts.get("top_doctors", [])
        if top_docs:
            fact_lines.append(f"  • Κορυφαίοι γιατροί: {', '.join(top_docs)}")
        parts.append("\n".join(fact_lines))

    # ── Reminders for today ──
    reminders = []
    if appts:
        reminders.append("Έχεις ραντεβού σήμερα — ρώτα με αν θες να προετοιμαστώ για κάποιον γιατρό.")
    if at_risk > 0:
        reminders.append(f"Έχεις {at_risk} περιστατικά σε κίνδυνο — θες να τα δούμε;")
    if pending > 0:
        reminders.append(f"Έχεις {pending} εκκρεμή περιστατικά.")

    if reminders:
        parts.append("ΥΠΕΝΘΥΜΙΣΕΙΣ:\n" + "\n".join(f"  • {r}" for r in reminders))

    return "\n\n".join(parts)


# ── Initialize memory collections ─────────────────────────────────────────────

async def init_memory_indexes(db: AsyncIOMotorDatabase):
    """Create indexes for the agent_memory collection."""
    await db.agent_memory.create_index([
        ("org_id", 1), ("type", 1),
    ])
    await db.agent_memory.create_index([
        ("org_id", 1), ("type", 1), ("at", -1),
    ])
    await db.agent_memory.create_index([
        ("org_id", 1), ("type", 1), ("event_type", 1),
    ])
    await db.agent_memory.create_index([
        ("org_id", 1), ("type", 1), ("key", 1),
    ], sparse=True)
    logger.info("Agent memory indexes created")
