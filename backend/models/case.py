"""
RxTrail — Case entity: Pydantic schemas (request/response).
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from backend.models.common import CaseFlag, CaseStatus, ClosedReason, LostReason


class ProposedSKU(BaseModel):
    sku_id: str
    qty: int = 1


class StatusHistoryEntry(BaseModel):
    from_status: str
    to: str
    at: datetime
    by: str
    note: Optional[str] = None


class CaseBase(BaseModel):
    status: CaseStatus = CaseStatus.NEW
    source_id: Optional[str] = Field("", description="Προαιρετική πηγή σύστασης — μπορεί να μην υπάρχει")
    alias: str = Field(..., min_length=1, max_length=300)
    area: str = ""
    amka: str = Field("", max_length=30, description="ΑΜΚΑ ασθενούς (11ψήφιο) — για αναζήτηση/ταυτοποίηση")
    need_categories: list[str] = []
    needs_freehand: str = ""
    flags: list[str] = []
    est_monthly_value: Optional[float] = None
    proposed_skus: list[ProposedSKU] = []
    doctor_id: Optional[str] = None
    doctor_appointment_at: Optional[datetime] = None
    patient_id: Optional[str] = None
    lost_reason: Optional[LostReason] = None
    lost_reason_text: str = ""
    lost_competitor: Optional[str] = None
    closed_reason: Optional[ClosedReason] = None
    no_repeat: bool = False
    last_fulfilment_id: Optional[str] = None
    fulfil_sla_due: Optional[datetime] = None
    cycle_number: int = 0
    repeat_clock_start: Optional[datetime] = None
    next_repeat_due: Optional[datetime] = None
    contact_attempts: list[dict] = []
    next_action: Optional[str] = None
    next_action_due: Optional[datetime] = None
    geo_lng: Optional[float] = None
    geo_lat: Optional[float] = None


class CaseCreate(BaseModel):
    source_id: Optional[str] = Field("", description="Προαιρετική πηγή — μπορεί να είναι κενό")
    alias: str = Field(..., min_length=1, max_length=300)
    area: str = ""
    amka: str = Field("", max_length=30)
    need_categories: list[str] = []
    needs_freehand: str = ""
    flags: list[str] = []
    geo_lng: Optional[float] = None
    geo_lat: Optional[float] = None


class CaseUpdate(BaseModel):
    alias: Optional[str] = None
    area: Optional[str] = None
    amka: Optional[str] = None
    need_categories: Optional[list[str]] = None
    needs_freehand: Optional[str] = None
    flags: Optional[list[str]] = None
    est_monthly_value: Optional[float] = None
    proposed_skus: Optional[list[ProposedSKU]] = None
    doctor_id: Optional[str] = None
    doctor_appointment_at: Optional[datetime] = None
    next_action: Optional[str] = None
    next_action_due: Optional[datetime] = None
    lost_competitor: Optional[str] = None
    no_repeat: Optional[bool] = None
    geo_lng: Optional[float] = None
    geo_lat: Optional[float] = None


class CaseTransitionRequest(BaseModel):
    action: str
    payload: dict = {}


class CaseResponse(CaseBase):
    id: str = Field(alias="_id")
    org_id: str
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None
    status_history: list[StatusHistoryEntry] = []

    class Config:
        from_attributes = True
        populate_by_name = True


class CaseListParams(BaseModel):
    status: Optional[str] = None
    source_id: Optional[str] = None
    doctor_id: Optional[str] = None
    skip: int = 0
    limit: int = 50
    sort: str = "-created_at"
