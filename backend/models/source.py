"""
RxTrail — Source (πηγή σύστασης) entity: Pydantic schemas.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from backend.models.common import SourceType


class SourceBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    type: SourceType = SourceType.OTHER
    linked_contact_id: Optional[str] = None
    linked_doctor_id: Optional[str] = None
    phone: str = ""
    notes: str = ""


class SourceCreate(SourceBase):
    pass


class SourceUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[SourceType] = None
    linked_contact_id: Optional[str] = None
    linked_doctor_id: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None


class SourceResponse(SourceBase):
    id: str = Field(alias="_id")
    org_id: str
    created_at: datetime
    updated_at: datetime
    # Metrics (event-driven, read-only)
    cases_brought: int = 0
    cases_active: int = 0
    cases_lost: int = 0
    cases_closed: int = 0
    last_referral_at: Optional[datetime] = None

    class Config:
        from_attributes = True
        populate_by_name = True
