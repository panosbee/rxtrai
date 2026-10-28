"""
RxTrail — Contacts router (auto-generated CRUD).
"""
from backend.models.contact import ContactCreate, ContactUpdate
from backend.routers.generic_router import make_crud_router

router = make_crud_router(
    prefix="/contacts",
    tag="contacts",
    collection_name="contacts",
    create_model=ContactCreate,
    update_model=ContactUpdate,
    list_filter_fields=["structure_id", "role_title"],
)
