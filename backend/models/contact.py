"""
RxTrail — Contact (gatekeeper) entity: Pydantic schemas.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from backend.models.common import ContactRole


class ContactBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    role_title: ContactRole = ContactRole.OTHER
    structure_id: Optional[str] = None
    phone: str = ""
    notes: str = ""
    is_also_source: bool = False


class ContactCreate(ContactBase):
    pass


class ContactUpdate(BaseModel):
    name: Optional[str] = None
    role_title: Optional[ContactRole] = None
    structure_id: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None
    is_also_source: Optional[bool] = None


class ContactResponse(ContactBase):
    id: str = Field(alias="_id")
    org_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True
