"""
RxTrail — Clock calculations: repeat schedules, SLA timers, notification due dates.

All calculations are pure functions (no DB access) for testability.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize a datetime read from MongoDB (stored UTC, returned naive) to tz-aware UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def repeat_notify_at(fulfilled_at: datetime, duration_days: int, notify_day: int) -> datetime:
    """Calculate when the repeat reminder should fire.
    Example: fulfilled day 1, duration 30 days, notify_day 25 → day 25 notify.
    """
    return ensure_utc(fulfilled_at) + timedelta(days=notify_day)


def repeat_due_at(fulfilled_at: datetime, duration_days: int) -> datetime:
    """When the next repeat is due."""
    return ensure_utc(fulfilled_at) + timedelta(days=duration_days)


def at_risk_at(fulfilled_at: datetime, at_risk_day: int) -> datetime:
    """When the case becomes AT_RISK if no repeat happened."""
    return ensure_utc(fulfilled_at) + timedelta(days=at_risk_day)


def sla_new_deadline(created_at: datetime, sla_days: int) -> datetime:
    """When the NEW case SLA expires."""
    return ensure_utc(created_at) + timedelta(days=sla_days)


def sla_assessed_deadline(entered_assessed_at: datetime, sla_days: int) -> datetime:
    """When ASSESSED SLA expires."""
    return ensure_utc(entered_assessed_at) + timedelta(days=sla_days)


def sla_prescribed_deadline(entered_prescribed_at: datetime, sla_days: int) -> datetime:
    """When PRESCRIBED SLA expires (no fulfilment yet)."""
    return ensure_utc(entered_prescribed_at) + timedelta(days=sla_days)


def urgency_decay(days_overdue: int) -> float:
    """Urgency multiplier that decays over time.
    0-3 days: 1.0, 4-7: 0.8, 8-14: 0.6, 15+: 0.4
    """
    if days_overdue <= 3:
        return 1.0
    if days_overdue <= 7:
        return 0.8
    if days_overdue <= 14:
        return 0.6
    return 0.4


def days_overdue(reference_date: datetime, now: Optional[datetime] = None) -> int:
    """How many days past a reference date.
    Handles both naive and timezone-aware datetimes from MongoDB.
    """
    now = now or datetime.now(timezone.utc)
    # Ensure reference_date is timezone-aware
    if reference_date.tzinfo is None:
        reference_date = reference_date.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0, (now - reference_date).days)


def next_cycle_number(last_fulfilment_cycle: int) -> int:
    """Increment cycle number for repeat fulfilments."""
    return last_fulfilment_cycle + 1


def fulfil_sla_deadline(entered_prescribed_at: datetime, fulfil_sla_days: int) -> datetime:
    """REQ-01: When the fulfil SLA expires after prescription (→ AT_RISK if not executed)."""
    return ensure_utc(entered_prescribed_at) + timedelta(days=fulfil_sla_days)


def should_skip_repeat_clock(case: dict) -> bool:
    """REQ-02: True if case should be skipped for repeat clock checks.
    True for no_repeat cases or cases with no repeat clock set."""
    if case.get("no_repeat"):
        return True
    if case.get("next_repeat_due") is None:
        return True
    return False
