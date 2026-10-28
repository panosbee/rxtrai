"""
RxTrail — Onboarding router: first-run status check + guided setup.
"""
from fastapi import APIRouter, Depends, Request
from backend.auth.deps import get_current_user, get_org
from backend.db import get_db

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("/status")
async def onboarding_status(
    request: Request = ...,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Return what the org has / hasn't set up yet for guided onboarding."""
    db = await get_db()

    # Count entities scoped to org
    doctors_count = await db.doctors.count_documents({"org_id": org_id})
    sources_count = await db.sources.count_documents({"org_id": org_id})
    cases_count = await db.cases.count_documents({"org_id": org_id})
    skus_count = await db.skus.count_documents({"org_id": org_id})
    structures_count = await db.structures.count_documents({"org_id": org_id})

    # Check if org has a name set
    org = await db.organizations.find_one({"_id": org_id})
    has_name = bool(org and org.get("name"))

    steps = [
        {
            "id": "profile",
            "label": "Προφίλ Οργανισμού",
            "icon": "🏢",
            "done": has_name,
            "description": "Συμπλήρωσε το όνομα του οργανισμού σου",
        },
        {
            "id": "sources",
            "label": "Πηγές Παραπομπών",
            "icon": "🔗",
            "done": sources_count > 0,
            "description": "Πρόσθεσε τις πηγές που σου στέλνουν περιστατικά",
        },
        {
            "id": "doctors",
            "label": "Γιατροί",
            "icon": "👨‍⚕️",
            "done": doctors_count > 0,
            "description": "Καταχώρησε τους γιατρούς που συνεργάζεσαι",
        },
        {
            "id": "structures",
            "label": "Δομές & Νοσοκομεία",
            "icon": "🏥",
            "done": structures_count > 0,
            "description": "Πρόσθεσε δομές υγείας της περιοχής σου",
        },
        {
            "id": "skus",
            "label": "Κατάλογος SKU",
            "icon": "📦",
            "done": skus_count > 0,
            "description": "Εισήγαγε κωδικούς προϊόντων ΕΟΠΥΥ",
        },
        {
            "id": "first_case",
            "label": "Πρώτο Περιστατικό",
            "icon": "📋",
            "done": cases_count > 0,
            "description": "Δημιούργησε το πρώτο σου περιστατικό",
        },
    ]

    completed = sum(1 for s in steps if s["done"])
    total = len(steps)
    all_done = completed == total

    return {
        "steps": steps,
        "completed": completed,
        "total": total,
        "all_done": all_done,
        "counts": {
            "doctors": doctors_count,
            "sources": sources_count,
            "cases": cases_count,
            "skus": skus_count,
            "structures": structures_count,
        },
    }
