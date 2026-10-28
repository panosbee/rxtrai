"""
RxTrail — Structures router (auto-generated CRUD).
"""
from backend.models.structure import StructureCreate, StructureUpdate
from backend.routers.generic_router import make_crud_router

router = make_crud_router(
    prefix="/structures",
    tag="structures",
    collection_name="structures",
    create_model=StructureCreate,
    update_model=StructureUpdate,
    list_filter_fields=["prefecture", "type"],
)
