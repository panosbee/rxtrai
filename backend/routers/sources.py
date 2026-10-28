"""
RxTrail — Sources router (auto-generated CRUD).
"""
from backend.models.source import SourceCreate, SourceUpdate
from backend.routers.generic_router import make_crud_router

router = make_crud_router(
    prefix="/sources",
    tag="sources",
    collection_name="sources",
    create_model=SourceCreate,
    update_model=SourceUpdate,
    list_filter_fields=["type"],
)
