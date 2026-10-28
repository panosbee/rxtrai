"""
RxTrail — Fulfilment entity: Pydantic schemas.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class FulfilmentLine(BaseModel):
    sku_id: str
    qty: int = 1
    revenue: float = 0.0


class FulfilmentBase(BaseModel):
    prescription_id: str
    case_id: Optional[str] = None
    doctor_id: Optional[str] = None
    lines: list[FulfilmentLine] = []
    total_revenue: float = 0.0
    fulfilled_at: Optional[datetime] = None


class FulfilmentCreate(FulfilmentBase):
    pass


class FulfilmentResponse(FulfilmentBase):
    id: str = Field(alias="_id")
    org_id: str
    created_at: datetime
    updated_at: datetime
    cycle_number: int = 1
    next_repeat_due: Optional[datetime] = None
    notify_at: Optional[datetime] = None

    class Config:
        from_attributes = True
        populate_by_name = True
