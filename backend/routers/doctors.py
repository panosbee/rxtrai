"""
RxTrail — Doctors router (CRUD + Excel export).
Το export route μπαίνει ΠΡΙΝ τα generic CRUD routes γιατί το GET /{item_id}
θα έπιανε το "export.xlsx" ως ObjectId.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from backend.auth.deps import get_current_user, get_org
from backend.db import get_db
from backend.models.doctor import DoctorCreate, DoctorUpdate
from backend.routers.generic_router import make_crud_router

router = APIRouter(tags=["doctors"])


@router.get("/doctors/export.xlsx")
async def export_doctors_xlsx(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Εξαγωγή υγειονομικού προσωπικού σε Excel (δομημένα πεδία)."""
    import io
    from datetime import datetime

    db = await get_db()

    try:
        from openpyxl import Workbook
    except ImportError:
        from fastapi import HTTPException
        raise HTTPException(500, "openpyxl not installed")

    # Δομές: id → όνομα (για ευανάγνωστη στήλη)
    struct_names: dict[str, str] = {}
    async for s in db.structures.find({"org_id": org_id}, {"name": 1}):
        struct_names[str(s["_id"])] = s.get("name", "")

    wb = Workbook()
    ws = wb.active
    ws.title = "Υγειονομικό Προσωπικό"
    ws.append([
        "Όνομα", "Ειδικότητα", "Τύπος προσωπικού", "Χώρος εργασίας", "Ρόλος",
        "Περιοχή", "Διεύθυνση", "Τηλέφωνο", "Email", "Δομές", "Σημειώσεις",
        "Rx 90 ημ.", "Ανοιχτά περιστατικά", "Τελευταία επίσκεψη", "Δημιουργήθηκε",
    ])

    n = 0
    async for d in db.doctors.find({"org_id": org_id}).sort("name", 1):
        structs = ", ".join(
            struct_names.get(sid, "") for sid in (d.get("structure_ids") or [])
        )
        last_visit = d.get("last_visit_at")
        last_visit_s = last_visit.strftime("%Y-%m-%d") if hasattr(last_visit, "strftime") else str(last_visit or "")
        created = d.get("created_at")
        created_s = created.strftime("%Y-%m-%d") if hasattr(created, "strftime") else str(created or "")
        ws.append([
            d.get("name", ""),
            d.get("specialty", ""),
            d.get("personnel_type", ""),
            d.get("workplace_type", ""),
            d.get("role_title", ""),
            d.get("area", ""),
            d.get("address", ""),
            d.get("phone", ""),
            d.get("email", ""),
            structs,
            d.get("notes", ""),
            d.get("rx_count_90d", 0),
            d.get("open_cases_count", 0),
            last_visit_s,
            created_s,
        ])
        n += 1

    # Πλάτος στηλών για αναγνωσιμότητα
    for col in ("A", "J", "K"):
        ws.column_dimensions[col].width = 28
    for col in ("B", "C", "D"):
        ws.column_dimensions[col].width = 16
    for col in ("E", "F", "G", "H", "I"):
        ws.column_dimensions[col].width = 18

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=prosopiko_{stamp}.xlsx"},
    )


# Generic CRUD — ΜΕΤΑ το export route (σειρά δήλωσης = σειρά προτεραιότητας)
router.include_router(make_crud_router(
    prefix="/doctors",
    tag="doctors",
    collection_name="doctors",
    create_model=DoctorCreate,
    update_model=DoctorUpdate,
    list_filter_fields=["specialty", "area", "workplace_type"],
))
