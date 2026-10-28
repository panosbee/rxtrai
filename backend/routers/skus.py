"""
RxTrail — SKU router: CRUD + search.
"""
from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Request

from backend.auth.deps import get_current_user, get_org
from backend.db import get_db
from backend.models.sku import SKUCreate, SKUUpdate, SKUResponse
from backend.routers.crud_helpers import create_item, delete_item, get_item, list_items, update_item

router = APIRouter(prefix="/skus", tags=["skus"])


def _serialize(doc: dict) -> dict:
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


async def _coll(request: Request, org_id: str):
    db = await get_db()
    from backend.db import scoped
    return scoped(db.skus, org_id)


@router.get("")
async def list_skus(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    sort: str = "-created_at",
    category: str = Query(""),
    active_only: bool = Query(True),
    search: str = Query(""),
):
    coll = await _coll(request, org_id)
    extra: dict = {}
    if category:
        extra["category"] = category
    if active_only:
        extra["active"] = True
    if search:
        # Use regex for Greek-friendly partial matching across multiple fields
        escaped = search.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        extra["$or"] = [
            {"commercial_name": {"$regex": escaped, "$options": "i"}},
            {"product_id": {"$regex": escaped, "$options": "i"}},
            {"eopyy_code": {"$regex": escaped, "$options": "i"}},
            {"description": {"$regex": escaped, "$options": "i"}},
            {"category": {"$regex": escaped, "$options": "i"}},
        ]

    items, total = await list_items(coll, org_id, skip, limit, sort, extra)
    return {"items": [_serialize(d) for d in items], "total": total}


@router.get("/{sku_id}")
async def get_sku(sku_id: str, request: Request, current_user: dict = Depends(get_current_user), org_id: str = Depends(get_org)):
    coll = await _coll(request, org_id)
    return _serialize(await get_item(coll, sku_id, org_id, "SKU"))


@router.post("", status_code=201)
async def create_sku(
    data: SKUCreate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    coll = await _coll(request, org_id)
    doc = await create_item(coll, data.model_dump(), org_id, str(current_user["_id"]))
    return _serialize(doc)


@router.patch("/{sku_id}")
async def update_sku(
    sku_id: str,
    data: SKUUpdate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    coll = await _coll(request, org_id)
    update_data = data.model_dump(exclude_unset=True)
    doc = await update_item(coll, sku_id, update_data, org_id, "SKU")
    return _serialize(doc)


@router.delete("/{sku_id}", status_code=204)
async def delete_sku(sku_id: str, request: Request, current_user: dict = Depends(get_current_user), org_id: str = Depends(get_org)):
    coll = await _coll(request, org_id)
    await delete_item(coll, sku_id, org_id, "SKU")
