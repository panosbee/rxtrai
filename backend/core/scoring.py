"""
RxTrail — Queue scoring: deterministic priority algorithm for the daily queue.

Each queue item has a kind and optional metadata. The score determines the
order in which items appear on the Today screen.
"""
from enum import Enum

from backend.config import settings


class QueueItemKind(str, Enum):
    AT_RISK = "AT_RISK"
    REPEAT_DUE = "REPEAT_DUE"
    SLA_NEW = "SLA_NEW"
    SLA_ASSESSED = "SLA_ASSESSED"
    SLA_PRESCRIBED = "SLA_PRESCRIBED"
    ACTION_DUE = "ACTION_DUE"
    VISIT_SUGGEST = "VISIT_SUGGEST"
    CONTACT_DUE = "CONTACT_DUE"     # REQ-06: contact retry due today


def score_queue_item(kind: str, days_overdue: int = 0, tier_bonus: float = 0.0, est_monthly_value: float | None = None, contact_due: bool = False) -> float:
    """Calculate priority score for a queue item. Higher = more urgent.

    Args:
        kind: One of QueueItemKind values
        days_overdue: How many days past the deadline
        tier_bonus: Additional bonus (e.g. for doctor tier in visit suggestions)
        est_monthly_value: Estimated monthly value in € (REQ-03)
        contact_due: Whether a contact retry is due today (REQ-06)

    Returns:
        Float score — higher means more urgent / appears first
    """
    from backend.core.clocks import urgency_decay

    s = 0.0

    if kind == "AT_RISK":
        s += 50
    elif kind == "REPEAT_DUE":
        s += 38 * urgency_decay(days_overdue)
    elif kind == "SLA_NEW":
        s += 25 * urgency_decay(days_overdue)
    elif kind == "SLA_PRESCRIBED":
        s += 20
    elif kind == "SLA_ASSESSED":
        s += 15
    elif kind == "ACTION_DUE":
        s += 20
    elif kind == "VISIT_SUGGEST":
        s += 10 + tier_bonus
    elif kind == "CONTACT_DUE":
        s += 12

    # REQ-03: Value-weighted multiplier
    base_score = s
    value_factor = _value_factor(est_monthly_value)
    s = base_score * value_factor

    # REQ-06: Contact due bonus (additive, after value factor)
    if contact_due:
        s += settings.CONTACT_DUE_BONUS

    return round(s, 1)


def _value_factor(est_monthly_value: float | None) -> float:
    """REQ-03: Compute value factor in [1, 2] based on monthly value.
    
    Formula: 1 + min(value, VALUE_CAP) / VALUE_CAP
    - 0 or missing -> 1.0 (no penalty for unvalued items)
    - 500 (cap) -> 2.0 (max boost)
    - 250 -> 1.5
    """
    if not est_monthly_value or est_monthly_value <= 0:
        return 1.0
    capped = min(est_monthly_value, settings.VALUE_CAP)
    return 1.0 + capped / settings.VALUE_CAP
