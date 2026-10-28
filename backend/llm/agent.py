"""
RxTrail — AI Agent: Natural Language → System Actions via DeepSeek Function Calling.

The agent has access to:
  - Calendar: create/update/delete/list appointments, check conflicts
  - Cases: search, get detail, transition
  - Doctors: search, get detail
  - Web search: Brave Search API
  - System: dashboard stats, weekly truth

User speaks Greek, agent translates to function calls, executes, returns results.
User ALWAYS confirms before writes (guardrail).
"""
import json
import httpx
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.config import settings
from backend.core.clocks import ensure_utc

# ── Session memory (MongoDB-backed, persistent per org) ───────────────────────
MAX_MEMORY = 20

async def _get_memory(db: AsyncIOMotorDatabase, org_id: str) -> list[dict]:
    """Retrieve conversation history from MongoDB (persistent)."""
    doc = await db.agent_memory.find_one({"org_id": org_id, "type": "working"})
    if not doc:
        return []
    return doc.get("messages", [])[-MAX_MEMORY:]


async def _get_conversation_summary(db: AsyncIOMotorDatabase, org_id: str) -> str:
    """Η συμπυκνωμένη μνήμη παλιότερων συνομιλιών (δεν χάνεται τίποτα)."""
    doc = await db.agent_memory.find_one({"org_id": org_id, "type": "convo_summary"})
    return (doc or {}).get("summary", "")


async def _summarize_memory(db: AsyncIOMotorDatabase, org_id: str, messages: list[dict]):
    """Συμπυκνώνει τα παλιά μηνύματα σε μία σύνοψη (background — δεν μπλοκάρει)."""
    try:
        from backend.llm.client import simple_call
        old = "\n".join(f"{m['role']}: {m['content'][:400]}" for m in messages[-10:])
        existing = await _get_conversation_summary(db, org_id)
        prompt = (
            "Συμπύκνωσε την παρακάτω συνομιλία CRM σε 3-5 γραμμές στα ελληνικά, "
            "κρατώντας ΜΟΝΟ γεγονότα/προτιμήσεις/αποφάσεις που θα χρειαστούν στο μέλλον.\n\n"
            f"ΠΡΟΗΓΟΥΜΕΝΗ ΣΥΝΟΨΗ:\n{existing or '(καμία)'}\n\n"
            f"ΝΕΑ ΜΗΝΥΜΑΤΑ:\n{old}\n\nΣΥΝΟΨΗ:"
        )
        summary = (await simple_call(prompt, temperature=0.2, max_tokens=400)).strip()
        await db.agent_memory.update_one(
            {"org_id": org_id, "type": "convo_summary"},
            {"$set": {"org_id": org_id, "type": "convo_summary",
                      "summary": summary, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
    except Exception:
        pass


async def _add_to_memory(db: AsyncIOMotorDatabase, org_id: str, role: str, content: str):
    """Append message to conversation history in MongoDB."""
    msg = {"role": role, "content": content, "at": datetime.now(timezone.utc)}
    await db.agent_memory.update_one(
        {"org_id": org_id, "type": "working"},
        {"$push": {"messages": msg},
         "$setOnInsert": {"org_id": org_id, "type": "working", "created_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    # Trim — τα παλιά πηγαίνουν σε συμπύκνωση (δεν χάνονται)
    doc = await db.agent_memory.find_one({"org_id": org_id, "type": "working"})
    if doc and len(doc.get("messages", [])) > MAX_MEMORY:
        overflow = doc["messages"][:-MAX_MEMORY]
        trimmed = doc["messages"][-MAX_MEMORY:]
        await db.agent_memory.update_one(
            {"org_id": org_id, "type": "working"}, {"$set": {"messages": trimmed}})
        if overflow:
            import asyncio as _asyncio
            _asyncio.ensure_future(_summarize_memory(db, org_id, overflow)) 

# ── Function definitions (OpenAI-compatible) ─────────────────────────────────

AGENT_FUNCTIONS = [
    # ── Appointment ──
    {
        "name": "create_appointment",
        "description": "Δημιουργεί νέο ραντεβού στο ημερολόγιο. Ελέγχει αυτόματα για conflicts.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Τίτλος ραντεβού"},
                "at": {"type": "string", "description": "Ημερομηνία & ώρα (ISO format)"},
                "end_at": {"type": "string", "description": "Τέλος (ISO format, προαιρετικό)"},
                "kind": {"type": "string", "enum": ["DOCTOR", "CASE", "PERSONAL", "OTHER"]},
                "doctor_id": {"type": "string", "description": "ID γιατρού (αν kind=DOCTOR)"},
                "case_id": {"type": "string", "description": "ID περιστατικού (αν kind=CASE)"},
                "location": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["title", "at", "kind"],
        },
    },
    {"name": "check_conflicts", "description": "Ελέγχει αν υπάρχουν conflicts για συγκεκριμένη ώρα/γιατρό.",
     "parameters": {"type": "object", "properties": {"at": {"type": "string"}, "end_at": {"type": "string"}, "doctor_id": {"type": "string"}}, "required": ["at"]}},
    {"name": "list_appointments", "description": "Λίστα ραντεβού σε εύρος ημερομηνιών.",
     "parameters": {"type": "object", "properties": {"from_date": {"type": "string"}, "to_date": {"type": "string"}, "doctor_id": {"type": "string"}}}},
    # ── Cases ──
    {"name": "search_cases", "description": "Αναζήτηση περιστατικών με κείμενο ή φίλτρα.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "status": {"type": "string"}}}},
    {"name": "get_case_detail", "description": "Λεπτομέρειες ενός περιστατικού (κατάσταση, γιατρός, ανάγκες, αξία, ιστορικό).",
     "parameters": {"type": "object", "properties": {"case_id": {"type": "string"}}, "required": ["case_id"]}},
    {"name": "create_case", "description": "Δημιουργεί νέο περιστατικό (intake). Η πηγή είναι ΠΡΟΑΙΡΕΤΙΚΗ.",
     "parameters": {"type": "object", "properties": {
         "alias": {"type": "string", "description": "Ψευδώνυμο (π.χ. 'Παππούς Γάζι — κατάκλιση')"},
         "source_id": {"type": "string", "description": "ID πηγής σύστασης (προαιρετικό)"},
         "area": {"type": "string", "description": "Περιοχή"},
         "amka": {"type": "string", "description": "ΑΜΚΑ ασθενούς (προαιρετικό)"},
         "need_categories": {"type": "array", "items": {"type": "string"}, "description": "Κατηγορίες αναγκών"},
         "needs_freehand": {"type": "string", "description": "Ελεύθερη περιγραφή αναγκών"},
     }, "required": ["alias"]}},
    {"name": "transition_case", "description": "Αλλάζει κατάσταση περιστατικού (assess, to_doctor, prescribe, fulfil, lose, close, reactivate).",
     "parameters": {"type": "object", "properties": {
         "case_id": {"type": "string", "description": "ID περιστατικού"},
         "action": {"type": "string", "enum": ["assess", "to_doctor", "prescribe", "fulfil", "lose", "close", "reactivate", "reassign"]},
         "doctor_id": {"type": "string", "description": "ID γιατρού (για to_doctor)"},
         "need_categories": {"type": "array", "items": {"type": "string"}},
         "est_monthly_value": {"type": "number"},
         "lost_reason": {"type": "string", "enum": ["WENT_TO_COMPETITOR", "NOT_EXECUTED", "NO_ANSWER", "PATIENT_DECLINED", "MEDICAL_CHANGE", "PRICE", "SERVICE", "DOCTOR", "OTHER"]},
         "closed_reason": {"type": "string", "enum": ["DECEASED", "RECOVERED"]},
     }, "required": ["case_id", "action"]}},
    {"name": "repeat_fulfilment", "description": "One-tap επανάληψη: αντιγράφει την τελευταία εκτέλεση, reset ρολόι.",
     "parameters": {"type": "object", "properties": {"case_id": {"type": "string"}}, "required": ["case_id"]}},
    # ── Doctors ──
    {"name": "search_doctors", "description": "Αναζήτηση γιατρών με όνομα, ειδικότητα ή περιοχή.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}},
    {"name": "create_doctor", "description": "Δημιουργεί νέο γιατρό. ΜΟΝΟ το όνομα είναι υποχρεωτικό.",
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Ονοματεπώνυμο (ΥΠΟΧΡΕΩΤΙΚΟ)"},
         "specialty": {"type": "string", "description": "Ειδικότητα"},
         "workplace_type": {"type": "string", "enum": ["HOSPITAL", "PRIVATE_CLINIC", "FREELANCER", "HEALTH_CENTER", "NURSING_HOME", "OTHER"]},
         "phone": {"type": "string"}, "email": {"type": "string"},
         "address": {"type": "string"}, "area": {"type": "string"}, "notes": {"type": "string"},
         "product_categories": {"type": "array", "items": {"type": "string"}, "description": "Κατηγορίες προϊόντων που συνταγογραφεί"},
     }, "required": ["name"]}},
    {"name": "associate_doctor_structure", "description": "Συσχετίζει γιατρό με δομή υγείας. Κάνει fuzzy search στο όνομα δομής.",
     "parameters": {"type": "object", "properties": {
         "doctor_name": {"type": "string", "description": "Όνομα ή μέρος ονόματος γιατρού"},
         "structure_name": {"type": "string", "description": "Όνομα δομής (π.χ. 'Βενιζέλειο')"},
     }, "required": ["doctor_name", "structure_name"]}},
    {"name": "get_doctor_context", "description": "Πλήρες προφίλ υγειονομικού προσωπικού: ειδικότητα/ρόλος, δομές, στατιστικά, προτεινόμενα προϊόντα.",
     "parameters": {"type": "object", "properties": {"doctor_name": {"type": "string"}}, "required": ["doctor_name"]}},
    # ── Structures ──
    {"name": "create_structure", "description": "Δημιουργεί νέα δομή υγείας (νοσοκομείο, κλινική, κέντρο υγείας).",
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Όνομα δομής (ΥΠΟΧΡΕΩΤΙΚΟ)"},
         "type": {"type": "string", "enum": ["PUBLIC_HOSP", "PRIVATE_CLINIC", "REHAB", "ELDERLY_HOME", "DIALYSIS", "PHARMACY", "HOMECARE_ORG", "PRACTICE", "OTHER"]},
         "city": {"type": "string"}, "prefecture": {"type": "string"}, "address": {"type": "string"}, "phone": {"type": "string"},
     }, "required": ["name"]}},
    {"name": "search_structures", "description": "Αναζήτηση δομών υγείας. Βρες δομή για να συσχετίσεις με γιατρό.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Όνομα, πόλη ή νομός"}}}},
    # ── SKU / Products ──
    {"name": "search_products", "description": "Αναζήτηση προϊόντων (SKU) στον κατάλογο.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "category": {"type": "string"}}}},
    {"name": "create_sku", "description": "Δημιουργεί νέο προϊόν (SKU) στον κατάλογο.",
     "parameters": {"type": "object", "properties": {
         "eopyy_code": {"type": "string", "description": "Κωδικός ΕΟΠΥΥ"},
         "commercial_name": {"type": "string", "description": "Εμπορική ονομασία (ΥΠΟΧΡΕΩΤΙΚΟ)"},
         "category": {"type": "string"}, "reimb_price": {"type": "number"},
         "description": {"type": "string"},
     }, "required": ["commercial_name"]}},
    {"name": "recommend_products_for_doctor", "description": "Προτείνει προϊόντα για γιατρό βάσει ειδικότητας.",
     "parameters": {"type": "object", "properties": {"doctor_name": {"type": "string"}}, "required": ["doctor_name"]}},
    # ── Visits ──
    {"name": "create_visit", "description": "Καταγράφει νέα επίσκεψη σε γιατρό ή δομή.",
     "parameters": {"type": "object", "properties": {
         "target_type": {"type": "string", "enum": ["DOCTOR", "CONTACT", "STRUCTURE"], "description": "DOCTOR / CONTACT / STRUCTURE"},
         "target_id": {"type": "string", "description": "ID του target"},
         "outcome": {"type": "string", "enum": ["POSITIVE", "NEUTRAL", "FRIDGE", "PRESCRIBED"]},
         "channel": {"type": "string", "enum": ["IN_PERSON", "PHONE", "CONGRESS"]},
         "voice_note_text": {"type": "string", "description": "Σημειώσεις φωνητικής καταγραφής"},
     }, "required": ["target_type", "target_id"]}},
    # ── Week Plan ──
    {"name": "write_week_plan", "description": "Δημιουργεί και κλειδώνει το εβδομαδιαίο πλάνο με βάση την ουρά και τη γεωγραφία.",
     "parameters": {"type": "object", "properties": {
         "comment": {"type": "string", "description": "Προαιρετικό σχόλιο για το πλάνο"},
     }}},
    # ── Sources ──
    {"name": "list_sources", "description": "Λίστα όλων των πηγών σύστασης. Χρήσιμο πριν δημιουργήσεις περιστατικό.",
     "parameters": {"type": "object", "properties": {}}},
    # ── Web & Dashboard ──
    {"name": "update_doctor", "description": "Enhmerwnei ta stoixeia enos giatrou.", "parameters": {"type": "object", "properties": {"doctor_id": {"type": "string"}, "name": {"type": "string"}, "specialty": {"type": "string"}, "phone": {"type": "string"}, "email": {"type": "string"}, "area": {"type": "string"}, "notes": {"type": "string"}}, "required": ["doctor_id"]}},
    {"name": "delete_appointment", "description": "Diagrafei ena ranteboy.", "parameters": {"type": "object", "properties": {"appointment_id": {"type": "string"}}, "required": ["appointment_id"]}},
    {"name": "update_appointment", "description": "Enhmerwnei ena ranteboy.", "parameters": {"type": "object", "properties": {"appointment_id": {"type": "string"}, "title": {"type": "string"}, "at": {"type": "string"}, "notes": {"type": "string"}}, "required": ["appointment_id"]}},
    {"name": "web_search", "description": "Αναζήτηση στο web μέσω Brave Search.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "get_dashboard", "description": "Στατιστικά ταμπλό: ενεργά, σε κίνδυνο, νέα μήνα, τζίρος.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "get_proactive_insights", "description": "Αναλύει τα δεδομένα και βγάζει προληπτικές προτάσεις: γιατροί χωρίς επαφή, επαναλήψεις που λήγουν, περιστατικά που κρυώνουν.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "get_doctor_strategy", "description": "Διαβάζει το προφίλ + σημειώσεις ενός γιατρού και προτείνει στρατηγική προσέγγισης: τι να πεις, ποια προϊόντα να τονίσεις, πώς να τον προσεγγίσεις με βάση τα ενδιαφέροντά του.",
     "parameters": {"type": "object", "properties": {"doctor_name": {"type": "string", "description": "Όνομα γιατρού"}}, "required": ["doctor_name"]}},
]

AGENT_SYSTEM_PROMPT = """Είσαι ο Ασκληπιός, ο προσωπικός βοηθός του χρήστη στο RxTrail CRM — CRM για πάροχο ιατροτεχνολογικών στην Ελλάδα.

ΠΟΙΟΣ ΕΙΣΑΙ:
- Είσαι έξυπνος, ζεστός, ανθρώπινος. Μιλάς ελληνικά φυσικά, όπως μιλάει ένας έμπειρος συνεργάτης.
- Έχεις πλήρη πρόσβαση στο σύστημα του χρήστη: γιατρούς, δομές, SKU, πηγές, περιστατικά, ραντεβού, σημειώσεις, βάση γνώσης ΕΟΠΥΥ.
- Θυμάσαι τι συζητήσατε. Κρατάς σημαντικά πράγματα στη μνήμη σου.
- Ξέρεις τι ώρα και τι μέρα είναι.

ΠΩΣ ΜΙΛΑΣ:
- Φυσικά, ζεστά, με ενδιαφέρον. Όχι σαν ρομπότ, όχι με τεχνική γλώσσα.
- Αν σε ρωτήσουν κάτι που δεν ξέρεις: ΠΕΣ ΤΟ καθαρά, κάνε 1-2 έξυπνες ερωτήσεις για να καταλάβεις, και δώσε ό,τι βοήθεια μπορείς.
- Αν σε ρωτήσουν για πρόσωπα/προϊόντα: ψάξε ΟΠΟΥ χρειαστεί (γιατρούς, πηγές, δομές, προϊόντα, γνώση), διάβασε σημειώσεις, και ΣΥΝΘΕΣΕ — πες τι είναι, τι σε ενδιαφέρει, τι να προτείνεις.
- Να είσαι ΠΡΟΛΗΠΤΙΚΟΣ: αν δεις κάτι σημαντικό (λήγει γνωμάτευση, γιατρός σε σιωπή, περιστατικό σε κίνδυνο), πες το πριν σε ρωτήσουν.
- Κάνε follow-up ερωτήσεις όταν χρειάζεται για να βοηθήσεις καλύτερα. Η συζήτηση είναι διάλογος, όχι εντολές-απαντήσεις.

ΤΙ ΜΠΟΡΕΙΣ ΝΑ ΚΑΝΕΙΣ (χρησιμοποίησε τα εργαλεία όταν χρειάζεται):
- Δημιουργία/ενημέρωση γιατρών, δομών, SKU, περιστατικών, επισκέψεων, ραντεβού
- Αναζήτηση σε ΟΛΑ τα δεδομένα του χρήστη
- Ανάγνωση σημειώσεων και στρατηγικών συμβουλών
- Εβδομαδιαίο πλάνο, στατιστικά, προληπτικές προτάσεις
- Αναζήτηση στη βάση γνώσης ΕΟΠΥΥ (συνταγογράφηση, κωδικοί, ICD, συμμετοχές)
- Αναζήτηση στο web

Όταν ο χρήστης σου ζητάει κάτι, καν' το. Όταν ρωτάει, απάντα. Όταν δεν ξέρεις, ρώτα. Να είσαι ο συνεργάτης που θα ήθελες να έχεις."""





# Agent Tool Groups
WRITER_TOOLS = [f for f in AGENT_FUNCTIONS if f["name"] in [
    "create_appointment", "create_doctor", "create_case", "create_sku",
    "create_structure", "create_visit", "transition_case", "repeat_fulfilment",
    "write_week_plan", "associate_doctor_structure",
]]

ADVISOR_TOOLS = [f for f in AGENT_FUNCTIONS if f["name"] in [
    "search_doctors", "search_structures", "search_products", "search_cases",
    "get_case_detail", "get_doctor_context", "get_doctor_strategy",
    "get_dashboard", "get_proactive_insights", "list_sources",
    "list_appointments", "search_knowledge", "find_person",
]]

WEB_TOOLS = [f for f in AGENT_FUNCTIONS if f["name"] in ["web_search"]]

AGENT_FUNCTIONS.append({
    "name": "search_knowledge",
    "description": "RAG knowledge search: prescribing guidelines, papers, EOPYY protocols, clinical studies.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
})

AGENT_FUNCTIONS.append({
    "name": "find_person",
    "description": "Ψάχνει ΕΝΑ ΟΝΟΜΑ σε ΟΛΟΥΣ τους καταλόγους ταυτόχρονα: γιατρούς/υγειονομικό προσωπικό, πηγές, δομές, επαφές. Επιστρέφει ό,τι βρει με σημειώσεις. Χρησιμοποίησέ το ΟΤΑΝ ο χρήστης αναφέρει όνομα ατόμου — γιατί μπορεί να είναι γιατρός Ή πηγή Ή επαφή.",
    "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Το όνομα που ψάχνουμε (π.χ. 'Παπαδάκη')"}}, "required": ["name"]},
})

WRITER_PROMPT = """Είσαι ο Ασκληπιός, ο βοηθός του χρήστη. Σου ζήτησε να δημιουργήσεις ή να αλλάξεις κάτι στο σύστημά του (γιατρό, δομή, SKU, περιστατικό, ραντεβού, πλάνο). Κάν' το σωστά, με προσοχή στα στοιχεία που σου έδωσε, και επιβεβαίωσε φυσικά με μια ζεστή γραμμή. Αν κάτι λείπει, ρώτα το χρήστη σύντομα."""

ADVISOR_PROMPT = """Είσαι ο Ασκληπιός, ο προσωπικός βοηθός και σύμβουλος του χρήστη.

ΠΩΣ ΣΚΕΦΤΕΣΑΙ:
- Ο χρήστης μιλάει φυσικά. Εσύ ψάχνεις ΟΠΟΥ χρειάζεται για να βρεις τι θέλει: πρόσωπα μπορεί να είναι γιατροί Ή πηγές Ή επαφές Ή δομές — ψάξε παντού.
- Διάβασε ΠΑΝΤΑ τις σημειώσεις τους. Σύνθεσε: τι είναι αυτό το άτομο, τι τον/την ενδιαφέρει, τι σημειώσεις υπάρχουν, τι μπορείς να προτείνεις.
- Για ιατρικές ερωτήσεις (συνταγογράφηση, κωδικοί ΕΟΠΥΥ, ICD, συμμετοχές): ψάξε τη βάση γνώσης (search_knowledge).
- Για ερωτήσεις για προϊόντα: ψάξε στον κατάλογο (search_products).
- ΣΥΝΔΥΑΖΕ ΟΛΕΣ ΤΙΣ ΠΗΓΕΣ: αν ο χρήστης ρωτάει «τι μπορεί να γράψει ο γιατρός Χ», τότε κοίτα (α) τον γιατρό και τις σημειώσεις του, (β) τα προϊόντα του καταλόγου σου που ταιριάζουν στην ειδικότητά του, (γ) τη γνώση ΕΟΠΥΥ για κωδικούς/κανόνες — και δώσε ΜΙΑ απάντηση που τα συνδυάζει όλα. ΠΟΤΕ μην απαντάς μόνο από ένα σημείο όταν η ερώτηση αγγίζει περισσότερα.

ΠΩΣ ΜΙΛΑΣ:
- Φυσικά, ζεστά, με ουσία. Όχι λίστες χωρίς νόημα, όχι τεχνική γλώσσα.
- Αν δεν βρεις κάτι: πες το καθαρά, κάνε έξυπνες ερωτήσεις, πρότεινε τι μπορείς να κάνεις.
- Σύνθεσε τις πληροφορίες σε μια ολοκληρωμένη απάντηση-συμβουλή.

ΑΠΑΓΟΡΕΥΜΕΝΟ:
- ΠΟΤΕ μην επινοείς ΑΡΙΘΜΟΥΣ: ποσότητες, τιμές, κωδικούς ΕΟΠΥΥ, ICD, ημερομηνίες, πλήθη, ποσοστά, τηλέφωνα.
  Χρησιμοποίησε ΜΟΝΟ ό,τι εμφανίζεται στα αποτελέσματα των εργαλείων ή στα δεδομένα του context.
  Αν δεν το βλέπεις, πες «δεν το βλέπω στα δεδομένα σου» — ΜΗΝ μαντέψεις."""

# ── Agent execution ──────────────────────────────────────────────────────────

def _accent_regex(word):
    """Build regex pattern matching Greek word with or without accents AND case-insensitively.

    BUG FIX: παλιά οι τόνοι του input (π.χ. «Πατρικαλάκης») έμεναν literal στο regex
    και δεν ταίριαζαν με το αποθηκευμένο «ΠΑΤΡΙΚΑΛΑΚΗΣ». Τώρα κανονικοποιούμε ΠΡΩΤΑ
    (φεύγουν οι τόνοι) και μετά φτιάχνουμε χαρακτήρες-κλάσεις.
    """
    import re as _re
    import unicodedata
    # 1. Αφαίρεσε τόνους (NFD + strip Mn)
    word_norm = ''.join(ch for ch in unicodedata.normalize('NFD', word or '')
                        if unicodedata.category(ch) != 'Mn')
    accent_map = {
        'α': '[αάΑΆ]', 'Α': '[αάΑΆ]',
        'ε': '[εέΕΈ]', 'Ε': '[εέΕΈ]',
        'η': '[ηήΗΉ]', 'Η': '[ηήΗΉ]',
        'ι': '[ιίϊΐΙΊΪ]', 'Ι': '[ιίϊΐΙΊΪ]',
        'ο': '[οόΟΌ]', 'Ο': '[οόΟΌ]',
        'υ': '[υύϋΰΥΎΫ]', 'Υ': '[υύϋΰΥΎΫ]',
        'ω': '[ωώΩΏ]', 'Ω': '[ωώΩΏ]',
        'σ': '[σςΣ]', 'Σ': '[σςΣ]',
    }
    return ''.join(accent_map.get(ch, _re.escape(ch)) for ch in word_norm)


def _norm_gr(s):
    """Κανονικοποίηση ελληνικού: πεζά + χωρίς τόνους — για deterministic matching."""
    import unicodedata
    return ''.join(ch for ch in unicodedata.normalize('NFD', (s or '').lower())
                   if unicodedata.category(ch) != 'Mn')


def _deterministic_name_match(query: str, names: list[str]) -> list[tuple[int, int]]:
    """Case/accent-insensitive αντιστοίχιση ονόματος — ΠΡΙΝ πέσουμε στο LLM.

    Επιστρέφει λίστα (idx, score) ταξινομημένη κατά score. Λύνει το πρόβλημα
    «πατρικαλακης» vs «ΠΑΤΡΙΚΑΛΑΚΗΣ» που μπερδεύει το μοντέλο.
    """
    q = _norm_gr(query)
    q_parts = [p for p in q.split() if len(p) >= 3]
    if not q_parts:
        q_parts = [q] if len(q) >= 3 else []
    if not q_parts:
        return []
    hits = []
    for i, name in enumerate(names):
        n = _norm_gr(name)
        if not n:
            continue
        score = sum(len(p) for p in q_parts if p in n)
        if score > 0:
            hits.append((i, score))
    hits.sort(key=lambda x: -x[1])
    return hits

# ── Πλάνο Ημέρας (multi-step) ────────────────────────────────────────────

async def _build_day_plan(db, org_id) -> dict:
    """Χτίζει το σημερινό πλάνο δράσης από τα ΔΕΔΟΜΕΝΑ (όχι LLM).

    Μαζεύει: AT_RISK, επαναλήψεις που λήγουν (≤2 μέρες), SLA εκπρόθεσμα,
    επικοινωνίες που χρωστάς. Επιστρέφει actions με προτεινόμενες ώρες.
    """
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=2)

    actions = []
    slot = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if slot < now:
        slot = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    def next_slot():
        nonlocal slot
        t = slot
        slot = slot + timedelta(minutes=45)
        return t

    # 1. Σε κίνδυνο — ΠΡΩΤΑ
    async for case in db.cases.find({"org_id": org_id, "status": "AT_RISK", "no_repeat": {"$ne": True}}).limit(5):
        t = next_slot()
        actions.append({
            "kind": "call", "case_id": str(case["_id"]),
            "title": f"🚨 Κλήση: {case.get('alias','Περιστατικό')} — επανάληψη εκπρόθεσμη",
            "reason": "σε κίνδυνο — επανάληψη καθυστερεί",
            "suggested_time": t.isoformat(),
            "time_label": _fmt_slot(t),
        })

    # 2. Επαναλήψεις που λήγουν σύντομα
    async for case in db.cases.find({
        "org_id": org_id, "status": "ACTIVE", "no_repeat": {"$ne": True},
        "next_repeat_due": {"$lte": tomorrow},
    }).sort("next_repeat_due", 1).limit(5):
        due = case.get("next_repeat_due")
        due_str = due.strftime("%d/%m") if due else "?"
        t = next_slot()
        actions.append({
            "kind": "call", "case_id": str(case["_id"]),
            "title": f"🔁 Κλήση: {case.get('alias','Περιστατικό')} — επανάληψη λήγει {due_str}",
            "reason": "επανάληψη λήγει",
            "suggested_time": t.isoformat(),
            "time_label": _fmt_slot(t),
        })

    # 3. SLA εκπρόθεσμα (NEW/ASSESSED/PRESCRIBED)
    s = (await db.orgs.find_one({"_id": ObjectId(org_id)}) or {}).get("settings", {}) if ObjectId.is_valid(org_id) else {}
    new_sla = s.get("new_sla_days", 5)
    async for case in db.cases.find({"org_id": org_id, "status": "NEW"}).limit(5):
        created = case.get("created_at")
        if created and (now - ensure_utc(created)).days >= new_sla:
            t = next_slot()
            actions.append({
                "kind": "followup", "case_id": str(case["_id"]),
                "title": f"🆕 Αξιολόγηση: {case.get('alias','Περιστατικό')} — ξεπέρασε το SLA",
                "reason": "νέο περιστατικό σε αναμονή",
                "suggested_time": t.isoformat(),
                "time_label": _fmt_slot(t),
            })

    # 4. Σημερινά ραντεβού (ενημερωτικά)
    scheduled = []
    async for a in db.appointments.find({
        "org_id": org_id,
        "at": {"$gte": today, "$lt": today + timedelta(days=1)},
    }).sort("at", 1).limit(10):
        scheduled.append({"at": str(a.get("at", ""))[:16], "title": a.get("title", "Ραντεβού")})

    return {
        "actions": actions[:8],
        "scheduled_today": scheduled,
        "generated_at": now.isoformat(),
    }


def _fmt_slot(dt) -> str:
    return dt.strftime("%H:%M")


async def _execute_day_plan(db, org_id, user_id, plan: dict, pending_doc: dict):
    """Εκτελεί το επιβεβαιωμένο πλάνο: δημιουργεί ραντεβού για κάθε action."""
    now = datetime.now(timezone.utc)
    created = 0
    for a in plan.get("actions", []):
        at = a.get("suggested_time")
        try:
            at_dt = datetime.fromisoformat(at) if at else now
        except Exception:
            at_dt = now
        if at_dt < now:
            at_dt = now + (created + 1) * 45 * 60 if created else now + 30 * 60
        import calendar
        at_dt = at_dt.replace(second=0, microsecond=0)
        try:
            await db.appointments.insert_one({
                "org_id": org_id,
                "at": at_dt,
                "title": a.get("title", "Ενέργεια"),
                "notes": a.get("reason", ""),
                "case_id": a.get("case_id"),
                "kind": a.get("kind", "call"),
                "created_by": user_id,
                "created_at": now,
                "updated_at": now,
            })
            created += 1
        except Exception:
            pass
    await db.agent_memory.delete_one({"_id": pending_doc["_id"]})
    await db.agent_memory.insert_one({
        "org_id": org_id, "type": "executed_day_plan",
        "executed_at": now, "created_count": created,
    })


async def _build_full_context(db, org_id) -> str:
    """Ο ΠΛΗΡΗΣ ΚΟΣΜΟΣ ΤΟΥ ΧΡΗΣΤΗ — όλα τα δεδομένα του, για κάθε ερώτηση.

    Γιατροί με ΟΛΕΣ τις σημειώσεις, δομές, πηγές, ενεργά περιστατικά,
    επερχόμενα ραντεβού, στατιστικά, σύνοψη συνομιλιών.
    Ο Ασκληπιός βλέπει ΤΑ ΠΑΝΤΑ σε κάθε απάντηση — συνδυάζει, δεν μαντεύει.
    """
    try:
        now = datetime.now(timezone.utc)
        lines: list[str] = []

        # ── Στατιστικά ──
        doctors_count = await db.doctors.count_documents({"org_id": org_id})
        structures_count = await db.structures.count_documents({"org_id": org_id})
        sources_count = await db.sources.count_documents({"org_id": org_id})
        skus_count = await db.skus.count_documents({"org_id": org_id, "active": True})
        cases_by_status = {}
        async for c in db.cases.aggregate([
            {"$match": {"org_id": org_id}},
            {"$group": {"_id": "$status", "n": {"$sum": 1}}},
        ]):
            cases_by_status[c["_id"]] = c["n"]
        lines.append(
            f"=== Ο ΚΟΣΜΟΣ ΣΟΥ (πλήρη δεδομένα) ===\n"
            f"Γιατροί: {doctors_count} · Δομές: {structures_count} · Πηγές: {sources_count} · Προϊόντα: {skus_count} · "
            f"Περιστατικά: {cases_by_status or {}}")

        # ── ΓΙΑΤΡΟΙ με ΠΛΗΡΕΙΣ σημειώσεις ──
        lines.append("\n=== ΓΙΑΤΡΟΙ / ΥΓΕΙΟΝΟΜΙΚΟ ΠΡΟΣΩΠΙΚΟ (με σημειώσεις) ===")
        async for d in db.doctors.find({"org_id": org_id}).limit(150):
            notes = (d.get("notes") or "").strip().replace("\n", " ")
            parts = [d.get("name", "?")]
            if d.get("specialty") and d.get("specialty") != "OTHER":
                parts.append(d.get("specialty"))
            if d.get("workplace_type") and d.get("workplace_type") != "OTHER":
                parts.append(d.get("workplace_type"))
            if d.get("area"):
                parts.append(d.get("area"))
            if d.get("phone"):
                parts.append(f"☎ {d.get('phone')}")
            line = " · ".join(parts)
            if d.get("rx_count_90d"):
                line += f" · Rx/90d: {d['rx_count_90d']}"
            if notes:
                line += f"\n    📝 {notes[:400]}"
            lines.append(line)

        # ── ΔΟΜΕΣ ──
        lines.append("\n=== ΔΟΜΕΣ ===")
        async for s in db.structures.find({"org_id": org_id}).limit(100):
            parts = [s.get("name", "?")]
            if s.get("city"):
                parts.append(s.get("city"))
            if s.get("type") and s.get("type") != "OTHER":
                parts.append(s.get("type"))
            line = " · ".join(parts)
            sn = (s.get("notes") or "").strip().replace("\n", " ")
            if sn:
                line += f"\n    📝 {sn[:200]}"
            lines.append(line)

        # ── ΠΗΓΕΣ ──
        lines.append("\n=== ΠΗΓΕΣ (ποιοι σου φέρνουν περιστατικά) ===")
        async for s in db.sources.find({"org_id": org_id}).limit(50):
            parts = [s.get("name", "?")]
            if s.get("type") and s.get("type") != "OTHER":
                parts.append(s.get("type"))
            line = " · ".join(parts)
            sn = (s.get("notes") or "").strip().replace("\n", " ")
            if sn:
                line += f"\n    📝 {sn[:200]}"
            lines.append(line)

        # ── ΕΝΕΡΓΑ ΠΕΡΙΣΤΑΤΙΚΑ ──
        lines.append("\n=== ΠΕΡΙΣΤΑΤΙΚΑ (ανοιχτά) ===")
        n_cases = 0
        async for c in db.cases.find(
            {"org_id": org_id, "status": {"$in": ["ACTIVE", "AT_RISK", "PRESCRIBED", "AT_DOCTOR", "NEW", "ASSESSED"]}}
        ).sort("updated_at", -1).limit(30):
            doctor_name = ""
            if c.get("doctor_id"):
                try:
                    dd = await db.doctors.find_one({"_id": ObjectId(c["doctor_id"]), "org_id": org_id}, {"name": 1})
                    if dd:
                        doctor_name = dd.get("name", "")
                except Exception:
                    pass
            due = c.get("next_repeat_due")
            due_s = f" · λήγει {due.strftime('%d/%m')}" if due else ""
            lines.append(
                f"  [{c.get('status','')}] {c.get('alias','?')} · {c.get('area','') or '?'}"
                f"{' · γιατρός: ' + doctor_name if doctor_name else ''}{due_s}")
            n_cases += 1
        if not n_cases:
            lines.append("  (κανένα ανοιχτό)")

        # ── ΕΠΟΜΕΝΑ ΡΑΝΤΕΒΟΥ (7 ημέρες) ──
        lines.append("\n=== ΕΠΟΜΕΝΑ ΡΑΝΤΕΒΟΥ (7 ημέρες) ===")
        n_appts = 0
        async for a in db.appointments.find({
            "org_id": org_id,
            "at": {"$gte": now, "$lte": now + timedelta(days=7)},
        }).sort("at", 1).limit(15):
            lines.append(f"  {str(a.get('at',''))[:16]} — {a.get('title','?')}")
            n_appts += 1
        if not n_appts:
            lines.append("  (κανένα)")

        # ── Σύνοψη παλιότερων συνομιλιών ──
        summary = await _get_conversation_summary(db, org_id)
        if summary:
            lines.append(f"\n=== ΜΝΗΜΗ ΠΡΟΗΓΟΥΜΕΝΩΝ ΣΥΖΗΤΗΣΕΩΝ ===\n{summary}")

        return "\n".join(lines)
    except Exception as e:
        return f"(Πλήρες context μη διαθέσιμο: {e})"


async def _build_context(db, org_id):
    try:
        # Counters
        doctor_count = await db.doctors.count_documents({"org_id": org_id})
        structure_count = await db.structures.count_documents({"org_id": org_id})
        sku_count = await db.skus.count_documents({"org_id": org_id})
        case_count = await db.cases.count_documents({"org_id": org_id})
        
        # Recent appointments (next 7 days)
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        week_end = now + timedelta(days=7)
        appt_cursor = db.appointments.find({
            "org_id": org_id,
            "at": {"$gte": now, "$lte": week_end},
        }).sort("at", 1).limit(10)
        appts = []
        async for a in appt_cursor:
            doctor_name = ""
            if a.get("doctor_id"):
                try:
                    d = await db.doctors.find_one({"_id": ObjectId(a["doctor_id"]), "org_id": org_id})
                    if d: doctor_name = d.get("name", "")
                except: pass
            appts.append(f"  - {a.get('title','?')} | {str(a.get('at',''))[:16]} | {doctor_name}")
        
        appt_text = "\n".join(appts) if appts else "  (κανένα)"
        
        # Sample doctors
        doc_cursor = db.doctors.find({"org_id": org_id}).limit(50)
        doctors = []
        async for d in doc_cursor:
            doctors.append(f"  - {d.get('name','?')} ({d.get('specialty','?')})")
        doctor_text = "\n".join(doctors) if doctors else "  (κανένας)"
        
        # Sample structures
        struct_cursor = db.structures.find({"org_id": org_id}).limit(50)
        structures = []
        async for s in struct_cursor:
            structures.append(f"  - {s.get('name','?')} ({s.get('city','?')})")
        struct_text = "\n".join(structures) if structures else "  (καμία)"
        
        return f"""ΠΕΡΙΒΑΛΛΟΝ ΧΡΗΣΤΗ:
- {doctor_count} γιατροί, {structure_count} δομές, {sku_count} προϊόντα, {case_count} περιστατικά

ΕΠΟΜΕΝΑ ΡΑΝΤΕΒΟΥ (7 ημέρες):
{appt_text}

ΓΙΑΤΡΟΙ:
{doctor_text}

ΔΟΜΕΣ:
{struct_text}

Χρησιμοποίησε αυτές τις πληροφορίες για να δώσεις συγκεκριμένες, προσωποποιημένες απαντήσεις. Αν ο χρήστης αναφέρει γιατρό που υπάρχει στη λίστα, χρησιμοποίησε το get_doctor_context ή το search_doctors για περισσότερες λεπτομέρειες."""
    except Exception as e:
        return f"(Context unavailable: {e})"


async def run_agent(
    user_message: str,
    db: AsyncIOMotorDatabase,
    org_id: str,
    user_id: str,
) -> dict:
    """Execute an AI agent command from natural language.

    Uses intent detection for deterministic actions (write_week_plan).
    Falls back to LLM function calling for general queries.
    """
    msg_lower = user_message.lower().strip()
    now = datetime.now(timezone.utc)
    rag_context = ""

    # ── Multi-step: Πλάνο Ημέρας (οργάνωσε τη μέρα μου) ──────────────────
    # «οργάνωσέ μου την ημέρα» → χτίζει πλάνο → ζητάει επιβεβαίωση → «ναι» → εκτελεί.
    _plan_keywords = ["οργανωσ", "οργάνωσ", "οργάνωσε", "τι να κανω σημερα", "τι να κάνω σήμερα",
                      "πλανο ημερασ", "πλάνο ημέρας", "κανονισε τη μερα", "κανονίσε τη μέρα",
                      "φτιαξε μου τη μερα", "φτιάξε μου τη μέρα", "προγραμμα ημερασ", "πρόγραμμα ημέρας"]
    _confirm_tokens = {"ναι", "ναι!", "οκ", "οκ!", "ok", "ok!", "yes", "καντα", "καν'τα", "καν' τα",
                       "κάντα", "κάν' τα", "προχωρα", "προχώρα", "φτιαξτα", "φτιάξτα", "φτιάξε τα",
                       "καλα", "καλά", "ενταξει", "εντάξει", "συμφωνω", "συμφωνώ", "τελεια", "τέλεια",
                       "βαλτα", "βάλτα"}
    _has_plan_request = any(k in msg_lower for k in _plan_keywords)
    _looks_like_confirm = msg_lower.strip() in _confirm_tokens

    if _looks_like_confirm:
        pending = await db.agent_memory.find_one({"org_id": org_id, "type": "pending_day_plan"},
                                                 sort=[("created_at", -1)])
        if pending:
            plan = pending.get("plan") or {}
            if plan.get("actions"):
                await _execute_day_plan(db, org_id, user_id, plan, pending)
                done = plan.get("actions", [])
                resp = f"✅ Έγινε! Δημιούργησα {len(done)} στοιχεία στη σημερινή ατζέντα:\n" + \
                       "\n".join(f"{i+1}. {a.get('title','')} — {str(a.get('suggested_time',''))[:16]}" for i, a in enumerate(done))
                await _add_to_memory(db, org_id, "user", user_message)
                await _add_to_memory(db, org_id, "assistant", resp)
                return {"response": resp, "actions": [{"function": "execute_day_plan", "result": {"created": len(done)}}],
                        "requires_confirmation": False}

    if _has_plan_request:
        plan = await _build_day_plan(db, org_id)
        actions = plan.get("actions", [])
        if not actions:
            resp = "Σήμερα δεν έχεις εκκρεμότητες! 🎉 Όλα είναι τακτοποιημένα. Δες το Πλάνο για τα ραντεβού σου."
        else:
            lines = ["Καλημέρα! 👋 Να το πλάνο της ημέρας:"]
            for i, a in enumerate(actions, 1):
                lines.append(f"{i}. {a.get('time_label','')} — {a.get('title','')} ({a.get('reason','')})")
            lines.append("")
            lines.append("Θες να τα βάλω στην ατζέντα σου ως ραντεβού; Πες «ναι» και το κάνω.")
            resp = "\n".join(lines)
            # Αποθήκευση pending plan για επιβεβαίωση
            await db.agent_memory.delete_many({"org_id": org_id, "type": "pending_day_plan"})
            await db.agent_memory.insert_one({
                "org_id": org_id, "type": "pending_day_plan",
                "plan": plan, "created_at": datetime.now(timezone.utc),
            })
        await _add_to_memory(db, org_id, "user", user_message)
        await _add_to_memory(db, org_id, "assistant", resp)
        return {"response": resp, "actions": [{"function": "build_day_plan", "result": {"actions": len(actions)}}],
                "requires_confirmation": True}

    # ── LLM Intent Classification ──────────────────────────────────────
    intent = "chat"
    # Deterministic φράγμα: ΕΡΩΤΗΣΗ ≠ εντολή δημιουργίας.
    # «τι μπορεί να μου γράψει ο γιατρός» είναι advise, ΟΧΙ create.
    _question_markers = ["?", "τι ", "ποιος ", "ποια ", "ποιο ", "πόσ", "πώς ", "πως ", "γιατί",
                         "μπορεί ", "μπορω ", "ξερεις ", "ξέρεις", "που ", "πού "]
    _create_verbs = ["φτιάξε", "φτιαξε", "δημιούργησε", "δημιουργησε", "πρόσθεσε", "προσθεσε",
                     "βάλε", "βαλε", "κατέγραψε", "κατεγραψε", "καταχώρησε", "καταχωρησε", "κανε "]
    _is_question = ("?" in user_message) or any(msg_lower.startswith(m) for m in _question_markers)
    _has_create_verb = any(v in msg_lower for v in _create_verbs)

    if _is_question and not _has_create_verb:
        intent = "advise"  # ερώτηση χωρίς ρητή εντολή — δεν δημιουργούμε ΠΟΤΕ
    else:
        try:
            from backend.llm.client import simple_call
            intent = (await simple_call(
                f"Classify this Greek CRM message intent. Reply with EXACTLY ONE WORD:\n"
                f"plan (weekly schedule/organization), create (ΕΝΤΟΛΗ to build/write/make a NEW entity: doctor,SKU,case,visit,appointment,structure — μόνο αν ο χρήστης ΖΗΤΑΕΙ ρητά να φτιάξεις κάτι), "
                f"dashboard (stats/numbers/counts), web (internet/Google search), advise (strategy/suggestions/questions about existing data).\n"
                f"ΠΡΟΣΟΧΗ: αν ο χρήστης ΡΩΤΑΕΙ κάτι (τι μπορεί, ποιος είναι, πώς γίνεται) τότε ΔΕΝ είναι create — είναι advise.\n"
                f"Message: {user_message}\nIntent:",
                temperature=0, max_tokens=10, model=settings.deepseek_model_fast)).strip().lower()
            # Normalize
            if "plan" in intent: intent = "plan"
            elif "create" in intent: intent = "create"
            elif "dashboard" in intent: intent = "dashboard"
            elif "web" in intent: intent = "web"
            elif "advise" in intent: intent = "advise"
            else: intent = "chat"
        except:
            # Fallback to regex on LLM failure
            if any(kw in msg_lower for kw in ["πλάνο", "προγραμμα", "οργανωσ", "εβδομαδια"]): intent = "plan"
            elif any(kw in msg_lower for kw in ["στατιστικα", "πόσα", "τζιρο"]): intent = "dashboard"
            elif any(kw in msg_lower for kw in ["ψάξε", "google", "ίντερνετ"]): intent = "web"
            elif _has_create_verb: intent = "create"
            else: intent = "advise"

    # ── Route based on intent ──────────────────────────────────────────
    if intent == "plan":
        # Εβδομαδιαίο πλάνο: διαβάζει ΣΗΜΕΙΩΣΕΙΣ, σέβεται Δευτέρα-Παρασκευή,
        # μοιράζει ομοιόμορφα και ΔΕΝ βάζει Σαββατοκύριακα.
        target_week = _parse_plan_week(user_message, now)

        doctors = []
        async for d in db.doctors.find({"org_id": org_id}).limit(150):
            last = d.get("last_visit_at")
            if last and hasattr(last, 'tzinfo') and last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            days_since = "never" if not last else f"{(now - last).days}d"
            notes = (d.get("notes") or "").strip()
            doctors.append(f"  {d.get('name','?')} ({d.get('specialty','?')}, area={d.get('area','?')}, last_visit={days_since}"
                           + (f", 📝 {notes[:120]}" if notes else ""))

        structures_list = []
        async for s in db.structures.find({"org_id": org_id}).limit(50):
            structures_list.append(f"  {s.get('name','?')} ({s.get('city','?')})")

        at_risk = await db.cases.count_documents({"org_id": org_id, "status": "AT_RISK"})
        active = await db.cases.count_documents({"org_id": org_id, "status": "ACTIVE"})
        pending = await db.cases.count_documents({"org_id": org_id, "status": "PRESCRIBED"})

        analysis_prompt = f"""Είσαι αναλυτής CRM ιατροτεχνολογικών. Ανάλυσε τα παρακάτω δεδομένα και πρότεινε στρατηγική για εβδομαδιαίο πλάνο επισκέψεων. Μίλα σαν άνθρωπος, ζεστά και έξυπνα. Εξήγησε το ΣΚΕΠΤΙΚΟ σου.

ΓΙΑΤΡΟΙ ({len(doctors)}):
{chr(10).join(doctors[:50])}

ΔΟΜΕΣ:
{chr(10).join(structures_list[:20])}

ΠΕΡΙΣΤΑΤΙΚΑ: {active} ενεργά, {at_risk} σε κίνδυνο, {pending} προς εκτέλεση

Ανάλυσε: ποιοι γιατροί έχουν προτεραιότητα, πώς ομαδοποιούνται γεωγραφικά, ποιες μέρες προτείνεις και ΓΙΑΤΙ. ΔΙΑΒΑΣΕ τις σημειώσεις (📝) — αν κάποιος δέχεται μόνο συγκεκριμένη μέρα, ΣΕΒΑΣΟΥ το. Σύντομα αλλά ουσιαστικά (5-8 γραμμές)."""

        from backend.llm.client import simple_call
        try:
            analysis = (await simple_call(analysis_prompt, temperature=0.3, max_tokens=600)).strip()
        except:
            analysis = "Ανάλυση του δικτύου σου..."

        # Step 2: Create appointments (Δευτέρα-Παρασκευή, ομοιόμορφα, με σημειώσεις)
        result = await _write_week_plan(db, org_id, user_id, target_week)
        summary = result.get("summary", "")
        msg = result.get("message", "")
        warnings = result.get("warnings", [])

        warn_text = ("\n⚠️ " + "\n⚠️ ".join(warnings)) if warnings else ""
        response = f"{analysis}\n\n📅 {msg}\n\n{summary}{warn_text}" if summary else f"{analysis}\n\n📅 {msg}{warn_text}"

        await _add_to_memory(db, org_id, "user", user_message)
        await _add_to_memory(db, org_id, "assistant", response)
        return {
            "response": response,
            "actions": [{"function": "write_week_plan", "result": result}],
            "requires_confirmation": False,
        }

    if intent == "create":
        # Extract what to create from the message via LLM
        from backend.llm.client import simple_call
        prompt = f"Ο χρήστης θέλει να δημιουργήσει κάτι. Τι ακριβώς; Απάντησε ΜΟΝΟ με μία λέξη: doctor, sku, structure, case, visit, appointment. Αν δεν είναι ξεκάθαρο, απάντησε 'unknown'.\n\nΜήνυμα: {user_message}\nΤι:"
        try:
            create_what = (await simple_call(prompt, temperature=0, max_tokens=20, model=settings.deepseek_model_fast)).strip().lower()
        except:
            create_what = "unknown"

        if "doctor" in create_what or "γιατρ" in user_message.lower() or "νοσηλ" in user_message.lower():
            from backend.llm.client import simple_call
            prompt = f"Από το παρακάτω μήνυμα, εξήγαγε ΜΟΝΟ το όνομα του γιατρού/νοσηλευτή. Απάντησε ΜΟΝΟ με το όνομα.\n\nΜήνυμα: {user_message}"
            try:
                name = (await simple_call(prompt, temperature=0, max_tokens=100, model=settings.deepseek_model_fast)).strip()
                result = await _create_doctor({"name": name}, db, org_id)
                response = f"👨‍⚕️ Το υγειονομικό προσωπικό '{result['name']}' δημιουργήθηκε."
            except:
                response = "Δεν μπόρεσα να καταλάβω το όνομα. Πες μου 'φτιάξε γιατρό [ΟΝΟΜΑ]'."
                result = {"error": "name extraction failed"}
            await _add_to_memory(db, org_id, "user", user_message)
            await _add_to_memory(db, org_id, "assistant", response)
            return {"response": response, "actions": [{"function": "create_doctor", "result": result}], "requires_confirmation": False}

    if intent == "dashboard":
        result = await _get_dashboard(db, org_id)
        response = f"📊 Ενεργά: {result.get('active_cases',0)} | 🚨 Σε κίνδυνο: {result.get('at_risk',0)} | 🆕 Νέο μήνα: {result.get('new_this_month',0)}"
        await _add_to_memory(db, org_id, "user", user_message)
        await _add_to_memory(db, org_id, "assistant", response)
        return {"response": response, "actions": [{"function": "get_dashboard", "result": result}], "requires_confirmation": False}

    # ── Fallback: LLM function calling for chat/advise/web ──────────────
    now2 = datetime.now(timezone.utc)
    now_str = now2.strftime("%Y-%m-%d %H:%M:%S UTC")
    now_greek = now2.strftime("%A %d %B %Y, %H:%M")

    # Use LLM-classified intent for tool selection
    if intent == "web":
        tools = WEB_TOOLS + ADVISOR_TOOLS
        system_prompt = "Eisai o Asklipios Erevnitis. Kaneis web search kai apantas me plirofories."
    elif intent == "create":
        tools = WRITER_TOOLS
        system_prompt = WRITER_PROMPT
    else:
        tools = ADVISOR_TOOLS
        system_prompt = ADVISOR_PROMPT

    try:
        # Πλήρης κόσμος χρήστη: ΟΛΑ τα δεδομένα (γιατροί+σημειώσεις, δομές, πηγές,
        # περιστατικά, ραντεβού, σύνοψη συνομιλιών) — για κάθε ερώτηση.
        context = await _build_full_context(db, org_id)
    except:
        context = await _build_context(db, org_id)
    timestamp_context = f"""NOW: {now_str}
DAY: {now_greek}
Today: {now2.strftime('%Y-%m-%d')}, week start: {(now2 - timedelta(days=now2.weekday())).strftime('%Y-%m-%d')}"""

    # ── LLM Router: decide WHERE this question should be answered ────────
    # The LLM understands the system structure and routes correctly.
    router_prompt = (
        "Είσαι ο δρομολογητής ενός CRM ιατροτεχνολογικών. Ο χρήστης ρώτησε:\n"
        f"«{user_message}»\n\n"
        "Το σύστημα έχει: (α) βάση γνώσης ΕΟΠΥΥ (συνταγογράφηση, κωδικοί, ICD, συμμετοχές, προϊόντα), "
        "(β) γιατρούς/υγειονομικό προσωπικό, (γ) πηγές συστάσεων, (δ) δομές/νοσοκομεία, "
        "(ε) κατάλογο προϊόντων (SKU).\n\n"
        "Αποφάσισε ΠΟΥ θα απαντηθεί καλύτερα. Απάντησε ΜΟΝΟ με μία λέξη:\n"
        "- rag: αν αφορά συνταγογράφηση/κωδικούς/ICD/συμμετοχές/γνωματεύσεις/ιατρική οδηγία\n"
        "- people: αν αφορά πρόσωπα (γιατρό, πηγή, επαφή, νοσηλευτή) ή τι να πεις/κάνεις με κάποιον\n"
        "- products: αν αφορά προϊόν/εταιρία/κατάλογο SKU\n"
        "- structures: αν αφορά δομή/νοσοκομείο/κλινική\n"
        "- general: οτιδήποτε άλλο (χαιρετισμός, πλάνο, στατιστικά, γενική ερώτηση)\n"
        "ΡΟΥΤΑ:"
    )
    route = "general"
    try:
        from backend.llm.client import simple_call
        route = (await simple_call(router_prompt, temperature=0, max_tokens=15, model=settings.deepseek_model_fast)).strip().lower()
        if route not in ("rag", "people", "products", "structures", "general"):
            route = "general"
    except:
        route = "general"

    # ── RAG auto-injection — για ΟΛΑ τα routes (συνδυασμένες ερωτήσεις) ──
    # Τοπικό reranking (cosine over embeddings) — το Jina έχει τελειώσει.
    rag_context = ""
    try:
        # Load knowledge chunks: SHARED (default EOPYY) + THIS org's own
        candidates = []
        async for k in db.knowledge_chunks.find(
            {"$or": [{"is_shared": True}, {"org_id": org_id}]},
            {"chunk_text": 1, "embedding": 1}).limit(200):
            txt = (k.get("chunk_text", "") or "")
            if txt:
                candidates.append({"text": txt, "embedding": k.get("embedding") or []})
        if candidates:
            from backend.llm.embeddings import rank_documents
            top_docs = await rank_documents(user_message[:500], candidates)
            if top_docs:
                rag_context = "\n\n=== RAG KNOWLEDGE ===\n" + "\n---\n".join(d["text"] for d in top_docs)
    except: pass

    # ── People extraction — για ΟΛΑ τα routes (όχι μόνο people) ──────────
    people_context = ""
    try:
        from backend.llm.client import simple_call as _sc
        # Extract person names from the user message
        names_raw = (await _sc(
            f"Από το μήνυμα του χρήστη, εξήγαγε τα ΟΝΟΜΑΤΑ των προσώπων που αναφέρονται "
            f"(γιατροί, πηγές, επαφές). Απάντησε ΜΟΝΟ με τα ονόματα χωρισμένα με κόμμα, χωρίς άλλο κείμενο. "
            f"Αν δεν υπάρχει κανένα όνομα, απάντησε ΜΟΝΟ 'ΚΑΝΕΝΑ'.\n"
            f"Μήνυμα: {user_message}",
            temperature=0, max_tokens=100)).strip()
        if names_raw and "κανενα" not in names_raw.lower():
            name_list = [n.strip() for n in names_raw.split(",") if n.strip()]
            found_blocks = []
            for nm in name_list[:3]:
                res = await _find_person({"name": nm}, db, org_id)
                if res.get("found"):
                    r = res["results"]
                    for d in r.get("doctors", []):
                        found_blocks.append(f"👨‍⚕️ ΓΙΑΤΡΟΣ: {d['name']} | {d.get('specialty','')} | {d.get('area','')} | {d.get('phone','')} | 📝 {d.get('notes','')[:250]}")
                    for s in r.get("sources", []):
                        found_blocks.append(f"🔗 ΠΗΓΗ: {s['name']} | {s.get('type','')} | {s.get('phone','')} | 📝 {s.get('notes','')[:250]}")
                    for st in r.get("structures", []):
                        found_blocks.append(f"🏥 ΔΟΜΗ: {st['name']} | {st.get('city','')} | 📝 {st.get('notes','')[:200]}")
            if found_blocks:
                people_context = "\n\n=== ΒΡΕΘΗΚΑΝ ΠΡΟΣΩΠΑ (χρησιμοποίησέ τα) ===\n" + "\n".join(found_blocks)
    except:
        people_context = ""

    # ── Product auto-context: deterministic αναζήτηση SKU από το μήνυμα ──
    products_context = ""
    try:
        import re as _re
        kw_list = [w for w in _re.split(r'\s+|[,.;:!?()]+', user_message) if len(w) >= 4][:6]
        if kw_list:
            or_clauses = []
            for kw in kw_list:
                rx = _accent_regex(kw)
                or_clauses.append({"commercial_name": {"$regex": rx, "$options": "i"}})
                or_clauses.append({"description": {"$regex": rx, "$options": "i"}})
                or_clauses.append({"category": {"$regex": rx, "$options": "i"}})
                or_clauses.append({"eopyy_code": {"$regex": rx, "$options": "i"}})
            found = []
            seen = set()
            async for p in db.skus.find({"org_id": org_id, "$or": or_clauses}).limit(10):
                name = p.get("commercial_name", "")
                if name and name not in seen:
                    seen.add(name)
                    found.append(f"📦 {name} | κωδ: {p.get('eopyy_code','')} | {p.get('category','')} | {p.get('reimb_price',0)}€ | { (p.get('description','') or '')[:200] }")
            if found:
                products_context = "\n\n=== ΠΡΟΪΟΝΤΑ ΠΟΥ ΤΑΙΡΙΑΖΟΥΝ (από τον κατάλογό σου) ===\n" + "\n".join(found)
    except:
        products_context = ""

    memory = await _get_memory(db, org_id)
    # Add router guidance so the agent knows where to focus — αλλά ΠΑΝΤΑ συνδυάζει
    route_guidance = {
        "people": "\n\n[ΟΔΗΓΙΑ: Η ερώτηση αφορά ΠΡΟΣΩΠΑ. Έχεις ήδη στο context: γνώση (RAG), πρόσωπα που βρέθηκαν, προϊόντα που ταιριάζουν. ΣΥΝΔΥΑΣΕ τα όλα — μην απαντάς μόνο από ένα. Αν κάτι λείπει, κάλεσε search_products / search_knowledge / find_person.]",
        "products": "\n\n[ΟΔΗΓΙΑ: Η ερώτηση αφορά ΠΡΟΪΟΝ/ΚΑΤΑΛΟΓΟ. Έχεις ήδη προϊόντα + γνώση RAG στο context. Συνδύασέ τα. Αν χρειάζεσαι περισσότερα, κάλεσε search_products και search_knowledge.]",
        "structures": "\n\n[ΟΔΗΓΙΑ: Η ερώτηση αφορά ΔΟΜΗ/ΝΟΣΟΚΟΜΕΙΟ. Έχεις δομές + γνώση στο context. Συνδύασέ τα.]",
        "rag": "\n\n[ΟΔΗΓΙΑ: Ιατρική ερώτηση — έχεις RAG γνώση + πρόσωπα + προϊόντα στο context σου. ΣΥΝΔΥΑΣΕ: π.χ. αν ρωτάει για γιατρό, δες και τι προϊόντα/γνώση σχετίζονται.]",
        "general": "\n\n[ΟΔΗΓΙΑ: Έχεις στο context: γνώση RAG, πρόσωπα που βρέθηκαν, προϊόντα που ταιριάζουν. Σύνθεσε μια ολοκληρωμένη απάντηση χρησιμοποιώντας Ο,ΤΙ είναι σχετικό.]",
    }.get(route, "")
    messages = [
        {"role": "system", "content": system_prompt + "\n\n" + timestamp_context + "\n\n" + context + rag_context + people_context + products_context + route_guidance},
    ]
    for m in memory[-6:]:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": user_message})

    await _add_to_memory(db, org_id, "user", user_message)

    actions_taken = []
    requires_confirmation = False
    final_response = ""

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            response = await client.post(
                f"{settings.deepseek_base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.deepseek_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.deepseek_model,
                    "messages": messages,
                    "tools": [{"type": "function", "function": f} for f in tools],
                    "tool_choice": "auto",
                    "temperature": 0.1,
                    "max_tokens": 4000,
                    "thinking": {"type": "disabled"},
                },
            )
            response.raise_for_status()
            body = response.json()
        except Exception as api_err:
            await _add_to_memory(db, org_id, "assistant", "Σφάλμα σύνδεσης με την AI. Προσπάθησε ξανά σε λίγο.")
            return {
                "response": f"Σφάλμα σύνδεσης με την AI: {str(api_err)[:200]}. Προσπάθησε ξανά σε λίγο.",
                "actions": [],
                "requires_confirmation": False,
            }

    choice = body["choices"][0]
    msg = choice["message"]

    # If the model wants to call functions
    if msg.get("tool_calls"):
        # Collect ALL tool calls into ONE assistant message
        assistant_msg = {
            "role": "assistant",
            "content": msg.get("content"),
            "tool_calls": msg["tool_calls"],
        }
        # Preserve reasoning_content if present (required for thinking mode)
        if msg.get("reasoning_content"):
            assistant_msg["reasoning_content"] = msg["reasoning_content"]
        messages.append(assistant_msg)

        for tool_call in msg["tool_calls"]:
            func_name = tool_call["function"]["name"]
            func_args = json.loads(tool_call["function"]["arguments"])

            result = await _execute_function(func_name, func_args, db, org_id, user_id)

            if func_name in ("create_appointment",):
                requires_confirmation = True

            await db.llm_calls.insert_one({
                "org_id": org_id, "user_id": user_id, "model": "agent-function",
                "prompt_preview": f"agent.{func_name}",
                "response_preview": str(result)[:300],
                "attempt": 1, "latency_ms": 0, "success": ("error" not in str(result).lower()),
                "at": datetime.now(timezone.utc),
            })
            # Log to episodic memory
            try:
                from backend.llm.memory import log_episode
                await log_episode(db, org_id, f"agent_{func_name}",
                    f"Agent called {func_name}: {json.dumps(func_args, ensure_ascii=False)[:200]}",
                    [{"name": func_name, "type": "function_call"}],
                    {"result_summary": json.dumps(result, ensure_ascii=False, default=str)[:200]})
            except: pass

            actions_taken.append({
                "function": func_name,
                "args": func_args,
                "result": result,
            })

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })

        # Get final response from model
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                # Strong final-answer instruction appended as a user message
                final_instruction = (
                    "ΤΕΛΙΚΗ ΑΠΑΝΤΗΣΗ ΠΡΟΣ ΤΟΝ ΧΡΗΣΤΗ:\n"
                    "Έχεις πλέον ΟΛΑ τα δεδομένα από τα εργαλεία. Γράψε την ΤΕΛΙΚΗ απάντηση:\n"
                    "- ΜΗΝ περιγράφεις τη διαδικασία σου (μην λες 'ας ψάξω', 'ας δω', 'βρήκα')\n"
                    "- ΜΗΝ αναφέρεις εργαλεία ή τεχνικές λεπτομέρειες\n"
                    "- Αν βρήκες πρόσωπα/προϊόντα: ΣΥΝΘΕΣΕ — πες τι είναι, τι σημειώσεις έχουν, τι ενδιαφέροντα, και δώσε ΣΥΓΚΕΚΡΙΜΕΝΗ πρόταση\n"
                    "- Αν δεν βρήκες στοιχεία: ΠΕΣ ΤΟ καθαρά, κάνε 1-2 στοχευμένες ερωτήσεις, και δώσε γενική αλλά χρήσιμη καθοδήγηση\n"
                    "- ΑΠΑΓΟΡΕΥΕΤΑΙ να επινοήσεις ΑΡΙΘΜΟΥΣ (ποσότητες, τιμές, κωδικούς, ημερομηνίες, πλήθη, τηλέφωνα): "
                    "χρησιμοποίησε ΜΟΝΟ ό,τι εμφανίζεται στα αποτελέσματα των εργαλείων ή στο context. Αν δεν το βλέπεις, πες το καθαρά.\n"
                    "- Απάντα ζεστά, έξυπνα, σαν έμπειρος συνεργάτης. Στα ελληνικά."
                )
                messages_for_final = messages + [{"role": "user", "content": final_instruction}]
                response2 = await client.post(
                    f"{settings.deepseek_base_url}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.deepseek_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.deepseek_model,
                        "messages": messages_for_final,
                        "temperature": 0.3,
                        "max_tokens": 2000,
                        "thinking": {"type": "disabled"},
                    },
                )
                response2.raise_for_status()
                body2 = response2.json()
                final_response = body2["choices"][0]["message"]["content"]
        except Exception as e2:
            error_body = ""
            try: error_body = response2.text[:500]
            except: pass
            final_response = f"Ολοκληρώθηκε η ενέργεια αλλά η AI δεν μπόρεσε να απαντήσει ({str(e2)[:100]}). Δες τα αποτελέσματα."
            if error_body:
                final_response += f" [debug: {error_body[:200]}]"
    else:
        final_response = msg.get("content", "Δεν κατάλαβα. Μπορείς να το επαναλάβεις;")

    # ── Build final response ──────────────────────────────────────────
    # Use the LLM's natural response when available (it's smarter than templates)
    llm_response = _clean_response(final_response) if final_response else ""
    if actions_taken and (not llm_response or len(llm_response) < 20):
        # Fallback: build from action results only if LLM response is empty/broken
        parts = ["Ολοκλήρωσα τις παρακάτω ενέργειες:"]
        for a in actions_taken:
            fn = a.get("function", "")
            res = a.get("result", {})
            if fn == "write_week_plan":
                parts.append(f"\n📅 Πλάνο εβδομάδας: {res.get('message','')}")
                if res.get("summary"):
                    parts.append(res["summary"])
            elif fn == "create_appointment":
                parts.append(f"\n📅 Ραντεβού: {a.get('args',{}).get('title','')}")
            elif fn == "create_doctor":
                parts.append(f"\n👨‍⚕️ Νέος: {res.get('name','')}")
            elif fn == "create_case":
                parts.append(f"\n📋 Νέο περιστατικό: {res.get('alias','')}")
            elif fn == "create_sku":
                parts.append(f"\n📦 Νέο προϊόν: {res.get('name','')}")
            elif fn == "create_structure":
                parts.append(f"\n🏥 Νέα δομή: {res.get('name','')}")
            elif fn == "transition_case":
                parts.append(f"\n🔄 {res.get('message','')}")
            elif fn == "repeat_fulfilment":
                parts.append(f"\n🔁 {res.get('message','')}")
            elif fn == "associate_doctor_structure":
                parts.append(f"\n🔗 {res.get('message','')}")
            elif fn == "get_proactive_insights":
                ins = res.get("insights", [])
                if ins: parts.append("\n🔍 " + "\n".join(ins))
            elif fn == "get_doctor_context":
                doc = res.get("doctor", {})
                if doc:
                    parts.append(f"\n👨‍⚕️ {doc.get('name','?')}")
                    if doc.get('specialty'): parts.append(f" | {doc['specialty']}")
                    if doc.get('workplace_type'): parts.append(f" | {doc['workplace_type']}")
                    if doc.get('phone'): parts.append(f" | 📞 {doc['phone']}")
                    if doc.get('email'): parts.append(f" | ✉️ {doc['email']}")
                    if doc.get('area'): parts.append(f" | 📍 {doc['area']}")
                    if doc.get('address'): parts.append(f"\n   Διεύθυνση: {doc['address']}")
                    if doc.get('notes'): parts.append(f"\n   📝 {doc['notes'][:300]}")
                    if doc.get('rx_count_90d'): parts.append(f"\n   📊 Γνωματεύσεις 90d: {doc['rx_count_90d']} | Ανοιχτά: {doc.get('open_cases',0)} | ΜΟ pickup: {doc.get('avg_pickup_days','?')}d")
                structs = res.get("structures", [])
                if structs:
                    parts.append("\n   🏥 Δομές: " + ", ".join(s.get('name','?') for s in structs))
            elif fn == "get_doctor_strategy":
                strat = res.get("strategy", "")
                if strat:
                    parts.append(f"\n🎯 {strat[:500]}")
            elif fn == "list_sources":
                parts.append(f"\n📋 {res.get('total',0)} πηγές")
            elif fn in ("search_products", "search_doctors", "search_structures"):
                items = res.get("items", [])
                if items:
                    parts.append(f"\n🔍 Βρέθηκαν {len(items)}:")
                    for item in items[:3]:
                        if fn == "search_products":
                            parts.append(f"\n📦 {item.get('name','?')}")
                            if item.get('eopyy_code'): parts.append(f" | Κωδ: {item['eopyy_code']}")
                            if item.get('category'): parts.append(f" | {item['category']}")
                            if item.get('price'): parts.append(f" | {item['price']}€")
                            if item.get('description'): parts.append(f"\n   {item['description'][:300]}")
                        elif fn == "search_doctors":
                            parts.append(f"\n👨‍⚕️ {item.get('name','?')}")
                            if item.get('specialty'): parts.append(f" | {item['specialty']}")
                            if item.get('workplace_type') and item.get('workplace_type') != 'OTHER': parts.append(f" | {item['workplace_type']}")
                            if item.get('area'): parts.append(f" | {item['area']}")
                            if item.get('phone'): parts.append(f" | {item['phone']}")
                            if item.get('email'): parts.append(f" | {item['email']}")
                            if item.get('address'): parts.append(f"\n   📍 {item['address']}")
                            if item.get('notes'): parts.append(f"\n   📝 {item['notes'][:200]}")
                            if item.get('rx_count_90d'): parts.append(f"\n   📊 Rx/90d: {item['rx_count_90d']}")
                        elif fn == "search_structures":
                            parts.append(f"\n🏥 {item.get('name','?')}")
                            if item.get('type'): parts.append(f" | {item['type']}")
                            if item.get('city'): parts.append(f" | {item['city']}")
                            if item.get('prefecture'): parts.append(f" | {item['prefecture']}")
                            if item.get('address'): parts.append(f"\n   📍 {item['address']}")
                            if item.get('phone'): parts.append(f"\n   📞 {item['phone']}")
                            if item.get('notes'): parts.append(f"\n   📝 {item['notes'][:200]}")
                else:
                    parts.append(f"\n🔍 Δεν βρέθηκαν αποτελέσματα")
            elif fn == "search_cases":
                items = res.get("items", [])
                if items:
                    parts.append(f"\n📋 Βρέθηκαν {len(items)} περιστατικά:")
                    for item in items[:5]:
                        parts.append(f"\n  • {item.get('alias','?')} ({item.get('status','')}, {item.get('area','')})")
            elif fn == "search_knowledge":
                items = res.get("items", [])
                if items:
                    for item in items[:3]:
                        parts.append(f"\n📚 {item.get('content','')[:600]}")
                else:
                    parts.append(f"\n📚 Δεν βρέθηκε σχετική γνώση στη βάση")
            elif fn == "find_person":
                r = res.get("results", {})
                for cat, label in [("doctors", "👨‍⚕️ Γιατροί"), ("sources", "🔗 Πηγές"), ("structures", "🏥 Δομές")]:
                    entries = r.get(cat, [])
                    if entries:
                        parts.append(f"\n{label}:")
                        for e in entries[:3]:
                            name = e.get("name", "?")
                            extra = e.get("specialty") or e.get("type") or ""
                            notes = e.get("notes") or ""
                            parts.append(f"\n  • {name} ({extra})")
                            if notes: parts.append(f"\n    📝 {notes[:250]}")
            else:
                if res.get("error"):
                    parts.append(f"\n⚠️ {fn}: {res['error']}")
                else:
                    parts.append(f"\n✅ {fn} — επιτυχία")
        final_response = "".join(parts)
    else:
        # LLM's own response is good — use it directly
        final_response = llm_response
    # Fallback for when final_response is still empty
    if not final_response:

        # No tool calls — use model response directly (with cleaning)
        final_response = _clean_response(msg.get("content", ""))
        if not final_response:
            final_response = "Κατάλαβα το αίτημά σου. Πες μου τι άλλο χρειάζεσαι."
    
    # ── Store in memory (MongoDB-backed) ──────────────────────────────
    await _add_to_memory(db, org_id, "assistant", final_response)

    return {
        "response": final_response,
        "actions": actions_taken,
        "requires_confirmation": requires_confirmation,
    }


def _clean_response(text: str) -> str:
    """Aggressively strip ALL XML/HTML, markdown, tool call artifacts."""
    import re
    if not text:
        return text
    # Remove ALL text between < and > (XML/HTML tags and their content)
    text = re.sub(r'<[^>]*>', '', text)
    # Remove any leftover angle bracket fragments
    text = re.sub(r'<[^>]*$', '', text)
    text = re.sub(r'^[^<]*>', '', text)
    # Remove markdown
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()




# ── Function executors ───────────────────────────────────────────────────────

async def _execute_function(
    name: str, args: dict, db: AsyncIOMotorDatabase, org_id: str, user_id: str,
) -> dict:
    """Route function call to the appropriate handler."""
    try:
        handlers = {
            "create_appointment": lambda: _create_appointment(args, db, org_id, user_id),
            "check_conflicts": lambda: _check_conflicts(args, db, org_id),
            "list_appointments": lambda: _list_appointments(args, db, org_id),
            "search_cases": lambda: _search_cases(args, db, org_id),
            "get_case_detail": lambda: _get_case_detail(args, db, org_id),
            "create_case": lambda: _create_case(args, db, org_id, user_id),
            "transition_case": lambda: _transition_case(args, db, org_id, user_id),
            "repeat_fulfilment": lambda: _repeat_fulfilment(args, db, org_id, user_id),
            "search_doctors": lambda: _search_doctors(args, db, org_id),
            "create_doctor": lambda: _create_doctor(args, db, org_id),
            "associate_doctor_structure": lambda: _associate_doctor_structure(args, db, org_id),
            "get_doctor_context": lambda: _get_doctor_context(args, db, org_id),
            "create_structure": lambda: _create_structure(args, db, org_id),
            "search_structures": lambda: _search_structures(args, db, org_id),
            "search_products": lambda: _search_products(args, db, org_id),
            "create_sku": lambda: _create_sku(args, db, org_id),
            "recommend_products_for_doctor": lambda: _recommend_products_for_doctor(args, db, org_id),
            "create_visit": lambda: _create_visit(args, db, org_id, user_id),
            "write_week_plan": lambda: _write_week_plan(db, org_id, user_id),
            "list_sources": lambda: _list_sources(db, org_id),
            "update_doctor": lambda: _update_doctor(args, db, org_id),
            "delete_appointment": lambda: _delete_appointment(args, db, org_id),
            "update_appointment": lambda: _update_appointment(args, db, org_id),
            "web_search": lambda: _web_search(args),
            "get_dashboard": lambda: _get_dashboard(db, org_id),
            "search_knowledge": lambda: _search_knowledge(args, db, org_id),
            "find_person": lambda: _find_person(args, db, org_id),
            "get_proactive_insights": lambda: _get_proactive_insights(db, org_id),
            "get_doctor_strategy": lambda: _get_doctor_strategy(args, db, org_id),
        }
        if name in handlers:
            return await handlers[name]()
        return {"error": f"Unknown function: {name}"}
    except Exception as e:
        return {"error": str(e)}


async def _create_appointment(args, db, org_id, user_id):
    now = datetime.now(timezone.utc)
    at = datetime.fromisoformat(args["at"])
    end_at = datetime.fromisoformat(args["end_at"]) if args.get("end_at") else (at + timedelta(minutes=30))

    # Check conflicts first
    conflicts = await _find_conflicts_raw(db, org_id, at, end_at, args.get("doctor_id"))

    doc = {
        "org_id": org_id, "title": args["title"], "at": at, "end_at": end_at,
        "kind": args.get("kind", "OTHER"), "doctor_id": args.get("doctor_id"),
        "case_id": args.get("case_id"), "location": args.get("location", ""),
        "notes": args.get("notes", ""), "created_by": user_id,
        "created_at": now, "updated_at": now,
    }
    result = await db.appointments.insert_one(doc)
    return {
        "created": str(result.inserted_id),
        "conflicts": [{"title": c.get("title"), "at": str(c.get("at"))} for c in conflicts],
        "has_conflict": len(conflicts) > 0,
    }


async def _check_conflicts(args, db, org_id):
    at = datetime.fromisoformat(args["at"])
    end_at = datetime.fromisoformat(args["end_at"]) if args.get("end_at") else None
    conflicts = await _find_conflicts_raw(db, org_id, at, end_at, args.get("doctor_id"))
    return {"conflicts": [{"title": c.get("title"), "at": str(c.get("at"))} for c in conflicts]}


async def _list_appointments(args, db, org_id):
    filt = {"org_id": org_id}
    if args.get("from_date"): filt["at"] = {"$gte": datetime.fromisoformat(args["from_date"])}
    if args.get("to_date"): filt.setdefault("at", {})["$lte"] = datetime.fromisoformat(args["to_date"])
    if args.get("doctor_id"): filt["doctor_id"] = args["doctor_id"]

    cursor = db.appointments.find(filt).sort("at", 1).limit(20)
    items = []
    async for d in cursor:
        items.append({"id": str(d["_id"]), "title": d["title"], "at": str(d.get("at", "")), "kind": d.get("kind", "")})
    return {"items": items}


async def _search_cases(args, db, org_id):
    filt = {"org_id": org_id}
    query = args.get("query", "")
    if query:
        filt["alias"] = {"$regex": query, "$options": "i"}
    if args.get("status"):
        filt["status"] = args["status"]

    cursor = db.cases.find(filt).sort("created_at", -1).limit(10)
    items = []
    async for c in cursor:
        items.append({
            "id": str(c["_id"]), "alias": c.get("alias", ""), "status": c.get("status", ""),
            "area": c.get("area", ""), "need_categories": c.get("need_categories", []),
            "needs_freehand": (c.get("needs_freehand", "") or "")[:200],
            "est_monthly_value": c.get("est_monthly_value"),
        })
    return {"items": items}


async def _get_case_detail(args, db, org_id):
    c = await db.cases.find_one({"_id": ObjectId(args["case_id"]), "org_id": org_id})
    if not c: return {"error": "Case not found"}
    return {
        "id": str(c["_id"]), "alias": c.get("alias"), "status": c.get("status"),
        "area": c.get("area"), "need_categories": c.get("need_categories", []),
        "needs_freehand": (c.get("needs_freehand", "") or "")[:300],
        "est_monthly_value": c.get("est_monthly_value"),
        "next_repeat_due": str(c.get("next_repeat_due", ""))[:10],
        "cycle_number": c.get("cycle_number", 0),
        "doctor_id": str(c.get("doctor_id", "")),
        "source_id": str(c.get("source_id", "")),
        "no_repeat": c.get("no_repeat", False),
    }


async def _search_doctors(args, db, org_id):
    """Search people ANYWHERE (doctors, sources, structures) using LLM matching."""
    query = args.get("query", "").strip()
    items = []

    # Load ALL entities with their names
    all_entities = []
    async for d in db.doctors.find({"org_id": org_id}, {"name": 1, "specialty": 1, "area": 1}).limit(200):
        all_entities.append({"type": "doctor", "name": d.get("name", ""), "detail": d.get("specialty", ""), "doc": d})
    async for s in db.sources.find({"org_id": org_id}, {"name": 1, "type": 1}).limit(100):
        all_entities.append({"type": "source", "name": s.get("name", ""), "detail": s.get("type", ""), "doc": s})
    async for st in db.structures.find({"org_id": org_id}, {"name": 1, "city": 1}).limit(100):
        all_entities.append({"type": "structure", "name": st.get("name", ""), "detail": st.get("city", ""), "doc": st})

    if not all_entities:
        return {"items": []}

    if not query:
        # No query — return all as summary
        for e in all_entities[:20]:
            items.append(_entity_to_item(e))
        return {"items": items}

    # ── 1ο βήμα: deterministic case/accent-insensitive match ──
    matched_ids = [i for i, _s in _deterministic_name_match(query, [e["name"] for e in all_entities])]

    # ── 2ο βήμα: LLM fallback ──
    if not matched_ids:
        from backend.llm.client import simple_call
        entity_list = "\n".join(f"[{i}] {e['type']}: {e['name']} ({e['detail']})" for i, e in enumerate(all_entities))
        prompt = (
            f"Ο χρήστης ψάχνει: «{query}»\n\n"
            f"Αυτές είναι ΟΛΕΣ οι εγγραφές (γιατροί, πηγές, δομές):\n{entity_list[:4000]}\n\n"
            f"Βρες ΠΟΙΕΣ ταιριάζουν (μπορεί να είναι γραμμένο με τόνους ή χωρίς, διαφορετική σειρά, ή μόνο μέρος του ονόματος). "
            f"Απάντησε ΜΟΝΟ με τους αριθμούς [i] χωρισμένους με κόμμα. Αν δεν ταιριάζει κανένα, απάντησε ΜΟΝΟ 'ΚΑΝΕΝΑ'."
        )
        try:
            answer = (await simple_call(prompt, temperature=0, max_tokens=150, model=settings.deepseek_model_fast)).strip()
        except:
            answer = "ΚΑΝΕΝΑ"
        for token in answer.replace("[", " ").replace("]", " ").split():
            if token.isdigit():
                idx = int(token)
                if 0 <= idx < len(all_entities) and idx not in matched_ids:
                    matched_ids.append(idx)

    for idx in matched_ids[:10]:
        items.append(_entity_to_item(all_entities[idx]))

    return {"items": items}


def _entity_to_item(e: dict) -> dict:
    """Convert an entity dict to a uniform search result item."""
    d = e.get("doc", {})
    t = e["type"]
    if t == "doctor":
        return {
            "type": "doctor",
            "id": str(d["_id"]), "name": d.get("name"), "specialty": d.get("specialty"),
            "phone": d.get("phone", ""), "email": d.get("email", ""),
            "area": d.get("area", ""), "workplace_type": d.get("workplace_type", ""),
            "address": d.get("address", ""),
            "notes": (d.get("notes", "") or "")[:300],
            "rx_count_90d": d.get("rx_count_90d", 0), "open_cases": d.get("open_cases_count", 0),
        }
    elif t == "source":
        return {
            "type": "source",
            "id": str(d["_id"]), "name": d.get("name"),
            "specialty": f"Πηγή ({d.get('type','')})",
            "phone": d.get("phone", ""), "area": "",
            "notes": (d.get("notes", "") or "")[:300],
        }
    else:
        return {
            "type": "structure",
            "id": str(d["_id"]), "name": d.get("name"),
            "specialty": f"Δομή ({d.get('type','')})",
            "phone": d.get("phone", ""), "area": d.get("city", ""),
            "address": d.get("address", ""),
            "notes": (d.get("notes", "") or "")[:300],
        }


async def _create_doctor(args, db, org_id):
    """Create a new doctor. Only 'name' is required."""
    now = datetime.now(timezone.utc)
    doc = {
        "org_id": org_id,
        "name": args["name"].strip(),
        "specialty": args.get("specialty", "OTHER"),
        "workplace_type": args.get("workplace_type", "OTHER"),
        "phone": args.get("phone", ""),
        "email": args.get("email", ""),
        "address": args.get("address", ""),
        "area": args.get("area", ""),
        "notes": args.get("notes", ""),
        "structure_ids": [],
        "relationship": "COLD",
        "max_patients_month": args.get("max_patients_month"),
        "product_categories": args.get("product_categories", []),
        "rx_count_90d": 0,
        "avg_pickup_days": None,
        "open_cases_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.doctors.insert_one(doc)
    return {
        "created": str(result.inserted_id),
        "name": doc["name"],
        "specialty": doc["specialty"],
        "message": f"Ο γιατρός {doc['name']} δημιουργήθηκε με επιτυχία.",
    }


async def _web_search(args):
    query = args["query"]
    results = []

    # 1. Brave Search (δωρεάν tier — το Jina έχει τελειώσει)
    if settings.brave_search_api_key:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip",
                        "X-Subscription-Token": settings.brave_search_api_key,
                    },
                    params={"q": query, "count": 5},
                )
                r.raise_for_status()
                data = r.json()
                for web in data.get("web", {}).get("results", [])[:5]:
                    results.append({
                        "title": web.get("title"), "url": web.get("url"),
                        "description": web.get("description", "")[:300],
                    })
        except: pass

    # 2. Fallback: DuckDuckGo (χωρίς κλειδί)
    if not results:
        try:
            import urllib.parse
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                    follow_redirects=True,
                )
                if r.status_code == 200:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(r.text, "html.parser")
                    for res in soup.select(".result")[:5]:
                        a = res.select_one("a.result__a")
                        snip = res.select_one(".result__snippet")
                        if a:
                            results.append({
                                "title": a.get_text(strip=True),
                                "url": a.get("href", ""),
                                "description": snip.get_text(strip=True)[:300] if snip else "",
                            })
        except: pass

    if not results:
        return {"error": "No search results found"}
    return {"results": results}


async def _get_dashboard(db, org_id):
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    active = await db.cases.count_documents({"org_id": org_id, "status": "ACTIVE"})
    at_risk = await db.cases.count_documents({"org_id": org_id, "status": "AT_RISK"})
    new_m = await db.cases.count_documents({"org_id": org_id, "created_at": {"$gte": month_start}})
    return {"active_cases": active, "at_risk": at_risk, "new_this_month": new_m}


async def _find_conflicts_raw(db, org_id, at, end_at, doctor_id=None):
    """Raw conflict finder for agent use."""
    if not end_at: end_at = at + timedelta(minutes=30)

    buffer = timedelta(minutes=15)
    cursor = db.appointments.find({
        "org_id": org_id,
        "at": {"$lte": end_at + buffer},
        "$or": [
            {"end_at": {"$gte": at - buffer}},
            {"end_at": None, "at": {"$gte": at - buffer}},
        ],
    }).sort("at", 1)

    conflicts = []
    async for doc in cursor:
        doc_at = doc.get("at")
        doc_end = doc.get("end_at") or (doc_at + timedelta(minutes=30))
        if doc_at and doc_end and doc_at < end_at and doc_end > at:
            conflicts.append(doc)
    return conflicts


# ── NEW: Product & Doctor Intelligence Functions ─────────────────────────────

# Specialty → product category mapping (Greek)
# Αντιστοίχιση κατηγοριών σε λέξεις-κλειδιά (ελληνικά + αγγλικά) —
# Οι κατηγορίες των SKU του χρήστη είναι στα ελληνικά («Κλινική Διατροφή» κλπ.)
# και ΔΕΝ ταιριάζουν με τα αγγλικά codes του SPECIALTY_PRODUCT_MAP.
CATEGORY_ALIASES = {
    "WOUND": ["τραυμ", "κατακλι", "επιθεμ", "ιατροτεχνολογικ", "wound"],
    "ONS": ["κλινικ διατροφ", "σιτισ", "ποσιμο", "εντερικ", "ons", "enteral"],
    "ENTERAL": ["γαστροστομ", "καθετηρ", "εντερικ", "σωληνα", "tube"],
    "CATHETER": ["καθετηρ", "ουροσυλλεκτ", "ουρηση", "catheter"],
    "ORTHO": ["ορθοπαιδικ", "αμαξιδ", "περιπατητ", "κηδεμον", "ναρθηκ", "ortho"],
    "HYALURONIC": ["υαλουρονικ", "hyaluron"],
    "OSTOMY": ["στομι", "ostomy"],
    "TRACHEO": ["τραχει", "tracheo"],
    "INCONT": ["ακρατει", "παν", "incont"],
    "URO": ["ουρολογ", "ουροσυλλεκτ", "ουρηση"],
    "SUPPLEMENTS": ["συμπληρωμα", "βιταμιν", "πρωτεϊν", "πρωτειν", "αμινοξε"],
    "GYN": ["γυναικολογικ"],
    "OXYGEN": ["οξυγον", "oxygen"],
    "ONCO_SUPPORT": ["ογκολογ", "onco"],
}

SPECIALTY_PRODUCT_MAP = {
    "ΟΡΘΟΠΕΔΙΚΉ": ["WOUND", "ORTHO", "HYALURONIC"],
    "ORTHOPEDIC": ["WOUND", "ORTHO", "HYALURONIC"],
    "ΧΕΙΡΟΥΡΓΟΣ": ["WOUND", "CATHETER", "OSTOMY", "TRACHEO"],
    "ΧΕΙΡΟΥΡΓΙΚΉ": ["WOUND", "CATHETER", "OSTOMY", "TRACHEO"],
    "SURGEON": ["WOUND", "CATHETER", "OSTOMY", "TRACHEO"],
    "ΠΑΘΟΛΟΓΟΣ": ["ONS", "WOUND", "CATHETER", "ENTERAL"],
    "INTERNIST": ["ONS", "WOUND", "CATHETER", "ENTERAL"],
    "ΦΥΣΙΑΤΡΟΣ": ["WOUND", "ORTHO", "HYALURONIC"],
    "PHYSIATRIST": ["WOUND", "ORTHO", "HYALURONIC"],
    "ΝΕΥΡΟΛΟΓΟΣ": ["WOUND", "CATHETER", "ONS"],
    "NEUROLOGIST": ["WOUND", "CATHETER", "ONS"],
    "ΟΥΡΟΛΟΓΟΣ": ["CATHETER", "INCONT", "URO"],
    "UROLOGIST": ["CATHETER", "INCONT", "URO"],
    "ΟΓΚΟΛΟΓΟΣ": ["ONS", "WOUND", "CATHETER", "ONCO_SUPPORT"],
    "ONCOLOGIST": ["ONS", "WOUND", "CATHETER", "ONCO_SUPPORT"],
    "ΠΝΕΥΜΟΝΟΛΟΓΟΣ": ["OXYGEN", "TRACHEO", "ONS"],
    "PULMONOLOGIST": ["OXYGEN", "TRACHEO", "ONS"],
    "ΓΑΣΤΡΕΝΤΕΡΟΛΟΓΟΣ": ["ONS", "ENTERAL", "OSTOMY"],
    "GASTROENTEROLOGIST": ["ONS", "ENTERAL", "OSTOMY"],
    "ΔΕΡΜΑΤΟΛΟΓΟΣ": ["WOUND", "ORTHO"],
    "DERMATOLOGIST": ["WOUND", "ORTHO"],
    "ΚΑΡΔΙΟΛΟΓΟΣ": ["ONS", "WOUND"],
    "CARDIOLOGIST": ["ONS", "WOUND"],
    "ΕΝΔΟΚΡΙΝΟΛΟΓΟΣ": ["WOUND", "ONS", "ENTERAL"],
    "ENDOCRINOLOGIST": ["WOUND", "ONS", "ENTERAL"],
    "ΓΗΡΙΑΤΡΟΣ": ["WOUND", "INCONT", "ONS", "CATHETER"],
    "GERIATRICIAN": ["WOUND", "INCONT", "ONS", "CATHETER"],
    "ΝΕΦΡΟΛΟΓΟΣ": ["CATHETER", "INCONT", "ONS"],
    "NEPHROLOGIST": ["CATHETER", "INCONT", "ONS"],
    "ΓΕΝΙΚΟΣ": ["WOUND", "ONS", "CATHETER"],
    "GP": ["WOUND", "ONS", "CATHETER"],
    "OTHER": ["WOUND", "ONS"],
}


async def _search_products(args, db, org_id):
    """Search products by keyword or category."""
    filt = {"org_id": org_id, "active": True}
    query = args.get("query", "")
    category = args.get("category", "")

    if query:
        escaped = _accent_regex(query)
        filt["$or"] = [
            {"commercial_name": {"$regex": escaped, "$options": "i"}},
            {"description": {"$regex": escaped, "$options": "i"}},
            {"category": {"$regex": escaped, "$options": "i"}},
        ]
    if category:
        filt["category"] = category.upper()

    cursor = db.skus.find(filt).limit(15)
    items = []
    async for s in cursor:
        items.append({
            "id": str(s["_id"]),
            "product_id": s.get("product_id", ""),
            "name": s.get("commercial_name", ""),
            "category": s.get("category", ""),
            "price": s.get("reimb_price", 0),
            "eopyy_code": s.get("eopyy_code", ""),
            "description": (s.get("description", "") or "")[:300],
        })
    return {"items": items, "total": len(items)}


async def _search_structures(args, db, org_id):
    """Search structures by name, city, or prefecture."""
    filt = {"org_id": org_id}
    query = args.get("query", "")
    if query:
        escaped = _accent_regex(query)
        filt["$or"] = [
            {"name": {"$regex": escaped, "$options": "i"}},
            {"city": {"$regex": escaped, "$options": "i"}},
            {"prefecture": {"$regex": escaped, "$options": "i"}},
        ]

    cursor = db.structures.find(filt).limit(100)
    items = []
    async for s in cursor:
        items.append({
            "id": str(s["_id"]),
            "name": s.get("name", ""),
            "type": s.get("type", ""),
            "city": s.get("city", ""),
            "prefecture": s.get("prefecture", ""),
            "address": s.get("address", ""),
            "phone": s.get("phone", ""),
            "notes": (s.get("notes", "") or "")[:300],
        })
    return {"items": items, "total": len(items)}


async def _products_for_specialty(db, org_id, specialty: str, limit: int = 15) -> list[dict]:
    """Προϊόντα που ταιριάζουν στην ειδικότητα — με ελληνικές ΚΑΙ αγγλικές κατηγορίες.

    Σκοράρει κάθε SKU βάσει λέξεων-κλειδιών της ειδικότητας (όνομα+κατηγορία+περιγραφή,
    χωρίς τόνους/πεζά). Επιστρέφει τα κορυφαία (score, μετά τιμή).
    """
    cats = SPECIALTY_PRODUCT_MAP.get((specialty or "OTHER").upper(), ["WOUND", "ONS"])
    aliases = []
    for c in cats:
        aliases.extend(CATEGORY_ALIASES.get(c, [c.lower()]))
    scored = []
    async for p in db.skus.find({"org_id": org_id, "active": True}).limit(500):
        txt = _norm_gr(" ".join(filter(None, [
            p.get("commercial_name", ""), p.get("category", ""),
            p.get("description", "") or "",
        ])))
        score = 0
        for a in aliases:
            an = _norm_gr(a).replace(" ", "")
            if an and an in txt.replace(" ", ""):
                score += len(an)
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda x: (-x[0], -(x[1].get("reimb_price") or 0)))
    return [
        {
            "id": str(p["_id"]),
            "name": p.get("commercial_name", ""),
            "category": p.get("category", ""),
            "price": p.get("reimb_price", 0),
            "eopyy_code": p.get("eopyy_code", ""),
            "product_id": p.get("product_id", ""),
            "description": (p.get("description", "") or "")[:200],
        }
        for _s, p in scored[:limit]
    ]


async def _recommend_products_for_doctor(args, db, org_id):
    """Recommend products based on doctor's specialty."""
    doctor_name = args.get("doctor_name", "").strip()
    if not doctor_name:
        return {"error": "Δώσε όνομα γιατρού"}

    # Find doctor
    # ── 1ο βήμα: deterministic case/accent-insensitive match ──
    doctor = None
    names = []
    async for d in db.doctors.find({"org_id": org_id}, {"name": 1}).limit(300):
        names.append(d.get("name", ""))
    hits = _deterministic_name_match(doctor_name, names)
    if hits:
        doctor = await db.doctors.find_one({"org_id": org_id, "name": names[hits[0][0]]})

    # ── 2ο βήμα: regex fallback ──
    if doctor is None:
        import unicodedata
        def _strip_acc(s):
            return ''.join(ch for ch in unicodedata.normalize('NFD', s) if unicodedata.category(ch) != 'Mn')
        name_parts = [p for p in doctor_name.split() if len(p) >= 3]
        if not name_parts:
            name_parts = [doctor_name]
        or_clauses = [{"name": {"$regex": _accent_regex(p), "$options": "i"}} for p in name_parts]
        best_score = 0
        async for cand in db.doctors.find({"org_id": org_id, "$or": or_clauses}).limit(20):
            cand_norm = _strip_acc(cand.get("name", "").lower())
            score = sum(len(p) for p in name_parts if _strip_acc(p).lower() in cand_norm)
            if score > best_score:
                best_score = score
                doctor = cand
        if doctor is None and name_parts:
            doctor = await db.doctors.find_one({"org_id": org_id, "$or": or_clauses})
    if not doctor:
        return {"error": f"Δεν βρέθηκε γιατρός με όνομα '{doctor_name}'"}

    specialty = (doctor.get("specialty") or "OTHER").upper()

    # Προϊόντα που ταιριάζουν στην ειδικότητα (ελληνικές + αγγλικές κατηγορίες)
    products = await _products_for_specialty(db, org_id, specialty, limit=15)

    return {
        "doctor": {
            "name": doctor.get("name"),
            "specialty": doctor.get("specialty"),
            "rx_count_90d": doctor.get("rx_count_90d", 0),
            "open_cases": doctor.get("open_cases_count", 0),
        },
        "relevant_categories": SPECIALTY_PRODUCT_MAP.get(specialty, ["WOUND", "ONS"]),
        "recommended_products": products,
        "total_products": len(products),
    }


async def _get_doctor_context(args, db, org_id):
    """Full doctor profile with structure, stats, and product recommendations."""
    doctor_name = args.get("doctor_name", "").strip()
    if not doctor_name:
        return {"error": "Δώσε όνομα γιατρού"}

    # ── 1ο βήμα: deterministic case/accent-insensitive match (χωρίς LLM) ──
    doctor = None
    names = []
    async for d in db.doctors.find({"org_id": org_id}, {"name": 1}).limit(300):
        names.append(d.get("name", ""))
    hits = _deterministic_name_match(doctor_name, names)
    if hits:
        doctor = await db.doctors.find_one({"org_id": org_id, "name": names[hits[0][0]]})

    # ── 2ο βήμα: accent/case regex fallback ──
    if doctor is None:
        name_parts = [p for p in doctor_name.split() if len(p) >= 3]
        if not name_parts:
            name_parts = [doctor_name]
        or_clauses = [{"name": {"$regex": _accent_regex(p), "$options": "i"}} for p in name_parts]
        import unicodedata
        def _strip_acc(s):
            return ''.join(ch for ch in unicodedata.normalize('NFD', s) if unicodedata.category(ch) != 'Mn')
        best_score = 0
        async for cand in db.doctors.find({"org_id": org_id, "$or": or_clauses}).limit(20):
            cand_norm = _strip_acc(cand.get("name", "").lower())
            score = sum(len(p) for p in name_parts if _strip_acc(p).lower() in cand_norm)
            if score > best_score:
                best_score = score
                doctor = cand
        if doctor is None:
            doctor = await db.doctors.find_one({"org_id": org_id, "$or": or_clauses})
    if not doctor:
        return {"error": f"Δεν βρέθηκε γιατρός με όνομα '{doctor_name}'"}

    structures = []
    for sid in doctor.get("structure_ids", [])[:3]:
        try:
            s = await db.structures.find_one({"_id": ObjectId(sid), "org_id": org_id})
            if s:
                structures.append({"id": str(s["_id"]), "name": s.get("name"), "city": s.get("city", "")})
        except Exception:
            pass

    # ── Appointment history with notes (chronological) ──────────────────
    doc_id = str(doctor["_id"])
    appointment_history = []
    async for appt in db.appointments.find(
        {"org_id": org_id, "doctor_id": doc_id}
    ).sort("at", 1).limit(30):
        entry = {
            "at": str(appt.get("at", ""))[:16],
            "title": appt.get("title", ""),
            "notes": appt.get("notes", ""),
        }
        # Include note history if the appointment has chronological notes
        if appt.get("note_history"):
            entry["note_history"] = appt["note_history"]
        appointment_history.append(entry)

    # Also pull episodic memory episodes for this doctor
    from backend.llm.memory import get_recent_episodes
    episodes = []
    try:
        episodes = await get_recent_episodes(
            db, org_id, days=180, entity_name=doctor.get("name", ""), limit=10,
        )
    except Exception:
        episodes = []

    recs = await _recommend_products_for_doctor(args, db, org_id)

    return {
        "doctor": {
            "id": doc_id,
            "name": doctor.get("name"),
            "specialty": doctor.get("specialty"),
            "workplace_type": doctor.get("workplace_type", ""),
            "phone": doctor.get("phone", ""),
            "email": doctor.get("email", ""),
            "address": doctor.get("address", ""),
            "area": doctor.get("area", ""),
            "notes": doctor.get("notes", ""),
            "rx_count_90d": doctor.get("rx_count_90d", 0),
            "open_cases": doctor.get("open_cases_count", 0),
            "avg_pickup_days": doctor.get("avg_pickup_days"),
            "last_visit": str(doctor.get("last_visit_at", ""))[:10],
        },
        "structures": structures,
        "recommended_products": recs.get("recommended_products", []),
        "relevant_categories": recs.get("relevant_categories", []),
        "appointment_history": appointment_history,
        "episodes": episodes,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  NEW: Case Management Functions
# ═══════════════════════════════════════════════════════════════════════════════

async def _create_case(args, db, org_id, user_id):
    """Create a new case (intake). source_id προαιρετικό."""
    now = datetime.now(timezone.utc)
    doc = {
        "org_id": org_id,
        "source_id": args.get("source_id", ""),
        "alias": args["alias"].strip(),
        "status": "NEW",
        "area": args.get("area", ""),
        "amka": args.get("amka", ""),
        "need_categories": args.get("need_categories", []),
        "needs_freehand": args.get("needs_freehand", ""),
        "flags": [],
        "created_by": user_id,
        "created_at": now,
        "updated_at": now,
        "status_history": [{"from_status": None, "to": "NEW", "at": now, "by": user_id}],
    }
    result = await db.cases.insert_one(doc)
    if args.get("source_id") and ObjectId.is_valid(args["source_id"]):
        await db.sources.update_one(
            {"_id": ObjectId(args["source_id"])},
            {"$inc": {"cases_brought": 1}, "$set": {"last_referral_at": now}},
        )
    return {
        "created": str(result.inserted_id),
        "alias": doc["alias"],
        "status": "NEW",
        "message": f"Το περιστατικό '{doc['alias']}' δημιουργήθηκε.",
    }


async def _transition_case(args, db, org_id, user_id):
    """Transition a case to a new status."""
    from backend.core.state_machine import transition, TransitionError
    try:
        case_id = args["case_id"]
        action = args["action"]
        payload = {}
        if action == "assess":
            payload["need_categories"] = args.get("need_categories", [])
            if args.get("est_monthly_value"):
                payload["est_monthly_value"] = args["est_monthly_value"]
        elif action == "to_doctor" or action == "reassign":
            payload["doctor_id"] = args.get("doctor_id")
        elif action == "lose":
            payload["lost_reason"] = args.get("lost_reason", "OTHER")
        elif action == "close":
            payload["closed_reason"] = args.get("closed_reason", "RECOVERED")

        updated = await transition(db=db, case_id=case_id, action=action, payload=payload, user_id=user_id)
        return {
            "success": True,
            "case_id": case_id,
            "new_status": updated.get("status"),
            "message": f"Το περιστατικό μεταφέρθηκε σε '{updated.get('status')}'.",
        }
    except TransitionError as e:
        return {"error": str(e), "status_code": e.status_code}


async def _repeat_fulfilment(args, db, org_id, user_id):
    """One-tap repeat fulfilment."""
    from backend.core.state_machine import transition, TransitionError
    now = datetime.now(timezone.utc)
    case_id = args["case_id"]
    case = await db.cases.find_one({"_id": ObjectId(case_id), "org_id": org_id})
    if not case:
        return {"error": "Case not found"}
    if case["status"] not in ("ACTIVE", "AT_RISK"):
        return {"error": "Repeat only allowed for ACTIVE or AT_RISK cases"}
    if case.get("no_repeat"):
        return {"error": "Cannot repeat a one-shot (no_repeat) case"}

    last_fulfil = await db.fulfilments.find_one(
        {"case_id": case_id, "org_id": org_id}, sort=[("fulfilled_at", -1)])
    if not last_fulfil:
        return {"error": "No previous fulfilment found"}

    duration_days = last_fulfil.get("duration_days", 30)
    new_fulfil = {
        "org_id": org_id, "case_id": case_id, "patient_id": case.get("patient_id"),
        "items": last_fulfil.get("items", []), "qty_total": last_fulfil.get("qty_total", 0),
        "duration_days": duration_days, "fulfilled_at": now,
        "cycle_number": case.get("cycle_number", 0) + 1,
        "created_by": user_id, "created_at": now, "updated_at": now,
    }
    result = await db.fulfilments.insert_one(new_fulfil)
    try:
        updated = await transition(db=db, case_id=case_id, action="fulfil", payload={
            "fulfilment_id": str(result.inserted_id), "duration_days": duration_days,
            "no_repeat": case.get("no_repeat", False),
        }, user_id=user_id)
        return {
            "success": True, "case_id": case_id,
            "cycle_number": updated.get("cycle_number"),
            "message": f"Επανάληψη ολοκληρώθηκε! Κύκλος #{updated.get('cycle_number')}.",
        }
    except TransitionError as e:
        await db.fulfilments.delete_one({"_id": result.inserted_id})
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
#  Structure Functions
# ═══════════════════════════════════════════════════════════════════════════════

async def _create_structure(args, db, org_id):
    """Create a new structure."""
    now = datetime.now(timezone.utc)
    doc = {
        "org_id": org_id,
        "name": args["name"].strip(),
        "type": args.get("type", "OTHER"),
        "city": args.get("city", ""),
        "prefecture": args.get("prefecture", ""),
        "address": args.get("address", ""),
        "phone": args.get("phone", ""),
        "notes": "",
        "priority": 3,
        "created_at": now, "updated_at": now,
    }
    result = await db.structures.insert_one(doc)
    return {
        "created": str(result.inserted_id),
        "name": doc["name"],
        "message": f"Η δομή '{doc['name']}' δημιουργήθηκε.",
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  NEW: SKU Functions
# ═══════════════════════════════════════════════════════════════════════════════

async def _create_sku(args, db, org_id):
    """Create a new SKU."""
    now = datetime.now(timezone.utc)
    doc = {
        "org_id": org_id,
        "eopyy_code": args.get("eopyy_code", ""),
        "commercial_name": args["commercial_name"].strip(),
        "category": args.get("category", "OTHER"),
        "reimb_price": args.get("reimb_price", 0),
        "description": args.get("description", ""),
        "active": True,
        "created_at": now, "updated_at": now,
    }
    result = await db.skus.insert_one(doc)
    return {
        "created": str(result.inserted_id),
        "name": doc["commercial_name"],
        "message": f"Το προϊόν '{doc['commercial_name']}' δημιουργήθηκε.",
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  NEW: Visit Functions
# ═══════════════════════════════════════════════════════════════════════════════

async def _create_visit(args, db, org_id, user_id):
    """Log a new visit."""
    now = datetime.now(timezone.utc)
    doc = {
        "org_id": org_id,
        "target_type": args["target_type"],
        "target_id": args["target_id"],
        "at": now,
        "channel": args.get("channel", "IN_PERSON"),
        "outcome": args.get("outcome", "NEUTRAL"),
        "voice_note_text": args.get("voice_note_text", ""),
        "extracted_actions": [],
        "created_by": user_id,
        "created_at": now, "updated_at": now,
    }
    result = await db.visits.insert_one(doc)
    # Update doctor last_visit_at
    if args["target_type"] == "DOCTOR":
        await db.doctors.update_one(
            {"_id": ObjectId(args["target_id"]), "org_id": org_id},
            {"$set": {"last_visit_at": now, "updated_at": now}},
        )
    return {
        "created": str(result.inserted_id),
        "target_type": args["target_type"],
        "message": "Η επίσκεψη καταγράφηκε.",
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  NEW: Week Plan Functions
# ═══════════════════════════════════════════════════════════════════════════════

DAY_WORDS = {
    "δευτερα": 0, "δευτέρα": 0, "τριτη": 1, "τρίτη": 1,
    "τεταρτη": 2, "τετάρτη": 2, "πεμπτη": 3, "πέμπτη": 3,
    "παρασκευη": 4, "παρασκευή": 4,
    "σαββατο": -1, "σάββατο": -1, "κυριακη": -1, "κυριακή": -1,
}

DAY_NAMES = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή"]


def _preferred_day_from_notes(notes: str) -> Optional[int]:
    """Βρίσκει προτιμώμενη μέρα από τις σημειώσεις.

    π.χ. «δέχεται επίσκεψη κάθε Πέμπτη» → 3. «μόνο Δευτέρα» → 0.
    Σαβ/Κυρ → -1 (δεν μπαίνει σε εργάσιμο πρόγραμμα).
    """
    n = _norm_gr(notes or "")
    found = []
    for word, day in DAY_WORDS.items():
        if word in n:
            found.append(day)
    if not found:
        return None
    # Αν αναφέρεται «μόνο» ή «κάθε» → ξεκάθαρη προτίμηση
    if "μονο" in n or "καθε" in n:
        return found[0] if found[0] is not None else None
    return found[0]


def _parse_plan_week(user_message: str, now: datetime) -> datetime:
    """Προσδιορίζει την εβδομάδα-στόχο του πλάνου.

    - «Δευτέρα 24/8» ή «24/8» → Δευτέρα της εβδομάδας της 24/8
    - «επόμενη εβδομάδα» ή Σαβ/Κυρ σήμερα → επόμενη Δευτέρα
    - αλλιώς → Δευτέρα της τρέχουσας εβδομάδας
    """
    import re as _re
    msg_lower = (user_message or "").lower()
    m = _re.search(r'(\d{1,2})[/\-.](\d{1,2})', msg_lower)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        try:
            target = now.replace(day=day, month=month, hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            target = None
        if target is None:
            pass
        else:
            if target.month < now.month or (target.month == now.month and target.day < now.day):
                try:
                    target = target.replace(year=now.year + 1)
                except ValueError:
                    pass
            return target - timedelta(days=target.weekday())

    if now.weekday() >= 5 or "επομενη" in msg_lower or "επόμενη" in msg_lower:
        return (now + timedelta(days=(7 - now.weekday()))).replace(hour=0, minute=0, second=0, microsecond=0)

    return (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


async def _write_week_plan(db, org_id, user_id, target_week: Optional[datetime] = None):
    """Εβδομαδιαίο πλάνο ΕΞΥΠΝΟ:
    - ΜΟΝΟ Δευτέρα-Παρασκευή (ποτέ ΣΚ)
    - διαβάζει σημειώσεις (μέρες δεκτικότητας: «κάθε Πέμπτη» κλπ.)
    - μοιράζει ομοιόμορφα, όχι 8 σε μία μέρα
    - γεωγραφική ομαδοποίηση
    """
    now = datetime.now(timezone.utc)
    if target_week is None:
        target_week = _parse_plan_week("", now)
    monday = target_week.replace(hour=0, minute=0, second=0, microsecond=0)
    created = 0
    summary = []
    warnings = []

    # Μέρες: πέντε εργάσιμες της εβδομάδας-στόχου
    days = [monday + timedelta(days=i) for i in range(5)]
    day_load = {i: 0 for i in range(5)}
    MAX_PER_DAY = 6

    # ── Φόρτωσε γιατρούς που χρειάζονται επίσκεψη ──
    cutoff_30d = now - timedelta(days=30)
    doctors = []
    async for doc in db.doctors.find({"org_id": org_id}).limit(200):
        last = doc.get("last_visit_at")
        if last and hasattr(last, 'tzinfo') and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last and last >= cutoff_30d:
            continue  # πρόσφατη επίσκεψη — δεν είναι προτεραιότητα
        preferred = _preferred_day_from_notes(doc.get("notes", ""))
        doctors.append({
            "doc": doc,
            "preferred": preferred,
            "area": (doc.get("area") or "").strip() or "Άγνωστη περιοχή",
        })

    if not doctors:
        return {
            "success": True, "created_appointments": 0,
            "summary": "", "message": "Δεν βρέθηκαν γιατροί που να χρειάζονται επίσκεψη.",
            "warnings": [],
        }

    # Ομαδοποίηση ανά περιοχή
    areas: dict[str, list] = {}
    for d in doctors:
        areas.setdefault(d["area"], []).append(d)

    def pick_day(preferred):
        if preferred is not None and preferred >= 0 and day_load[preferred] < MAX_PER_DAY:
            return preferred
        # λιγότερο φορτωμένη μέρα
        best = min(range(5), key=lambda i: day_load[i])
        return best

    # Σειρά: πρώτα αυτοί με ξεκάθαρη προτίμηση μέρας (π.χ. «μόνο Πέμπτη»)
    doctors_sorted = sorted(
        doctors,
        key=lambda d: (0 if d["preferred"] is not None and d["preferred"] >= 0 else 1, -(d["preferred"] is not None))
    )

    weekend_skipped = []
    for entry in doctors_sorted:
        doc = entry["doc"]
        preferred = entry["preferred"]
        if preferred == -1:
            weekend_skipped.append(doc.get("name", "?"))
            continue

        day_idx = pick_day(preferred)
        if day_load[day_idx] >= MAX_PER_DAY:
            day_idx = min(range(5), key=lambda i: day_load[i])

        # Ώρες: 09:30 + slot*1h (ώρα Ελλάδας = UTC+3 → 06:30 UTC)
        slot = day_load[day_idx]
        at = days[day_idx] + timedelta(hours=6, minutes=30 + slot * 60)

        struct_name = ""
        sids = doc.get("structure_ids", [])
        if sids:
            try:
                s = await db.structures.find_one({"_id": ObjectId(sids[0]), "org_id": org_id})
                if s:
                    struct_name = s.get("name", "")
            except Exception:
                pass

        notes_snip = (doc.get("notes") or "").strip()
        notes_field = f"📍 {entry['area']} · {doc.get('specialty','')}"
        if notes_snip:
            notes_field += f" · 📝 {notes_snip[:2000]}"
        if preferred is not None and preferred >= 0:
            notes_field += f" · [προτίμηση: {DAY_NAMES[preferred]}]"

        await db.appointments.insert_one({
            "org_id": org_id,
            "title": f"Επίσκεψη: {doc.get('name')}",
            "at": at.replace(tzinfo=timezone.utc),
            "end_at": (at + timedelta(minutes=45)).replace(tzinfo=timezone.utc),
            "kind": "DOCTOR", "doctor_id": str(doc["_id"]),
            "location": struct_name,
            "notes": notes_field,
            "created_by": user_id, "created_at": now, "updated_at": now,
        })
        summary.append(f"  {DAY_NAMES[day_idx]} {at.strftime('%H:%M')} — {doc.get('name')} ({entry['area']})")
        day_load[day_idx] += 1
        created += 1

    if weekend_skipped:
        warnings.append(f"{len(weekend_skipped)} γιατροί αναφέρουν Σαββατοκύριακο στις σημειώσεις τους — δεν μπήκαν σε εργάσιμο πρόγραμμα.")

    # ── Phase 2: AT_RISK περιστατικά σε μέρες με χώρο ──
    at_risk_count = 0
    async for case in db.cases.find({"org_id": org_id, "status": "AT_RISK"}).limit(10):
        day_idx = min(range(5), key=lambda i: day_load[i])
        slot = day_load[day_idx]
        at = days[day_idx] + timedelta(hours=12, minutes=30 + slot * 45)
        await db.appointments.insert_one({
            "org_id": org_id,
            "title": f"ΕΠΕΙΓΟΝ: {case.get('alias','')}",
            "at": at.replace(tzinfo=timezone.utc),
            "end_at": (at + timedelta(minutes=30)).replace(tzinfo=timezone.utc),
            "kind": "CASE", "case_id": str(case["_id"]),
            "notes": "Σε κίνδυνο — χρειάζεται άμεση επανάληψη",
            "created_by": user_id, "created_at": now, "updated_at": now,
        })
        summary.append(f"  {DAY_NAMES[day_idx]} {at.strftime('%H:%M')} — 🚨 {case.get('alias','')}")
        day_load[day_idx] += 1
        at_risk_count += 1

    load_summary = " · ".join(f"{DAY_NAMES[i]}: {day_load[i]}" for i in range(5))
    return {
        "success": True,
        "created_appointments": created,
        "at_risk_added": at_risk_count,
        "week_start": monday.isoformat(),
        "load": load_summary,
        "summary": "\n".join(summary) if summary else "Δεν βρέθηκαν ενέργειες.",
        "message": f"Δημιουργήθηκαν {created} επισκέψεις ({monday.strftime('%d/%m')}–{(monday+timedelta(days=4)).strftime('%d/%m')}). Φόρτος: {load_summary}",
        "warnings": warnings,
    }


async def _list_sources(db, org_id):
    """List all sources for case creation reference."""
    cursor = db.sources.find({"org_id": org_id}).limit(200)
    items = []
    async for s in cursor:
        items.append({
            "id": str(s["_id"]), "name": s.get("name"), "type": s.get("type"),
            "phone": s.get("phone", ""), "notes": (s.get("notes", "") or "")[:300],
        })
    return {"items": items, "total": len(items)}


async def _associate_doctor_structure(args, db, org_id):
    """Fuzzy-match structure name and associate with doctor."""
    doctor_name = args.get("doctor_name", "").strip()
    structure_name = args.get("structure_name", "").strip()
    if not doctor_name or not structure_name:
        return {"error": "Χρειάζονται doctor_name και structure_name"}

    # Find doctor
    escaped_doc = _accent_regex(doctor_name)
    doctor = await db.doctors.find_one({"org_id": org_id, "name": {"$regex": escaped_doc, "$options": "i"}})
    if not doctor:
        return {"error": f"Δεν βρέθηκε γιατρός '{doctor_name}'"}

    # Fuzzy find structure
    escaped_struct = _accent_regex(structure_name)
    structure = await db.structures.find_one({"org_id": org_id, "name": {"$regex": escaped_struct, "$options": "i"}})
    if not structure:
        # Try broader search — tokenize
        tokens = structure_name.split()
        for token in tokens:
            if len(token) >= 3:
                esc = _accent_regex(token)
                structure = await db.structures.find_one({"org_id": org_id, "name": {"$regex": esc, "$options": "i"}})
                if structure:
                    break

    if not structure:
        # Return all structures for user to choose
        cursor = db.structures.find({"org_id": org_id}).limit(20)
        all_structs = []
        async for s in cursor:
            all_structs.append({"id": str(s["_id"]), "name": s.get("name"), "city": s.get("city", "")})
        return {
            "error": f"Δεν βρέθηκε δομή που να ταιριάζει με '{structure_name}'.",
            "available_structures": all_structs,
            "suggestion": "Διάλεξε μία από τις διαθέσιμες δομές ή δημιούργησε την πρώτα με create_structure.",
        }

    # Associate
    struct_id = str(structure["_id"])
    structure_ids = doctor.get("structure_ids", [])
    if struct_id not in structure_ids:
        structure_ids.append(struct_id)
    await db.doctors.update_one(
        {"_id": doctor["_id"]},
        {"$set": {"structure_ids": structure_ids, "updated_at": datetime.now(timezone.utc)}},
    )
    return {
        "success": True,
        "doctor": doctor.get("name"),
        "structure": structure.get("name"),
        "message": f"Ο γιατρός {doctor.get('name')} συσχετίστηκε με '{structure.get('name')}'.",
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  NEW: Proactive Insights
# ═══════════════════════════════════════════════════════════════════════════════

async def _get_proactive_insights(db, org_id):
    """Analyze data and return proactive suggestions."""
    now = datetime.now(timezone.utc)
    insights = []

    def _ensure_utc(dt):
        if dt is None: return None
        if dt.tzinfo is None: return dt.replace(tzinfo=timezone.utc)
        return dt

    # AT_RISK cases
    at_risk = await db.cases.count_documents({"org_id": org_id, "status": "AT_RISK"})
    if at_risk:
        insights.append(f"🚨 {at_risk} περιστατικά σε κίνδυνο — χρειάζονται άμεση ενέργεια.")

    # Doctors not visited in 45+ days
    cutoff = now - timedelta(days=45)
    async for doc in db.doctors.find({"org_id": org_id}):
        last_visit = _ensure_utc(doc.get("last_visit_at"))
        if not last_visit or last_visit < cutoff:
            insights.append(f"👨‍⚕️ Ο γιατρός {doc.get('name')} έχει να επισκεφτεί 45+ μέρες.")

    # Cases approaching repeat (ACTIVE with next_repeat_due within 7 days)
    soon = now + timedelta(days=7)
    repeat_soon = await db.cases.count_documents({
        "org_id": org_id, "status": "ACTIVE",
        "next_repeat_due": {"$lte": soon, "$gte": now},
    })
    if repeat_soon:
        insights.append(f"🔁 {repeat_soon} επαναλήψεις λήγουν μέσα σε 7 μέρες.")

    # NEW cases older than 3 days
    old_new = await db.cases.count_documents({
        "org_id": org_id, "status": "NEW",
        "created_at": {"$lte": now - timedelta(days=3)},
    })
    if old_new:
        insights.append(f"🆕 {old_new} νέα περιστατικά περιμένουν αξιολόγηση πάνω από 3 μέρες.")

    # PRESCRIBED not fulfilled in 5+ days
    old_presc = await db.cases.count_documents({
        "org_id": org_id, "status": "PRESCRIBED",
    })
    if old_presc:
        count = 0
        async for c in db.cases.find({"org_id": org_id, "status": "PRESCRIBED"}):
            for entry in c.get("status_history", []):
                if entry.get("to") == "PRESCRIBED":
                    entered = _ensure_utc(entry.get("at"))
                    if entered and (now - entered).days >= 5:
                        count += 1
                    break
        if count:
            insights.append(f"📝 {count} γνωματεύσεις ανεκτέλεστες πάνω από 5 μέρες.")

    if not insights:
        insights.append("✅ Όλα υπό έλεγχο! Δεν υπάρχουν εκκρεμότητες που χρειάζονται προσοχή.")

    return {"insights": insights, "total": len(insights), "generated_at": now.isoformat()}


# ═══════════════════════════════════════════════════════════════════════════════
#  NEW: Doctor Strategy — AI reads profile + notes → personalized talking points
# ═══════════════════════════════════════════════════════════════════════════════

async def _get_doctor_strategy(args, db, org_id):
    """Read doctor profile + notes + appointment history + product catalog → personalized strategy."""
    doctor_name = args.get("doctor_name", "").strip()
    if not doctor_name:
        return {"error": "Δώσε όνομα γιατρού"}

    # ── 1ο βήμα: deterministic case/accent-insensitive match ──
    doctor = None
    names = []
    async for d in db.doctors.find({"org_id": org_id}, {"name": 1}).limit(300):
        names.append(d.get("name", ""))
    hits = _deterministic_name_match(doctor_name, names)
    if hits:
        doctor = await db.doctors.find_one({"org_id": org_id, "name": names[hits[0][0]]})

    # ── 2ο βήμα: regex fallback (πια διορθωμένο — κανονικοποιεί τόνους) ──
    if doctor is None:
        escaped = _accent_regex(doctor_name)
        doctor = await db.doctors.find_one({"org_id": org_id, "name": {"$regex": escaped, "$options": "i"}})
    if not doctor:
        return {"error": f"Δεν βρέθηκε γιατρός με όνομα '{doctor_name}'"}

    specialty = doctor.get("specialty", "OTHER")
    notes = doctor.get("notes", "")
    area = doctor.get("area", "")
    workplace = doctor.get("workplace_type", "")

    # ── Appointment history with chronological notes ──────────────────
    doc_id = str(doctor["_id"])
    history_text = []
    async for appt in db.appointments.find(
        {"org_id": org_id, "doctor_id": doc_id}
    ).sort("at", 1).limit(20):
        note = (appt.get("notes") or "").strip()
        if note:
            history_text.append(f"- {str(appt.get('at',''))[:10]}: {note[:200]}")
        # Επεισοδιακές σημειώσεις ανά ραντεβού (timestamped) — πλήρες χρονολόγιο
        for nh in (appt.get("note_history") or []):
            nh_note = (nh.get("note") or "").strip()
            if nh_note and nh_note != note:
                d = (nh.get("note_date") or nh.get("at") or "")[:10]
                history_text.append(f"- {d}: {nh_note[:200]}")
    history_joined = "\n".join(history_text) if history_text else "(καμία σημείωση ραντεβού)"

    # Episodic memory for this doctor
    episode_text = ""
    try:
        from backend.llm.memory import get_recent_episodes
        episodes = await get_recent_episodes(db, org_id, days=180, entity_name=doctor.get("name", ""), limit=8)
        if episodes:
            ep_lines = []
            for ep in episodes:
                ep_lines.append(f"- {str(ep.get('at',''))[:10]} [{ep.get('event_type','')}]: {ep.get('summary','')[:150]}")
            episode_text = "\n".join(ep_lines)
    except Exception:
        pass

    # Προϊόντα που ταιριάζουν στην ειδικότητα (ελληνικές + αγγλικές κατηγορίες)
    products = await _products_for_specialty(db, org_id, specialty, limit=12)

    # Build strategy analysis
    strategy_parts = []

    # Analyze notes for clues
    notes_lower = notes.lower() if notes else ""
    interests = []
    if any(w in notes_lower for w in ["μελέτη", "κλινικ", "study", "evidence", "τεκμηρίωση", "έρευνα", "paper"]):
        interests.append("Ενδιαφέρεται για κλινικές μελέτες και τεκμηρίωση")
    if any(w in notes_lower for w in ["ποιότητα", "quality", "αξιοπιστία", "γερμανικό", "ελβετικό"]):
        interests.append("Δίνει βάρος στην ποιότητα και την προέλευση")
    if any(w in notes_lower for w in ["τιμή", "οικονομ", "κόστος", "price", "φθηνό", "ακριβό"]):
        interests.append("Ευαισθησία στην τιμή και το κόστος")
    if any(w in notes_lower for w in ["καινοτομ", "νέο", "τεχνολογί", "innovation", "modern"]):
        interests.append("Τον ελκύει η καινοτομία")
    if any(w in notes_lower for w in ["γρήγορα", "αποτελέσμα", "εύκολο", "απλό"]):
        interests.append("Εκτιμά την ευκολία και τα γρήγορα αποτελέσματα")
    if any(w in notes_lower for w in ["άνεση", "comfort", "ασφαλή", "απαλό"]):
        interests.append("Νοιάζεται για την άνεση του ασθενή")
    if any(w in notes_lower for w in ["απαιτητικό", "δύσκολο", "σκληρό"]):
        interests.append("Απαιτητικός — χρειάζεται προετοιμασία")
    if any(w in notes_lower for w in ["φιλικό", "εύκολο", "χαλαρό", "προσιτό"]):
        interests.append("Προσιτός και εύκολος στην επικοινωνία")

    # Build response
    strategy_text = f"**Προφίλ γιατρού:** {doctor.get('name')} — {specialty}"
    if workplace and workplace != "OTHER":
        strategy_text += f" ({workplace})"
    if area:
        strategy_text += f", {area}"

    strategy_text += "\n\n**Τι τον ενδιαφέρει:**\n"
    if interests:
        strategy_text += "\n".join(f"• {i}" for i in interests) + "\n"
    else:
        strategy_text += "• Δεν έχουν καταγραφεί συγκεκριμένα ενδιαφέροντα. Πρόσθεσε σημειώσεις στο προφίλ του για εξατομικευμένη στρατηγική.\n"

    if notes:
        strategy_text += f"\n**Σημειώσεις προφίλ:** {notes[:300]}"

    # ── Chronological history (the KEY addition) ──
    strategy_text += f"\n\n**Ιστορικό σημειώσεων ραντεβού (χρονοσειρά):**\n{history_joined}"
    if episode_text:
        strategy_text += f"\n\n**Πρόσφατα γεγονότα:**\n{episode_text}"

    strategy_text += f"\n\n**Προτεινόμενα προϊόντα για συζήτηση:**"
    for p in products[:6]:
        strategy_text += f"\n• {p['name']} ({p['category']}, {p['price']}€)"

    strategy_text += "\n\n**Προσέγγιση:** "
    if "κλινικ" in notes_lower or "μελέτη" in notes_lower:
        strategy_text += "Τόνισε τα δεδομένα κλινικών μελετών. Ανέφερε συγκεκριμένα papers ή evidence. "
    if "ποιότητα" in notes_lower:
        strategy_text += "Εστίασε στην ποιότητα υλικών και την προέλευση. "
    if "τιμή" in notes_lower:
        strategy_text += "Μίλησε για cost-effectiveness και αποζημίωση ΕΟΠΥΥ. "
    if not interests:
        strategy_text += "Ρώτησέ τον τι τον ενδιαφέρει περισσότερο όταν επιλέγει υλικά — ποιότητα, τιμή, ή καινοτομία;"

    return {
        "doctor_name": doctor.get("name"),
        "specialty": specialty,
        "strategy": strategy_text,
        "interests": interests,
        "recommended_products": products[:6],
        "notes": notes[:300],
        "appointment_history": history_joined,
        "episodes": episode_text,
    }


# ── Helpers ──


# RAG Knowledge Search
async def _search_knowledge(args, db, org_id):
    """Search the knowledge base (RAG) for guidelines, papers, protocols."""
    query = args.get("query", "").strip()
    if not query:
        return {"items": [], "total": 0}
    import re
    # Split into keywords and search with accent/case-insensitive regex on chunk_text
    keywords = [w for w in re.split(r'\s+|[,.;:!?]+', query) if len(w) >= 2]
    if not keywords:
        keywords = [query]
    or_clauses = []
    for kw in keywords[:6]:
        rx = _accent_regex(kw)
        or_clauses.append({"chunk_text": {"$regex": rx, "$options": "i"}})
        or_clauses.append({"tags": {"$regex": rx, "$options": "i"}})
    items = []
    seen = set()
    async for k in db.knowledge_chunks.find({
        "$and": [
            {"$or": [{"is_shared": True}, {"org_id": org_id}]},
            {"$or": or_clauses},
        ]}).limit(10):
        txt = (k.get("chunk_text", "") or "")[:500]
        if txt and txt not in seen:
            seen.add(txt)
            items.append({"content": txt, "tab_name": k.get("tab_name", ""), "tags": k.get("tags", [])})
    return {"items": items, "total": len(items)}


async def _find_person(args, db, org_id):
    """Search ONE name across ALL catalogs using LLM matching (no regex)."""
    name = args.get("name", "").strip()
    if not name:
        return {"error": "name required"}

    # Load ALL entities with their names
    all_entities = []

    async for d in db.doctors.find({"org_id": org_id}, {"name": 1, "specialty": 1, "area": 1}).limit(200):
        all_entities.append({"type": "doctor", "name": d.get("name", ""), "detail": d.get("specialty", "")})

    async for s in db.sources.find({"org_id": org_id}, {"name": 1, "type": 1}).limit(100):
        all_entities.append({"type": "source", "name": s.get("name", ""), "detail": s.get("type", "")})

    async for st in db.structures.find({"org_id": org_id}, {"name": 1, "city": 1}).limit(100):
        all_entities.append({"type": "structure", "name": st.get("name", ""), "detail": st.get("city", "")})

    if not all_entities:
        return {"found": False, "message": "Το σύστημα είναι άδειο — δεν υπάρχουν εγγραφές.", "results": {"doctors": [], "sources": [], "structures": []}}

    # ── 1ο βήμα: DETERMINISTIC case/accent-insensitive match (χωρίς LLM) ──
    # Λύνει το «πατρικαλακης» vs «ΠΑΤΡΙΚΑΛΑΚΗΣ» — το μοντέλο δεν ξέρει πεζά/κεφαλαία.
    matched_ids = [i for i, _s in _deterministic_name_match(name, [e["name"] for e in all_entities])]

    # ── 2ο βήμα: αν δεν βρέθηκε τίποτα, ρώτα το LLM (συνώνυμα, σειρά λέξεων) ──
    if not matched_ids:
        from backend.llm.client import simple_call
        entity_list = "\n".join(f"[{i}] {e['type']}: {e['name']} ({e['detail']})" for i, e in enumerate(all_entities))
        prompt = (
            f"Ο χρήστης ψάχνει άτομο/εγγραφή με το όνομα: «{name}»\n\n"
            f"Αυτές είναι ΟΛΕΣ οι εγγραφές του συστήματος:\n{entity_list}\n\n"
            f"Βρες ΠΟΙΕΣ ταιριάζουν (μπορεί να είναι γραμμένο με τόνους ή χωρίς, σε διαφορετική σειρά λέξεων, ή μόνο το επίθετο). "
            f"Απάντησε ΜΟΝΟ με τους αριθμούς [i] των ταιριασμάτων χωρισμένους με κόμμα. Αν δεν ταιριάζει κανένα, απάντησε ΜΟΝΟ 'ΚΑΝΕΝΑ'."
        )
        try:
            answer = (await simple_call(prompt, temperature=0, max_tokens=100, model=settings.deepseek_model_fast)).strip()
        except:
            answer = "ΚΑΝΕΝΑ"
        for token in answer.replace("[", " ").replace("]", " ").split():
            if token.isdigit():
                idx = int(token)
                if 0 <= idx < len(all_entities):
                    matched_ids.append(idx)
        # dedupe διατηρώντας σειρά
        seen_ids = set()
        matched_ids = [i for i in matched_ids if not (i in seen_ids or seen_ids.add(i))]

    results = {"doctors": [], "sources": [], "structures": []}
    for idx in matched_ids:
        e = all_entities[idx]
        t = e["type"]
        if t == "doctor":
            d = await db.doctors.find_one({"org_id": org_id, "name": e["name"]})
            if d:
                results["doctors"].append({
                    "name": d.get("name"), "specialty": d.get("specialty"),
                    "phone": d.get("phone"), "email": d.get("email"),
                    "area": d.get("area"), "workplace_type": d.get("workplace_type"),
                    "address": d.get("address"),
                    "notes": (d.get("notes") or "")[:400],
                    "structure_ids": d.get("structure_ids", []),
                })
        elif t == "source":
            s = await db.sources.find_one({"org_id": org_id, "name": e["name"]})
            if s:
                results["sources"].append({
                    "name": s.get("name"), "type": s.get("type"),
                    "phone": s.get("phone"), "notes": (s.get("notes") or "")[:400],
                })
        elif t == "structure":
            st = await db.structures.find_one({"org_id": org_id, "name": e["name"]})
            if st:
                results["structures"].append({
                    "name": st.get("name"), "type": st.get("type"),
                    "city": st.get("city"), "prefecture": st.get("prefecture"),
                    "address": st.get("address"), "phone": st.get("phone"),
                    "notes": (st.get("notes") or "")[:400],
                })

    total = len(results["doctors"]) + len(results["sources"]) + len(results["structures"])
    if total == 0:
        return {"found": False, "message": f"Δεν βρέθηκε κανένα άτομο/εγγραφή με το όνομα '{name}' σε γιατρούς, πηγές ή δομές.", "results": results}
    return {"found": True, "total": total, "results": results}




# Update/Delete Functions
async def _update_doctor(args, db, org_id):
    doctor_id = args.get("doctor_id")
    if not doctor_id: return {"error": "doctor_id required"}
    updates = {}
    for f in ["name","specialty","phone","email","area","notes","workplace_type","address"]:
        if f in args: updates[f] = args[f]
    updates["updated_at"] = datetime.now(timezone.utc)
    await db.doctors.update_one({"_id": ObjectId(doctor_id), "org_id": org_id}, {"$set": updates})
    return {"success": True, "name": args.get("name",""), "message": "O giatros enhmerothike."}

async def _delete_appointment(args, db, org_id):
    appt_id = args.get("appointment_id")
    if not appt_id: return {"error": "appointment_id required"}
    await db.appointments.delete_one({"_id": ObjectId(appt_id), "org_id": org_id})
    return {"success": True, "message": "To ranteboy diagrafthike."}

async def _update_appointment(args, db, org_id):
    appt_id = args.get("appointment_id")
    if not appt_id: return {"error": "appointment_id required"}
    updates = {}
    for f in ["title","notes","kind","doctor_id","location"]:
        if f in args: updates[f] = args[f]
    if "at" in args:
        try: updates["at"] = datetime.fromisoformat(args["at"])
        except: pass
    updates["updated_at"] = datetime.now(timezone.utc)
    await db.appointments.update_one({"_id": ObjectId(appt_id), "org_id": org_id}, {"$set": updates})
    return {"success": True, "message": "To ranteboy enhmerothike."}

def _case_summary(case: dict, kind: str) -> dict:
    return {
        "_id": str(case["_id"]),
        "alias": case.get("alias", ""),
        "area": case.get("area", ""),
        "kind": kind,
        "case_id": str(case["_id"]),
        "status": case.get("status", ""),
        "need_categories": case.get("need_categories", []),
        "est_monthly_value": case.get("est_monthly_value"),
    }
