"""
RxTrail — LLM router: API endpoints for all 6 articulations.

Articulations:
  A. POST /llm/intake       — voice/text → structured case card
  B. POST /llm/assess       — needs + SKU catalog → proposed SKUs
  C. POST /llm/prescription — OCR text → structured prescription (M5)
  D. GET  /llm/brief/{type}/{id} — context before contact
  E. GET  /llm/weekly-truth — weekly insight (aggregates only)
  F. POST /llm/mapping      — Excel headers → field mapping
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.auth.deps import get_current_user, get_org
from backend.config import settings
from backend.db import get_db
from backend.llm.client import structured_call, simple_call, LLMError
from backend.llm.embeddings import search_similar_skus, search_similar_sources
from backend.llm.prompts import (
    INTAKE_V1, ASSESS_V1, BRIEF_V1, WEEKLY_TRUTH_V1, MAPPING_V1,
)
from backend.llm.schemas import (
    IntakeResult, AssessResult, BriefResult, WeeklyTruthResult, MappingResult,
)
from backend.llm.scrubber import scrub_text, scrub_text_sync

router = APIRouter(prefix="/llm", tags=["llm"])


def _json_safe(obj):
    """Μετατρέπει ObjectId → str παντού (το FastAPI δεν τα σειριοποιεί)."""
    from bson import ObjectId as _OID
    if isinstance(obj, _OID):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


# ── A: Intake ────────────────────────────────────────────────────────────────

@router.post("/intake")
async def llm_intake(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Process voice/text intake → structured case card."""
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(400, "Missing 'text' field")

    db = await get_db()

    # ── Scrub PII (light: only ΑΜΚΑ/phones/dates — intake names are public) ─
    scrubbed = scrub_text_sync(text)

    # ── LLM call ───────────────────────────────────────────────────────
    prompt = INTAKE_V1.format(text=scrubbed)

    try:
        result = await structured_call(
            prompt=prompt,
            schema=IntakeResult,
            db=db,
            org_id=org_id,
            user_id=str(current_user["_id"]),
            temperature=0.1,
        )
    except LLMError as e:
        raise HTTPException(500, f"LLM call failed: {e}")

    # ── Fuzzy source matching ──────────────────────────────────────────
    source_name = result.get("source_name_raw", "")
    sources = []
    if source_name:
        all_sources = await db.sources.find(
            {"org_id": org_id}, {"name": 1, "type": 1}
        ).to_list(length=200)
        sources = await search_similar_sources(source_name, all_sources, top_k=3)
        # BUG FIX: το ObjectId δεν είναι JSON-serializable → 500 στο response.
        for s in sources:
            if "_id" in s:
                s["_id"] = str(s["_id"])

    result["source_suggestions"] = sources
    result["_scrubbed_text"] = scrubbed[:200]  # For debugging

    return _json_safe(result)


# ── B: Assess ────────────────────────────────────────────────────────────────

@router.post("/assess")
async def llm_assess(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Assess case needs → propose SKUs from catalog."""
    body = await request.json()
    needs_description = body.get("needs_description", "").strip()
    if not needs_description:
        raise HTTPException(400, "Missing 'needs_description'")

    db = await get_db()

    # ── Scrub ──────────────────────────────────────────────────────────
    scrubbed = scrub_text_sync(needs_description)

    # ── Vector search over SKU catalog ──────────────────────────────────
    all_skus = await db.skus.find(
        {"org_id": org_id, "active": True},
        {"eopyy_code": 1, "commercial_name": 1, "category": 1, "reimb_price": 1,
         "specialties_allowed": 1, "duration_days_default": 1, "name_embedding": 1},
    ).to_list(length=500)

    # Get top-K similar SKUs
    similar = await search_similar_skus(scrubbed, all_skus, top_k=15)

    # Build catalog string for prompt
    catalog_lines = []
    for sku in similar:
        catalog_lines.append(
            f"  - {sku['eopyy_code']}: {sku.get('commercial_name','')} "
            f"(κατ: {sku.get('category','')}, τιμή: {sku.get('reimb_price',0)}€, "
            f"διάρκεια: {sku.get('duration_days_default',30)}ημ, "
            f"ειδικότητες: {', '.join(sku.get('specialties_allowed', []))})"
        )
    sku_catalog = "\n".join(catalog_lines) if catalog_lines else "(κενός κατάλογος)"

    # ── LLM call ───────────────────────────────────────────────────────
    prompt = ASSESS_V1.format(
        sku_catalog=sku_catalog,
        needs_description=scrubbed,
    )

    try:
        result = await structured_call(
            prompt=prompt,
            schema=AssessResult,
            db=db,
            org_id=org_id,
            user_id=str(current_user["_id"]),
            temperature=0.05,
        )
    except LLMError as e:
        raise HTTPException(500, f"LLM call failed: {e}")

    # Embed full SKU details for the proposed items
    proposed_with_details = []
    for item in result.get("proposed", []):
        match = await db.skus.find_one(
            {"org_id": org_id, "eopyy_code": item["eopyy_code"]},
            {"commercial_name": 1, "reimb_price": 1, "category": 1},
        )
        proposed_with_details.append({
            **item,
            "commercial_name": match.get("commercial_name", "") if match else "",
            "reimb_price": match.get("reimb_price", 0) if match else 0,
            "category": match.get("category", "") if match else "",
        })

    result["proposed"] = proposed_with_details
    result["similar_sku_count"] = len(similar)

    return result


# ── D: Brief ─────────────────────────────────────────────────────────────────

@router.get("/brief/{target_type}/{target_id}")
async def llm_brief(
    target_type: str,
    target_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Generate a 1-2 sentence brief before contacting a doctor/contact."""
    db = await get_db()

    # Collect recent notes
    notes = []
    if target_type == "DOCTOR":
        async for visit in db.visits.find(
            {"org_id": org_id, "target_type": "DOCTOR", "target_id": target_id}
        ).sort("at", -1).limit(5):
            if visit.get("voice_note_text"):
                notes.append(visit["voice_note_text"])
        async for case in db.cases.find(
            {"org_id": org_id, "doctor_id": target_id}
        ).sort("updated_at", -1).limit(3):
            notes.append(f"Περιστατικό: {case.get('alias','')} — {case.get('needs_freehand','')}")

    elif target_type == "CONTACT":
        async for visit in db.visits.find(
            {"org_id": org_id, "target_type": "CONTACT", "target_id": target_id}
        ).sort("at", -1).limit(5):
            if visit.get("voice_note_text"):
                notes.append(visit["voice_note_text"])

    if not notes:
        return {"brief": "Δεν υπάρχουν πρόσφατες σημειώσεις για αυτή την επαφή.", "fallback": True}

    notes_text = "\n".join(f"- {n}" for n in notes)
    prompt = BRIEF_V1.format(notes=notes_text)

    try:
        brief_text = await simple_call(prompt, temperature=0.3, max_tokens=300)
        return {"brief": brief_text.strip()}
    except Exception:
        # Graceful fallback: return the most recent note
        return {"brief": notes[0][:200] if notes else "Δεν υπάρχουν σημειώσεις.", "fallback": True}


# ── E: Weekly Truth ─────────────────────────────────────────────────────────

@router.get("/weekly-truth")
async def llm_weekly_truth(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Generate weekly insight from AGGREGATE data only."""
    db = await get_db()
    now = datetime.now(timezone.utc)
    week_ago = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Aggregate stats (NO raw patient data)
    visit_count = await db.visits.count_documents({
        "org_id": org_id,
        "at": {"$gte": week_ago},
    })
    rx_count = await db.prescriptions.count_documents({
        "org_id": org_id,
        "created_at": {"$gte": week_ago},
    })
    fulfil_count = await db.fulfilments.count_documents({
        "org_id": org_id,
        "created_at": {"$gte": week_ago},
    })
    new_cases = await db.cases.count_documents({
        "org_id": org_id,
        "created_at": {"$gte": week_ago},
    })
    lost_cases = await db.cases.count_documents({
        "org_id": org_id,
        "status": "LOST",
        "updated_at": {"$gte": week_ago},
    })

    weekly_data = (
        f"Επισκέψεις: {visit_count}\n"
        f"Γνωματεύσεις: {rx_count}\n"
        f"Εκτελέσεις: {fulfil_count}\n"
        f"Νέα περιστατικά: {new_cases}\n"
        f"Χαμένα περιστατικά: {lost_cases}\n"
    )

    prompt = WEEKLY_TRUTH_V1.format(weekly_data=weekly_data)

    try:
        result = await structured_call(
            prompt=prompt,
            schema=WeeklyTruthResult,
            db=db,
            org_id=org_id,
            user_id=str(current_user["_id"]),
            temperature=0.5,
            model=settings.deepseek_model_deep,
        )
        return {**result, "stats": {
            "visits": visit_count, "prescriptions": rx_count,
            "fulfilments": fulfil_count, "new_cases": new_cases, "lost_cases": lost_cases,
        }}
    except Exception:
        return {
            "headline": f"{visit_count} επισκέψεις, {rx_count} γνωματεύσεις αυτή την εβδομάδα",
            "insight": f"{new_cases} νέα περιστατικά, {lost_cases} χάθηκαν.",
            "fallback": True,
            "stats": {
                "visits": visit_count, "prescriptions": rx_count,
                "fulfilments": fulfil_count, "new_cases": new_cases, "lost_cases": lost_cases,
            },
        }


# ── F: Mapping ──────────────────────────────────────────────────────────────

@router.post("/mapping")
async def llm_mapping(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """LLM-assisted Excel column mapping for imports."""
    body = await request.json()
    headers = body.get("headers", [])
    kind = body.get("kind", "SKU")
    sample_rows = body.get("sample_rows", [])

    if not headers:
        raise HTTPException(400, "Missing 'headers'")

    db = await get_db()

    available_fields = _available_fields_for(kind)
    sample_text = "\n".join(
        ", ".join(str(c) for c in row) for row in sample_rows[:3]
    ) if sample_rows else "(δεν υπάρχει δείγμα)"

    prompt = MAPPING_V1.format(
        kind=kind,
        available_fields="\n".join(f"  - {f}" for f in available_fields),
        headers="\n".join(f"  - {h}" for h in headers),
        sample_rows=sample_text,
    )

    try:
        result = await structured_call(
            prompt=prompt,
            schema=MappingResult,
            db=db,
            org_id=org_id,
            user_id=str(current_user["_id"]),
            temperature=0.0,
        )
        return result
    except Exception:
        # Fallback: return empty mappings
        return {"mappings": [], "fallback": True}


def _available_fields_for(kind: str) -> list[str]:
    """Return available field names for a given import kind."""
    if kind == "SKU":
        return ["eopyy_code", "commercial_name", "description", "category",
                "reimb_price", "patient_copay_pct", "max_qty_month",
                "duration_days_default"]
    elif kind == "STRUCTURES":
        return ["name", "type", "city", "prefecture", "address", "phone"]
    elif kind == "DOCTORS":
        return ["name", "specialty", "phone", "notes"]
    elif kind == "CONTACTS":
        return ["name", "role_title", "phone", "notes", "structure_id"]
    return []


# ── STT: Speech-to-Text (Whisper, server-side) ───────────────────────────────

@router.post("/stt")
async def llm_stt(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Server-side speech-to-text using faster-whisper. More reliable than Web Speech API."""
    import tempfile, os
    from fastapi import UploadFile, File, Form
    from fastapi.responses import JSONResponse

    # Read raw bytes from request body
    audio_bytes = await request.body()
    if not audio_bytes:
        return JSONResponse({"error": "No audio data"}, status_code=400)

    suffix = ".webm"
    content_type = request.headers.get("content-type", "")
    if "wav" in content_type: suffix = ".wav"
    elif "ogg" in content_type: suffix = ".ogg"
    elif "mp4" in content_type or "m4a" in content_type: suffix = ".m4a"

    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(audio_bytes)
        tmp.close()

        from faster_whisper import WhisperModel
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, info = model.transcribe(tmp.name, language="el", beam_size=5)
        text = " ".join(s.text for s in segments).strip()

        return {"text": text, "language": info.language, "model": "whisper-tiny"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        try: os.unlink(tmp.name)
        except: pass


# ── Agent Feedback ──────────────────────────────────────────────────────────

@router.post("/agent/feedback")
async def agent_feedback(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """User feedback on agent response (thumbs up/down)."""
    body = await request.json()
    feedback = body.get("feedback", "")
    message = body.get("message", "")[:500]

    db = await get_db()
    await db.agent_memory.insert_one({
        "org_id": org_id,
        "type": "feedback",
        "user_id": str(current_user["_id"]),
        "feedback": feedback,
        "message": message,
        "at": datetime.now(timezone.utc),
    })
    return {"status": "ok"}


# ── G: AI Agent ─────────────────────────────────────────────────────────────

@router.post("/agent")
async def llm_agent(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """AI Agent: Natural Language → System Actions.
    Ο χρήστης μιλάει φυσικά Ελληνικά, ο agent εκτελεί ενέργειες.
    """
    body = await request.json()
    message = body.get("message", "").strip()
    if not message:
        raise HTTPException(400, "Missing 'message' field")

    db = await get_db()

    from backend.llm.agent import run_agent
    result = await run_agent(
        user_message=message,
        db=db,
        org_id=org_id,
        user_id=str(current_user["_id"]),
    )

    return result


@router.post("/agent/reset")
async def reset_agent_memory(
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Καθαρισμός μνήμης AI — σβήνει μόνο το ιστορικό συνομιλιών.
    ΔΕΝ πειράζει: SKU, γιατρούς, δομές, επαφές, πηγές, γνώσεις (RAG).
    """
    db = await get_db()
    from backend.llm.memory import clear_working_memory
    await clear_working_memory(db, org_id)
    return {
        "status": "ok",
        "message": "Η μνήμη του Ασκληπιού καθαρίστηκε. Τα δεδομένα σου (SKU, γιατροί, δομές, επαφές, πηγές, γνώσεις) είναι ασφαλή.",
        "cleared": "working_memory",
        "untouched": ["skus", "doctors", "structures", "contacts", "sources", "knowledge_chunks", "knowledge_files", "cases", "appointments", "prescriptions", "fulfilments", "visits"],
    }


# ── Proactive alerts ─────────────────────────────────────────────────────────

@router.get("/agent/proactive")
async def get_proactive_alerts(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Λαμβάνει proactive alerts από τον AI. Χρησιμοποιεί cached insights που ανανεώνονται κάθε 4 ώρες."""
    db = await get_db()

    # Check if we have recent cached insights
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    cached = await db.agent_memory.find_one({
        "org_id": org_id, "type": "proactive",
        "at": {"$gte": now - timedelta(hours=4)},
    }, sort=[("at", -1)])

    if cached:
        return {
            "alerts": cached.get("alerts", []),
            "generated_at": cached.get("at").isoformat() if cached.get("at") else "",
            "cached": True,
        }

    # Generate fresh insights via the agent helper
    from backend.llm.agent import _get_proactive_insights
    result = await _get_proactive_insights(db, org_id)
    alerts = result.get("insights", [])

    # Cache
    await db.agent_memory.insert_one({
        "org_id": org_id,
        "type": "proactive",
        "alerts": alerts,
        "at": now,
    })

    return {
        "alerts": alerts,
        "generated_at": now.isoformat(),
        "cached": False,
    }
