"""
RxTrail — Settings router: org configuration management.
"""
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.auth.deps import get_current_user, get_org
from backend.db import get_db

router = APIRouter(prefix="/settings", tags=["settings"])


class OrgSettingsUpdate(BaseModel):
    name: str | None = None
    repeat_notify_day: int | None = Field(default=None, ge=1, le=30)
    at_risk_day: int | None = Field(default=None, ge=1, le=60)
    new_sla_days: int | None = Field(default=None, ge=1, le=30)
    assessed_sla_days: int | None = Field(default=None, ge=1, le=30)
    prescribed_sla_days: int | None = Field(default=None, ge=1, le=30)
    photo_retention_days: int | None = Field(default=None, ge=1, le=365)


@router.get("")
async def get_settings(
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Get current org settings."""
    db = await get_db()
    org = await db.orgs.find_one({"_id": ObjectId(org_id)})
    if not org:
        raise HTTPException(404, "Org not found")

    return {
        "name": org.get("name", ""),
        "settings": org.get("settings", {}),
        "created_at": str(org.get("created_at", "")),
    }


@router.patch("")
async def update_settings(
    data: OrgSettingsUpdate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    org_id: str = Depends(get_org),
):
    """Update org settings. Only owner can do this."""
    db = await get_db()

    # Only owner can update settings
    if current_user.get("role") != "owner":
        raise HTTPException(403, "Μόνο ο owner μπορεί να αλλάξει ρυθμίσεις")

    update_fields = {}

    # Update org name
    if data.name is not None:
        update_fields["name"] = data.name

    # Update nested settings
    settings_update = {}
    for field in ["repeat_notify_day", "at_risk_day", "new_sla_days",
                   "assessed_sla_days", "prescribed_sla_days", "photo_retention_days"]:
        val = getattr(data, field, None)
        if val is not None:
            settings_update[field] = val

    if settings_update:
        for key, val in settings_update.items():
            update_fields[f"settings.{key}"] = val

    if not update_fields:
        raise HTTPException(400, "No fields to update")

    update_fields["updated_at"] = datetime.now(timezone.utc)

    await db.orgs.update_one(
        {"_id": ObjectId(org_id)},
        {"$set": update_fields},
    )

    org = await db.orgs.find_one({"_id": ObjectId(org_id)})
    return {
        "name": org.get("name", ""),
        "settings": org.get("settings", {}),
    }
