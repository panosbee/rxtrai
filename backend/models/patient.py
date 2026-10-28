"""
RxTrail — Patient entity: Pydantic schemas.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PatientBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    phone: str = ""
    address: str = ""
    caregiver_name: Optional[str] = None
    caregiver_phone: Optional[str] = None
    consent_at: Optional[datetime] = None
    erased: bool = False


class PatientCreate(PatientBase):
    case_id: str = Field(..., description="1:1 με case")


class PatientUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    caregiver_name: Optional[str] = None
    caregiver_phone: Optional[str] = None


class PatientResponse(PatientBase):
    id: str = Field(alias="_id")
    org_id: str
    case_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True
