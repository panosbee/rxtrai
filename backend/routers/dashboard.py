"""
RxTrail — Dashboard router: monthly stats + weekly truth + xlsx export.
M6 — final polish.
"""
from datetime import datetime, timezone

import io
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from backend.auth.deps import get_current_user, get_org
from backend.db import get_db

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/month")
async def dashboard_month(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Monthly dashboard summary."""
    db = await get_db()
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Aggregate stats
    active_cases = await db.cases.count_documents({"org_id": org_id, "status": "ACTIVE"})
    at_risk = await db.cases.count_documents({"org_id": org_id, "status": "AT_RISK"})
    new_this_month = await db.cases.count_documents({"org_id": org_id, "created_at": {"$gte": month_start}})
    lost_this_month = await db.cases.count_documents({"org_id": org_id, "status": "LOST", "updated_at": {"$gte": month_start}})

    # Revenue this month (from fulfilments)
    pipeline = [
        {"$match": {"org_id": org_id, "fulfilled_at": {"$gte": month_start}}},
        {"$group": {"_id": None, "total": {"$sum": "$total_revenue"}}},
    ]
    rev_result = None
    async for r in db.fulfilments.aggregate(pipeline):
        rev_result = r
    monthly_revenue = rev_result["total"] if rev_result else 0

    # Prescriptions this month
    rx_count = await db.prescriptions.count_documents({"org_id": org_id, "created_at": {"$gte": month_start}})
    visits_count = await db.visits.count_documents({"org_id": org_id, "at": {"$gte": month_start}})

    return {
        "active_cases": active_cases,
        "at_risk": at_risk,
        "new_this_month": new_this_month,
        "lost_this_month": lost_this_month,
        "monthly_revenue": round(monthly_revenue, 2),
        "prescriptions_this_month": rx_count,
        "visits_this_month": visits_count,
    }


@router.get("/roi")
async def dashboard_roi(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
    months: int = Query(3, ge=1, le=12),
):
    """ROI analytics: revenue by doctor, structure, source, area for the last N months."""
    db = await get_db()
    now = datetime.now(timezone.utc)
    from datetime import timedelta
    since = now - timedelta(days=30 * months)

    # Load all fulfilments in period
    fulfs = []
    async for f in db.fulfilments.find({
        "org_id": org_id,
        "fulfilled_at": {"$gte": since},
    }, {"case_id": 1, "total_revenue": 1, "fulfilled_at": 1}):
        fulfs.append(f)

    # Build case lookup (case_id → case)
    case_ids = [f.get("case_id") for f in fulfs if f.get("case_id")]
    case_map = {}
    from bson import ObjectId
    for cid in set(case_ids):
        try:
            case = await db.cases.find_one({"_id": ObjectId(cid)}, {
                "doctor_id": 1, "source_id": 1, "area": 1, "alias": 1, "est_monthly_value": 1,
            })
            if case:
                case_map[str(cid)] = case
        except Exception:
            pass

    # Load name maps
    doctor_names, source_names, structure_names = {}, {}, {}
    async for d in db.doctors.find({"org_id": org_id}, {"name": 1}):
        doctor_names[str(d["_id"])] = d.get("name", "?")
    async for s in db.sources.find({"org_id": org_id}, {"name": 1}):
        source_names[str(s["_id"])] = s.get("name", "?")
    async for st in db.structures.find({"org_id": org_id}, {"name": 1}):
        structure_names[str(st["_id"])] = st.get("name", "?")

    # Aggregate
    by_doctor, by_source, by_area = {}, {}, {}
    total_revenue = 0
    for f in fulfs:
        rev = f.get("total_revenue") or 0
        total_revenue += rev
        case = case_map.get(str(f.get("case_id"))) if f.get("case_id") else None
        if case:
            doc_id = str(case.get("doctor_id")) if case.get("doctor_id") else None
            src_id = str(case.get("source_id")) if case.get("source_id") else None
            area = (case.get("area") or "Χωρίς περιοχή").strip()
            if doc_id:
                by_doctor[doc_id] = by_doctor.get(doc_id, 0) + rev
            if src_id:
                by_source[src_id] = by_source.get(src_id, 0) + rev
            by_area[area] = by_area.get(area, 0) + rev

    def top_n(d: dict, names: dict, n: int = 8) -> list:
        items = [{"name": names.get(k, "?"), "revenue": round(v, 2)} for k, v in d.items()]
        items.sort(key=lambda x: -x["revenue"])
        return items[:n]

    return {
        "months": months,
        "total_revenue": round(total_revenue, 2),
        "by_doctor": top_n(by_doctor, doctor_names),
        "by_source": top_n(by_source, source_names),
        "by_area": top_n(by_area, {}),
        "generated_at": now.isoformat(),
    }


@router.get("/export/xlsx")
async def export_xlsx(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Export cases to Excel (openpyxl)."""
    try:
        from openpyxl import Workbook
    except ImportError:
        from fastapi import HTTPException
        raise HTTPException(500, "openpyxl not installed")

    db = await get_db()
    wb = Workbook()
    ws = wb.active
    ws.title = "Περιστατικά"

    # Headers
    headers = ["Ψευδώνυμο", "Κατάσταση", "Περιοχή", "Κατηγορίες", "Εκτίμηση €",
               "Πηγή", "Γιατρός", "Ημ/νία Δημιουργίας"]
    ws.append(headers)

    # Data
    async for case in db.cases.find({"org_id": org_id}).sort("created_at", -1).limit(1000):
        source = None
        if case.get("source_id"):
            source = await db.sources.find_one({"_id": case["source_id"]}, {"name": 1})
        doctor = None
        if case.get("doctor_id"):
            doctor = await db.doctors.find_one({"_id": case["doctor_id"]}, {"name": 1})

        ws.append([
            case.get("alias", ""),
            case.get("status", ""),
            case.get("area", ""),
            ", ".join(case.get("need_categories", [])),
            case.get("est_monthly_value", ""),
            source["name"] if source else "",
            doctor["name"] if doctor else "",
            str(case.get("created_at", ""))[:10],
        ])

    # Save to buffer
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=rxtrail_cases.xlsx"},
    )
