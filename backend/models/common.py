"""
RxTrail — Shared Pydantic models and enums used across entities.
"""
from datetime import datetime
from enum import Enum

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field


class PyObjectId(str):
    """Pydantic-compatible ObjectId that serializes to string."""
    @classmethod
    def __get_pydantic_core_schema__(cls, _source_type, _handler):
        from pydantic_core import core_schema
        return core_schema.union_schema([
            core_schema.is_instance_schema(ObjectId),
            core_schema.no_info_plain_validator_function(cls.validate),
        ])

    @classmethod
    def validate(cls, v):
        if isinstance(v, ObjectId):
            return str(v)
        if isinstance(v, str) and ObjectId.is_valid(v):
            return v
        raise ValueError(f"Invalid ObjectId: {v}")


class MongoModel(BaseModel):
    """Base model with id, timestamps."""
    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)
    id: str | None = Field(default=None, alias="_id")
    org_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ── Enums ────────────────────────────────────────────────────────────────────

class CaseStatus(str, Enum):
    NEW = "NEW"
    ASSESSED = "ASSESSED"
    AT_DOCTOR = "AT_DOCTOR"
    PRESCRIBED = "PRESCRIBED"
    ACTIVE = "ACTIVE"
    AT_RISK = "AT_RISK"
    LOST = "LOST"
    CLOSED = "CLOSED"


class SKUCategory(str, Enum):
    ONS = "ONS"
    WOUND = "WOUND"
    CATHETER = "CATHETER"
    OSTOMY = "OSTOMY"
    TRACHEO = "TRACHEO"
    OXYGEN = "OXYGEN"
    ENTERAL = "ENTERAL"
    ORTHO = "ORTHO"
    HYALURONIC = "HYALURONIC"
    ONCO_SUPPORT = "ONCO_SUPPORT"
    INCONT = "INCONT"
    URO = "URO"
    OTHER = "OTHER"


class StructureType(str, Enum):
    PUBLIC_HOSP = "PUBLIC_HOSP"
    PRIVATE_CLINIC = "PRIVATE_CLINIC"
    REHAB = "REHAB"
    ELDERLY_HOME = "ELDERLY_HOME"
    DIALYSIS = "DIALYSIS"
    PHARMACY = "PHARMACY"
    HOMECARE_ORG = "HOMECARE_ORG"
    PRACTICE = "PRACTICE"
    OTHER = "OTHER"


class DoctorSpecialty(str, Enum):
    SURGEON = "SURGEON"
    INTERNIST = "INTERNIST"
    PHYSIATRIST = "PHYSIATRIST"
    ORTHOPEDIC = "ORTHOPEDIC"
    NEUROLOGIST = "NEUROLOGIST"
    DERMATOLOGIST = "DERMATOLOGIST"
    UROLOGIST = "UROLOGIST"
    ONCOLOGIST = "ONCOLOGIST"
    PULMONOLOGIST = "PULMONOLOGIST"
    GASTROENTEROLOGIST = "GASTROENTEROLOGIST"
    ENDOCRINOLOGIST = "ENDOCRINOLOGIST"
    CARDIOLOGIST = "CARDIOLOGIST"
    NEPHROLOGIST = "NEPHROLOGIST"
    GERIATRICIAN = "GERIATRICIAN"
    GP = "GP"
    HEAD_NURSE = "HEAD_NURSE"
    WOUND_NURSE = "WOUND_NURSE"
    STOMA_NURSE = "STOMA_NURSE"
    DIETITIAN = "DIETITIAN"
    PHYSIO = "PHYSIO"
    SECRETARY = "SECRETARY"
    OTHER = "OTHER"


class DoctorRelationship(str, Enum):
    COLD = "COLD"
    WARM = "WARM"
    FRIEND = "FRIEND"


class WorkplaceType(str, Enum):
    HOSPITAL = "HOSPITAL"
    PRIVATE_CLINIC = "PRIVATE_CLINIC"
    FREELANCER = "FREELANCER"
    HEALTH_CENTER = "HEALTH_CENTER"
    NURSING_HOME = "NURSING_HOME"
    OTHER = "OTHER"


class SourceType(str, Enum):
    PHYSIO = "PHYSIO"
    HOMECARE_CREW = "HOMECARE_CREW"
    NURSE = "NURSE"
    PATIENT_ASSOC = "PATIENT_ASSOC"
    DOCTOR = "DOCTOR"
    WORD_OF_MOUTH = "WORD_OF_MOUTH"
    OTHER = "OTHER"


class VisitChannel(str, Enum):
    IN_PERSON = "IN_PERSON"
    PHONE = "PHONE"
    CONGRESS = "CONGRESS"


class VisitOutcome(str, Enum):
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    FRIDGE = "FRIDGE"
    PRESCRIBED = "PRESCRIBED"


class LostReason(str, Enum):
    WENT_TO_COMPETITOR = "WENT_TO_COMPETITOR"
    NOT_EXECUTED = "NOT_EXECUTED"
    NO_ANSWER = "NO_ANSWER"
    PATIENT_DECLINED = "PATIENT_DECLINED"
    MEDICAL_CHANGE = "MEDICAL_CHANGE"
    PRICE = "PRICE"
    SERVICE = "SERVICE"
    DOCTOR = "DOCTOR"
    OTHER = "OTHER"


class ClosedReason(str, Enum):
    DECEASED = "DECEASED"
    RECOVERED = "RECOVERED"


class ContactChannel(str, Enum):
    CALL = "CALL"
    SMS = "SMS"
    IN_PERSON = "IN_PERSON"
    OTHER = "OTHER"


class ContactOutcome(str, Enum):
    NO_ANSWER = "NO_ANSWER"
    REACHED = "REACHED"
    CALLBACK_REQUESTED = "CALLBACK_REQUESTED"
    WRONG_NUMBER = "WRONG_NUMBER"


class PersonnelType(str, Enum):
    DOCTOR = "DOCTOR"
    NURSE = "NURSE"
    OTHER = "OTHER"


class CaseFlag(str, Enum):
    FAMILY_DECIDES = "FAMILY_DECIDES"


class NotificationKind(str, Enum):
    REPEAT_DUE = "REPEAT_DUE"
    SLA_NEW = "SLA_NEW"
    SLA_ASSESSED = "SLA_ASSESSED"
    SLA_PRESCRIBED = "SLA_PRESCRIBED"
    AT_RISK = "AT_RISK"
    ACTION_DUE = "ACTION_DUE"
