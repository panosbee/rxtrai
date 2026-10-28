"""
RxTrail — Visit entity: Pydantic schemas.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from backend.models.common import VisitChannel, VisitOutcome


class RxEstimate(BaseModel):
    count: int = 0
    category: str = ""


class ExtractedAction(BaseModel):
    text: str
    due: Optional[datetime] = None
    confirmed: bool = False


class VisitBase(BaseModel):
    target_type: str = Field(..., description="DOCTOR|CONTACT|STRUCTURE")
    target_id: str = Field(...)
    at: datetime = Field(default_factory=datetime.utcnow)
    channel: VisitChannel = VisitChannel.IN_PERSON
    outcome: VisitOutcome = VisitOutcome.NEUTRAL
    rx_estimate: Optional[RxEstimate] = None
    voice_note_text: Optional[str] = None
    extracted_actions: list[ExtractedAction] = []


class VisitCreate(VisitBase):
    pass


class VisitUpdate(BaseModel):
    channel: Optional[VisitChannel] = None
    outcome: Optional[VisitOutcome] = None
    rx_estimate: Optional[RxEstimate] = None
    voice_note_text: Optional[str] = None


class VisitResponse(VisitBase):
    id: str = Field(alias="_id")
    org_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True
