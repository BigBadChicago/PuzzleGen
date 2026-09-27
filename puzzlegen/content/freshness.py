"""Freshness policy.

A fact's freshness class says how fast it is expected to go wrong, and that
single classification drives three things: when it is next reviewed, when it
becomes stale, and whether a gate will let it into a puzzle at all.

The intervals below are deliberately conservative. Getting them slightly too
short costs curator attention; getting them too long ships a puzzle whose
answer stopped being true, which is the failure mode that damages trust in
every game at once.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from ..core.types import FreshnessClass

#: Days between scheduled re-verifications, by class. ``None`` means a fact
#: that never needs re-checking: a mountain's height, a word's etymology.
REVIEW_INTERVAL_DAYS: dict[FreshnessClass, int | None] = {
    FreshnessClass.STATIC: None,
    FreshnessClass.SLOW_CHANGING: 730,
    FreshnessClass.PERIODIC: 365,
    FreshnessClass.TIME_SENSITIVE: 90,
    FreshnessClass.VOLATILE: 7,
}

#: How far past its review date a fact may drift before it is refused outright
#: rather than merely flagged. Expressed as a multiple of the interval: a
#: volatile fact one day late is suspect, a slow-changing fact one day late is
#: not, and a single absolute grace period cannot express both.
STALENESS_MULTIPLIER: dict[FreshnessClass, float] = {
    FreshnessClass.STATIC: 1.0,
    FreshnessClass.SLOW_CHANGING: 1.5,
    FreshnessClass.PERIODIC: 1.25,
    FreshnessClass.TIME_SENSITIVE: 1.1,
    FreshnessClass.VOLATILE: 1.0,
}


@dataclass(frozen=True, slots=True)
class FreshnessVerdict:
    fresh: bool
    stale: bool
    due: bool
    days_overdue: int
    detail: str


def next_review_at(
    freshness_class: FreshnessClass, verified_at: dt.datetime
) -> dt.datetime | None:
    """When a record verified now should next be checked."""
    interval = REVIEW_INTERVAL_DAYS[freshness_class]
    if interval is None:
        return None
    return verified_at + dt.timedelta(days=interval)


def evaluate(
    freshness_class: FreshnessClass,
    verified_at: dt.datetime | None,
    next_review: dt.datetime | None,
    now: dt.datetime,
) -> FreshnessVerdict:
    """Whether a record is fresh enough to use, and how overdue it is."""
    if freshness_class is FreshnessClass.STATIC:
        return FreshnessVerdict(True, False, False, 0, "static fact, never expires")

    if verified_at is None or next_review is None:
        return FreshnessVerdict(
            False, True, True, 0, "non-static fact has never been verified"
        )

    overdue = (now - next_review).days
    if overdue <= 0:
        return FreshnessVerdict(True, False, False, 0, "within review window")

    interval = REVIEW_INTERVAL_DAYS[freshness_class] or 1
    grace = interval * (STALENESS_MULTIPLIER[freshness_class] - 1.0)
    stale = overdue > grace
    return FreshnessVerdict(
        fresh=not stale,
        stale=stale,
        due=True,
        days_overdue=overdue,
        detail=f"{overdue} days past review; grace is {grace:.0f} days",
    )
