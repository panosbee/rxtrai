"""
RxTrail — Prescription entity: Pydantic schemas.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PrescriptionLine(BaseModel):
    sku_id: str
    eopyy_code: str = ""
    qty: int = 1
    duration_days: int = 30


class PrescriptionBase(BaseModel):
    case_id: Optional[str] = None
    doctor_id: str = Field(..., description="Ο γιατρός που έγραψε")
    lines: list[PrescriptionLine] = []
    issued_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    confirmed_by_user: bool = True


class PrescriptionCreate(PrescriptionBase):
    pass


class PrescriptionUpdate(BaseModel):
    case_id: Optional[str] = None
    doctor_id: Optional[str] = None
    confirmed_by_user: Optional[bool] = None


class PrescriptionResponse(PrescriptionBase):
    id: str = Field(alias="_id")
    org_id: str
    created_at: datetime
    updated_at: datetime
    photo_path: Optional[str] = None
    ocr_confidence: Optional[float] = None

    class Config:
        from_attributes = True
        populate_by_name = True
