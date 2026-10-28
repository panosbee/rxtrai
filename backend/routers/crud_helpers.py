"""
RxTrail — Generic CRUD helpers for network entities.
Reduces boilerplate in routers while keeping full control.
"""
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorCollection


async def list_items(
    coll: AsyncIOMotorCollection,
    org_id: str,
    skip: int = 0,
    limit: int = 50,
    sort: str = "-created_at",
    extra_filter: dict | None = None,
) -> tuple[list[dict], int]:
    """Return paginated list + total count."""
    filt = {"org_id": org_id}
    if extra_filter:
        filt.update(extra_filter)

    sort_field = sort.lstrip("-")
    sort_dir = -1 if sort.startswith("-") else 1

    cursor = coll.find(filt).sort(sort_field, sort_dir).skip(skip).limit(limit)
    items = await cursor.to_list(length=limit)
    total = await coll.count_documents(filt)
    return items, total


async def get_item(
    coll: AsyncIOMotorCollection,
    item_id: str,
    org_id: str,
    name: str = "Item",
) -> dict:
    """Fetch single item by id, scoped to org."""
    doc = await coll.find_one({"_id": ObjectId(item_id), "org_id": org_id})
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{name} not found")
    return doc


async def create_item(
    coll: AsyncIOMotorCollection,
    data: dict,
    org_id: str,
    user_id: str | None = None,
) -> dict:
    now = datetime.now(timezone.utc)
    doc = {
        **data,
        "org_id": org_id,
        "created_at": now,
        "updated_at": now,
    }
    if user_id:
        doc["created_by"] = user_id
    result = await coll.insert_one(doc)
    doc["_id"] = str(result.inserted_id)
    return doc


async def update_item(
    coll: AsyncIOMotorCollection,
    item_id: str,
    data: dict,
    org_id: str,
    name: str = "Item",
) -> dict:
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")
    data["updated_at"] = datetime.now(timezone.utc)
    result = await coll.update_one(
        {"_id": ObjectId(item_id), "org_id": org_id},
        {"$set": data},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{name} not found")
    doc = await coll.find_one({"_id": ObjectId(item_id), "org_id": org_id})
    return doc


async def delete_item(
    coll: AsyncIOMotorCollection,
    item_id: str,
    org_id: str,
    name: str = "Item",
) -> None:
    result = await coll.delete_one({"_id": ObjectId(item_id), "org_id": org_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{name} not found")
