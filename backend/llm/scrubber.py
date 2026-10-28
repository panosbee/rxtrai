"""
RxTrail — PII Scrubber: runs BEFORE every external LLM call.

Pipeline:
  1. Regex ΑΜΚΑ: \\b\\d{11}\\b → "[ΑΜΚΑ]"
  2. Regex dates near keywords (γενν, ετών, /19xx) → "[ΗΜ]"
  3. Regex phones (10-digit Greek numbers) → "[ΤΗΛ]"
  4. NER Greek names (rule-based v1, spaCy v2) → "[ΟΝΟΜΑ]" or "[ΑΣΘΕΝΗΣ]"
  5. Whitelist: names from doctors/contacts/sources DB NOT removed
  6. Blacklist: names from patients DB ALWAYS removed → "[ΑΣΘΕΝΗΣ]"

CRITICAL: Un-scrubbed text must NEVER leave the server to an external API.
"""
import re
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

# ── Regex patterns ───────────────────────────────────────────────────────────

# ΑΜΚΑ: exactly 11 digits, standalone
AMKA_RE = re.compile(r'\b\d{11}\b')

# Phones: Greek mobile (69xxxxxxxx) or landline (2xxxxxxxxx), 10 digits
PHONE_RE = re.compile(r'\b(?:69\d{8}|2\d{9})\b')

# Dates near keywords that might indicate birth date or personal dates
DATE_NEAR_KEYWORD_RE = re.compile(
    r'(γενν(?:ήθηκε|ηθείσα|ηθέντα)?|ετών?\s*\d{1,3}|γέννηση)\s*[:.]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
    re.IGNORECASE,
)

# Standalone dates (DD/MM/YYYY) that might be birth dates
DATE_STANDALONE_RE = re.compile(r'\b\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}\b')

# Greek name patterns (common first/last name endings)
GREEK_NAME_PATTERNS = [
    r'\b(?:[Α-Ωα-ω]{2,15})\s+(?:του\s+)?(?:[Α-Ωα-ω]{2,15})\b',  # Two-word names
]

# ── Public API ───────────────────────────────────────────────────────────────

async def scrub_text(
    text: str,
    db: AsyncIOMotorDatabase,
    org_id: str,
) -> str:
    """Scrub PII from text before sending to external API.

    Args:
        text: Raw text that may contain PII
        db: MongoDB database handle (for whitelist/blacklist lookup)
        org_id: Organization ID for scoping lookups

    Returns:
        Scrubbed text safe for external API
    """
    if not text or not text.strip():
        return text

    # ── Step 1: Regex scrubbing ─────────────────────────────────────────
    text = AMKA_RE.sub('[ΑΜΚΑ]', text)
    text = DATE_NEAR_KEYWORD_RE.sub('[ΗΜ]', text)

    # Scrub standalone dates near the beginning (likely birth dates)
    # Only if they appear isolated (not in a numeric context)
    text = DATE_STANDALONE_RE.sub('[ΗΜ]', text)

    # ── Step 2: Phone scrubbing ─────────────────────────────────────────
    text = PHONE_RE.sub('[ΤΗΛ]', text)

    # ── Step 3: Name scrubbing (rule-based) ─────────────────────────────
    # Load whitelist (doctors, contacts, sources names)
    whitelist = await _load_whitelist(db, org_id)

    # Load blacklist (patient names)
    blacklist = await _load_blacklist(db, org_id)

    # Find potential Greek names (capitalized two-word sequences)
    name_pattern = re.compile(
        r'\b([Α-Ω][α-ω]{2,20})\s+([Α-Ω][α-ω]{2,20})\b'
    )
    matches = list(name_pattern.finditer(text))

    # Process from end to start to preserve indices
    for match in reversed(matches):
        full_name = match.group(0)
        # Check blacklist first
        if _matches_list(full_name, blacklist):
            text = text[:match.start()] + '[ΑΣΘΕΝΗΣ]' + text[match.end():]
            continue
        # Skip whitelisted names (doctors, contacts, sources)
        if _matches_list(full_name, whitelist):
            continue
        # Conservative: scrub unknown names
        text = text[:match.start()] + '[ΟΝΟΜΑ]' + text[match.end():]

    return text


def scrub_text_sync(text: str) -> str:
    """Synchronous, stateless scrubber for quick use (no DB lookup).
    Only does regex scrubbing — no name removal (safe for internal use).
    """
    if not text:
        return text
    text = AMKA_RE.sub('[ΑΜΚΑ]', text)
    text = DATE_NEAR_KEYWORD_RE.sub('[ΗΜ]', text)
    text = PHONE_RE.sub('[ΤΗΛ]', text)
    return text


# ── Internal helpers ─────────────────────────────────────────────────────────

async def _load_whitelist(db: AsyncIOMotorDatabase, org_id: str) -> set[str]:
    """Load whitelisted names from doctors, contacts, sources."""
    names: set[str] = set()
    collections = [db.doctors, db.contacts, db.sources]
    for coll in collections:
        async for doc in coll.find({"org_id": org_id}, {"name": 1}):
            name = doc.get("name", "").strip()
            if name:
                names.add(name.lower())
    return names


async def _load_blacklist(db: AsyncIOMotorDatabase, org_id: str) -> set[str]:
    """Load blacklisted names from patients."""
    names: set[str] = set()
    async for doc in db.patients.find({"org_id": org_id, "erased": False}, {"name": 1}):
        name = doc.get("name", "").strip()
        if name and name != "[ERASED]":
            names.add(name.lower())
    return names


def _matches_list(full_name: str, name_set: set[str]) -> bool:
    """Check if a full name matches any entry in the set.
    Handles partial matches (e.g., 'Γιώργος Παπαδάκης' matches 'Παπαδάκης').
    """
    lower = full_name.lower()
    if lower in name_set:
        return True
    # Check individual words
    parts = lower.split()
    for part in parts:
        if part in name_set:
            return True
    return False
