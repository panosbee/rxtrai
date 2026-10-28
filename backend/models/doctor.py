"""
RxTrail — Doctor entity: Pydantic schemas.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from backend.models.common import DoctorRelationship, DoctorSpecialty, PersonnelType, WorkplaceType


class DoctorBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    personnel_type: PersonnelType = PersonnelType.DOCTOR
    specialty: DoctorSpecialty = DoctorSpecialty.OTHER
    role_title: str = ""
    workplace_type: WorkplaceType = WorkplaceType.OTHER
    structure_ids: list[str] = []
    address: str = ""
    area: str = ""
    phone: str = ""
    email: str = ""
    notes: str = ""
    relationship: DoctorRelationship = DoctorRelationship.COLD
    max_patients_month: Optional[int] = None
    product_categories: list[str] = []


class DoctorCreate(DoctorBase):
    pass


class DoctorUpdate(BaseModel):
    name: Optional[str] = None
    personnel_type: Optional[PersonnelType] = None
    specialty: Optional[DoctorSpecialty] = None
    role_title: Optional[str] = None
    workplace_type: Optional[WorkplaceType] = None
    structure_ids: Optional[list[str]] = None
    address: Optional[str] = None
    area: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None
    relationship: Optional[DoctorRelationship] = None
    max_patients_month: Optional[int] = None
    product_categories: Optional[list[str]] = None


class DoctorResponse(DoctorBase):
    id: str = Field(alias="_id")
    org_id: str
    created_at: datetime
    updated_at: datetime
    # Metrics (read-only, updated by jobs/events)
    rx_count_90d: int = 0
    avg_pickup_days: Optional[float] = None
    open_cases_count: int = 0
    last_visit_at: Optional[datetime] = None

    class Config:
        from_attributes = True
        populate_by_name = True
