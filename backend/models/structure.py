"""
RxTrail — Structure entity: Pydantic schemas.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from backend.models.common import StructureType


class StructureBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    type: StructureType = StructureType.OTHER
    city: str = ""
    prefecture: str = ""
    address: str = ""
    phone: str = ""
    notes: str = ""
    priority: int = Field(default=1, ge=1, le=3)
    geo_lng: Optional[float] = None
    geo_lat: Optional[float] = None


class StructureCreate(StructureBase):
    pass


class StructureUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[StructureType] = None
    city: Optional[str] = None
    prefecture: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None
    priority: Optional[int] = None
    geo_lng: Optional[float] = None
    geo_lat: Optional[float] = None


class StructureResponse(StructureBase):
    id: str = Field(alias="_id")
    org_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True
