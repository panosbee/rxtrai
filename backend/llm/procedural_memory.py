"""
RxTrail — Procedural Memory: Workflow Templates.

Phase 2 of the Multi-Tier Memory System.

Stores "how-to" knowledge as structured procedures:
  - How to process an EOPYY prescription
  - How to handle a new patient referral
  - How to prepare for a doctor visit
  - Sales playbooks per specialty

Procedures can be:
  - System-defined (built-in templates)
  - User-defined (created via agent conversation)
  - Triggered by context (e.g., when user asks "how do I...")

Storage: MongoDB collection `agent_procedures`
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# ── Built-in Procedure Templates ──────────────────────────────────────────────

BUILTIN_PROCEDURES = [
    {
        "name": "Προετοιμασία επίσκεψης σε γιατρό",
        "triggers": ["επίσκεψη", "ραντεβού με γιατρό", "πάω σε", "θα δω τον"],
        "steps": [
            "1. Βρες τον γιατρό στο σύστημα (search_doctors)",
            "2. Διάβασε τις ΣΗΜΕΙΩΣΕΙΣ του — χαρακτήρας, προτιμήσεις, ιστορικό",
            "3. Δες τα ΣΤΑΤΙΣΤΙΚΑ του (rx_count, open_cases) — πόσο ενεργός είναι;",
            "4. Πάρε ΠΡΟΤΕΙΝΟΜΕΝΑ ΠΡΟΪΟΝΤΑ βάσει ειδικότητας (recommend_products_for_doctor)",
            "5. Αν έχει σημειώσεις για επιστημονικό προφίλ → ψάξε ΕΡΕΥΝΕΣ/studies (web_search)",
            "6. Δες ΤΙ ΕΓΙΝΕ ΤΗΝ ΠΡΟΗΓΟΥΜΕΝΗ ΦΟΡΑ (search_memory για episodes με αυτόν τον γιατρό)",
            "7. Χτίσε ΣΤΡΑΤΗΓΙΚΗ: τι να πεις, ποια προϊόντα να προτείνεις, τι να αποφύγεις",
            "8. Ρώτα τον χρήστη αν θέλει να κλείσει ραντεβού ή να σημειώσει κάτι",
        ],
    },
    {
        "name": "Διαχείριση νέου περιστατικού",
        "triggers": ["νέο περιστατικό", "καινούργιο περιστατικό", "new case", "νέα συνταγή"],
        "steps": [
            "1. Συγκέντρωσε στοιχεία ασθενούς (ΑΜΚΑ, όνομα, πάθηση)",
            "2. Ταυτοποίησε τον γιατρό και την δομή",
            "3. Έλεγξε αν υπάρχει ενεργό περιστατικό για τον ίδιο ασθενή",
            "4. Δημιούργησε το περιστατικό στο σύστημα",
            "5. Ανέβασε τη συνταγή (φωτογραφία ή εισαγωγή στοιχείων)",
            "6. Όρισε προθεσμία εκτέλεσης και reminders",
        ],
    },
    {
        "name": "Follow-up μετά από συνταγογράφηση",
        "triggers": ["follow-up", "followup", "επανεξέταση", "επόμενο ραντεβού"],
        "steps": [
            "1. Έλεγξε την κατάσταση της συνταγής (εκτελέστηκε; εκκρεμεί;)",
            "2. Δες αν ο ασθενής παρέλαβε τα προϊόντα",
            "3. Προγραμμάτισε το επόμενο ραντεβού (βάσει duration_days ή max_qty_month)",
            "4. Ενημέρωσε τον γιατρό αν χρειάζεται ανανέωση",
            "5. Καταχώρησε σημειώσεις από την επικοινωνία",
        ],
    },
    {
        "name": "Προσέγγιση νέου γιατρού (cold approach)",
        "triggers": ["νέος γιατρός", "καινούργιος γιατρός", "πρώτη επαφή", "cold"],
        "steps": [
            "1. Ερεύνησε τον γιατρό (web_search για το όνομά του + ειδικότητα)",
            "2. Δες την δομή που ανήκει — τι προϊόντα κυκλοφορούν εκεί;",
            "3. Βρες παρόμοιους γιατρούς που ήδη συνεργάζεσαι (get_similar_doctors)",
            "4. Ετοίμασε 2-3 προϊόντα-κλειδιά για την ειδικότητά του",
            "5. Ετοίμασε υλικό: studies, φυλλάδια, τιμοκατάλογο",
            "6. Πρότεινε πλάνο πρώτης επίσκεψης: τι να πεις, τι να αφήσεις",
        ],
    },
]

# ── CRUD Operations ───────────────────────────────────────────────────────────

async def init_builtin_procedures(db: AsyncIOMotorDatabase, org_id: str):
    """Seed built-in procedure templates for an org (idempotent)."""
    for proc in BUILTIN_PROCEDURES:
        existing = await db.agent_procedures.find_one({
            "org_id": org_id,
            "name": proc["name"],
        })
        if not existing:
            await db.agent_procedures.insert_one({
                "org_id": org_id,
                "name": proc["name"],
                "triggers": proc["triggers"],
                "steps": proc["steps"],
                "is_builtin": True,
                "created_at": datetime.now(timezone.utc),
            })
    logger.info(f"Seeded {len(BUILTIN_PROCEDURES)} built-in procedures for org {org_id}")


async def find_procedure_by_trigger(
    db: AsyncIOMotorDatabase, org_id: str, query: str,
) -> Optional[dict]:
    """Find a procedure matching the user's query via trigger keywords."""
    query_lower = query.lower()
    
    cursor = db.agent_procedures.find({"org_id": org_id})
    async for proc in cursor:
        for trigger in proc.get("triggers", []):
            if trigger.lower() in query_lower:
                return {
                    "name": proc["name"],
                    "steps": proc["steps"],
                    "matched_trigger": trigger,
                }
    return None


async def get_procedure_by_name(
    db: AsyncIOMotorDatabase, org_id: str, name: str,
) -> Optional[dict]:
    """Get a specific procedure by name."""
    proc = await db.agent_procedures.find_one({
        "org_id": org_id,
        "name": {"$regex": name, "$options": "i"},
    })
    if proc:
        return {
            "name": proc["name"],
            "steps": proc["steps"],
            "triggers": proc.get("triggers", []),
        }
    return None


async def list_all_procedures(
    db: AsyncIOMotorDatabase, org_id: str,
) -> list[dict]:
    """List all available procedures."""
    cursor = db.agent_procedures.find({"org_id": org_id})
    procedures = []
    async for proc in cursor:
        procedures.append({
            "name": proc["name"],
            "triggers": proc.get("triggers", []),
            "step_count": len(proc.get("steps", [])),
            "is_builtin": proc.get("is_builtin", False),
        })
    return procedures


async def create_procedure(
    db: AsyncIOMotorDatabase, org_id: str,
    name: str, steps: list[str], triggers: list[str] = None,
) -> dict:
    """Create a custom procedure."""
    proc = {
        "org_id": org_id,
        "name": name,
        "triggers": triggers or [],
        "steps": steps,
        "is_builtin": False,
        "created_at": datetime.now(timezone.utc),
    }
    result = await db.agent_procedures.insert_one(proc)
    return {"id": str(result.inserted_id), "name": name, "steps": len(steps)}


# ── Indexes ───────────────────────────────────────────────────────────────────

async def init_procedural_indexes(db: AsyncIOMotorDatabase):
    """Create indexes for agent_procedures collection."""
    await db.agent_procedures.create_index([("org_id", 1), ("name", 1)], unique=True)
    await db.agent_procedures.create_index([("org_id", 1), ("triggers", 1)])
    logger.info("Procedural memory indexes created")
