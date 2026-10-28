"""
RxTrail — SKU entity: Pydantic request/response schemas.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from backend.models.common import SKUCategory


class SKUBase(BaseModel):
    product_id: Optional[str] = Field(None, max_length=100, description="Κωδικός προϊόντος κατασκευαστή / εσωτερικός (π.χ. Product ID)")
    eopyy_code: Optional[str] = Field(None, max_length=50, description="Κωδικός ΕΟΠΥΥ — κενό μέχρι να ληφθεί από τον ΕΟΠΥΥ")
    commercial_name: str = Field(..., min_length=1, max_length=300)
    description: str = ""
    category: SKUCategory = SKUCategory.OTHER
    icd10_code: Optional[str] = Field(None, max_length=20, description="Κύριος κωδικός ICD-10 της νόσου που αντιστοιχεί")
    icd10_allowed: list[str] = []
    specialties_allowed: list[str] = []
    reimb_price: float = 0.0
    patient_copay_pct: float = 0.0
    max_qty_month: int = 0
    duration_days_default: int = 30
    criteria: dict = {}
    my_margin: Optional[float] = None
    no_repeat: bool = False
    active: bool = True


class SKUCreate(SKUBase):
    pass


class SKUUpdate(BaseModel):
    product_id: Optional[str] = None
    eopyy_code: Optional[str] = None
    commercial_name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[SKUCategory] = None
    icd10_code: Optional[str] = None
    icd10_allowed: Optional[list[str]] = None
    specialties_allowed: Optional[list[str]] = None
    reimb_price: Optional[float] = None
    patient_copay_pct: Optional[float] = None
    max_qty_month: Optional[int] = None
    duration_days_default: Optional[int] = None
    criteria: Optional[dict] = None
    my_margin: Optional[float] = None
    no_repeat: Optional[bool] = None
    active: Optional[bool] = None


class SKUResponse(SKUBase):
    id: str = Field(alias="_id")
    org_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True
