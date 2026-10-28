"""
RxTrail — LLM JSON schemas: strict Pydantic models for every articulation.

Every LLM response MUST validate against one of these schemas.
If validation fails → retry → fallback to manual form.
"""
from typing import Optional

from pydantic import BaseModel, Field


# ── Άρθρωση A: Intake περιστατικού ──────────────────────────────────────────

class IntakeResult(BaseModel):
    """Structured output from voice/text intake of a new case."""
    source_name_raw: str = Field(
        default="",
        description="Όνομα πηγής όπως αναφέρθηκε (π.χ. 'Μαρία / Βοήθεια στο Σπίτι')",
    )
    area: str = Field(
        default="",
        description="Περιοχή ασθενή (π.χ. 'Γάζι', 'Ηράκλειο')",
    )
    alias_suggestion: str = Field(
        default="",
        description="Προτεινόμενο ψευδώνυμο (π.χ. 'Παππούς Γάζι — κατάκλιση')",
    )
    need_categories: list[str] = Field(
        default_factory=list,
        description="Κατηγορίες αναγκών: ONS|WOUND|CATHETER|OSTOMY|TRACHEO|OXYGEN|ENTERAL|ORTHO|HYALURONIC|ONCO_SUPPORT|INCONT|OTHER",
    )
    uncertain_categories: list[str] = Field(
        default_factory=list,
        description="Κατηγορίες που αναφέρθηκαν αλλά δεν είναι σίγουρο",
    )
    flags: list[str] = Field(
        default_factory=list,
        description="Σημαίες: FAMILY_DECIDES",
    )
    freehand_summary: str = Field(
        default="",
        description="Σύντομη περίληψη σε ελεύθερο κείμενο",
    )
    followup_questions: list[str] = Field(
        default_factory=list,
        description=(
            "Έως 3 σύντομες ερωτήσεις για ό,τι ΣΗΜΑΝΤΙΚΟ λείπει και δεν ειπώθηκε "
            "(π.χ. 'Ποιος σε έστειλε;', 'Περιοχή;', 'Τι ανάγκες έχει;'). "
            "Κενή λίστα αν δεν λείπουν σημαντικά στοιχεία."
        ),
    )


# ── Άρθρωση B: Αξιολόγηση αναγκών ──────────────────────────────────────────

class ProposedSKUItem(BaseModel):
    eopyy_code: str = Field(..., description="Κωδικός ΕΟΠΥΥ του προτεινόμενου SKU")
    qty_month: int = Field(default=1, description="Προτεινόμενη μηνιαία ποσότητα")
    why_one_line: str = Field(default="", description="Μία γραμμή αιτιολόγησης")


class AssessResult(BaseModel):
    """Structured output from case assessment."""
    proposed: list[ProposedSKUItem] = Field(
        default_factory=list,
        description="Προτεινόμενα SKU (ΜΟΝΟ από τον κατάλογο που δόθηκε)",
    )
    specialties_allowed_union: list[str] = Field(
        default_factory=list,
        description="Ειδικότητες που δικαιούνται να γράψουν αυτά τα SKU",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Προειδοποιήσεις (π.χ. αντένδειξη, έλλειψη κάλυψης)",
    )


# ── Άρθρωση C: Δόμηση γνωμάτευσης ─────────────────────────────────────────

class PrescriptionLineRaw(BaseModel):
    eopyy_code_raw: str = Field(default="", description="Κωδικός όπως φαίνεται στη γνωμάτευση")
    name_raw: str = Field(default="", description="Ονομασία όπως φαίνεται στη γνωμάτευση")
    qty: int = Field(default=1)
    duration_days: int = Field(default=30)


class PrescriptionOCRResult(BaseModel):
    """Structured output from OCR'd prescription."""
    lines: list[PrescriptionLineRaw] = Field(default_factory=list)
    doctor_name_raw: str = Field(default="", description="Όνομα γιατρού όπως φαίνεται")
    issued_date: str = Field(default="", description="Ημερομηνία έκδοσης")


# ── Άρθρωση D: Brief πριν από επαφή ────────────────────────────────────────

class BriefResult(BaseModel):
    brief: str = Field(default="", description="1-2 προτάσεις context")


# ── Άρθρωση E: Αλήθεια εβδομάδας ──────────────────────────────────────────

class WeeklyTruthResult(BaseModel):
    headline: str = Field(default="", description="Τίτλος-κλειδί εβδομάδας")
    insight: str = Field(default="", description="Το insight της εβδομάδας")


# ── Άρθρωση F: Wizard mapping ──────────────────────────────────────────────

class ColumnMapping(BaseModel):
    column_name: str
    field: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class MappingResult(BaseModel):
    mappings: list[ColumnMapping] = Field(default_factory=list)
