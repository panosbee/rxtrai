"""
RxTrail — Document entity: Pydantic schemas.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class DocumentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="Όνομα αρχείου (π.χ. Βεβαίωση Ιατρού)")
    type: str = Field(default="OTHER", description="DOCUMENT|PHOTO|PRESCRIPTION|CERTIFICATE|OTHER")


class DocumentResponse(BaseModel):
    id: str = Field(alias="_id")
    case_id: str
    org_id: str
    name: str
    type: str
    file_name: str  # original filename
    file_size: int
    mime_type: str
    created_at: datetime
    created_by: Optional[str] = None

    class Config:
        from_attributes = True
        populate_by_name = True
