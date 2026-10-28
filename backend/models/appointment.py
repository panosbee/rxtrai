"""
RxTrail — Appointments: model + CRUD API + conflict detection.

Supports:
  - Appointments with doctors (doctor_id)
  - Appointments linked to cases (case_id)
  - Free-form appointments (personal tasks)
  - Conflict detection (same time ± buffer for same user)
  - Month/week views
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, Field


class AppointmentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    at: datetime
    end_at: Optional[datetime] = None
    kind: str = Field(default="OTHER", description="DOCTOR|CASE|PERSONAL|OTHER")
    doctor_id: Optional[str] = None
    case_id: Optional[str] = None
    location: str = ""
    notes: str = ""


class AppointmentUpdate(BaseModel):
    title: Optional[str] = None
    at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    kind: Optional[str] = None
    doctor_id: Optional[str] = None
    case_id: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None


class AppointmentResponse(BaseModel):
    id: str = Field(alias="_id")
    org_id: str
    title: str
    at: datetime
    end_at: Optional[datetime] = None
    kind: str
    doctor_id: Optional[str] = None
    case_id: Optional[str] = None
    location: str
    notes: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True
