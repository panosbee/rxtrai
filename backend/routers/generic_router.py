"""
RxTrail — Generic CRUD router factory for network entities.
One function creates all 5 routes (list, get, create, update, delete)
for any collection/model pair, reducing boilerplate.
"""
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from backend.auth.deps import get_current_user, get_org
from backend.db import get_db
from backend.routers.crud_helpers import create_item, delete_item, get_item, list_items, update_item


def _serialize(doc: dict) -> dict:
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


def make_crud_router(
    prefix: str,
    tag: str,
    collection_name: str,
    create_model: Any,
    update_model: Any,
    list_filter_fields: list[str] | None = None,
) -> APIRouter:
    """Factory: returns an APIRouter with standard CRUD routes.

    Args:
        prefix: URL path (e.g. "/structures")
        tag: OpenAPI tag
        collection_name: MongoDB collection name
        create_model: Pydantic model for POST body
        update_model: Pydantic model for PATCH body
        list_filter_fields: optional string fields allowed as query params for filtering
    """
    r = APIRouter(prefix=prefix, tags=[tag])

    async def _coll(request: Request, org_id: str):
        db = await get_db()
        from backend.db import scoped
        return scoped(db[collection_name], org_id)

    @r.get("")
    async def _list(
        request: Request,
        current_user: dict = Depends(get_current_user),
        org_id: str = Depends(get_org),
        skip: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
        sort: str = "-created_at",
    ):
        coll = await _coll(request, org_id)
        extra: dict = {}
        if list_filter_fields:
            for f in list_filter_fields:
                val = request.query_params.get(f, "")
                if val:
                    extra[f] = val
        items, total = await list_items(coll, org_id, skip, limit, sort, extra)
        return {"items": [_serialize(d) for d in items], "total": total}

    @r.get("/{item_id}")
    async def _get(item_id: str, request: Request, current_user: dict = Depends(get_current_user), org_id: str = Depends(get_org)):
        coll = await _coll(request, org_id)
        return _serialize(await get_item(coll, item_id, org_id, tag))

    @r.post("", status_code=201)
    async def _create(
        data: create_model,
        request: Request,
        current_user: dict = Depends(get_current_user),
        org_id: str = Depends(get_org),
    ):
        coll = await _coll(request, org_id)
        doc = await create_item(coll, data.model_dump(), org_id, str(current_user["_id"]))
        return _serialize(doc)

    @r.patch("/{item_id}")
    async def _update(
        item_id: str,
        data: update_model,
        request: Request,
        current_user: dict = Depends(get_current_user),
        org_id: str = Depends(get_org),
    ):
        coll = await _coll(request, org_id)
        update_data = data.model_dump(exclude_unset=True)
        doc = await update_item(coll, item_id, update_data, org_id, tag)
        return _serialize(doc)

    @r.delete("/{item_id}", status_code=204)
    async def _delete(item_id: str, request: Request, current_user: dict = Depends(get_current_user), org_id: str = Depends(get_org)):
        coll = await _coll(request, org_id)
        await delete_item(coll, item_id, org_id, tag)

    return r
