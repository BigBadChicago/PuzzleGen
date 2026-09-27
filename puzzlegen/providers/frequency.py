"""Word frequency as a replaceable service.

Frequency matters because a group mixing an everyday word with an obscure one
is unfair regardless of how semantically tidy it is. What must not happen is
the frequency dataset becoming load-bearing: ``wordfreq`` could be abandoned
tomorrow, so the engine reads an exported table rather than calling any
library, and every score carries the source name, source version and retrieval
date that would let a curator identify what needs replacing.

Bands, not raw scores, cross the plugin boundary. A plugin asking for COMMON
entities keeps working when the underlying dataset changes scale.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..core.types import FrequencyBand

#: Zipf scale: roughly 7 for "the", 4 for a common everyday word, 2 for a word
#: most adults know but rarely write, below 2 for specialist vocabulary.
#: Boundaries are inclusive lower bounds, ordered from most to least common.
DEFAULT_BANDS: tuple[tuple[FrequencyBand, float], ...] = (
    (FrequencyBand.VERY_COMMON, 5.0),
    (FrequencyBand.COMMON, 4.0),
    (FrequencyBand.UNCOMMON, 3.0),
    (FrequencyBand.RARE, 2.0),
    (FrequencyBand.OBSCURE, 0.0),
)

#: Ordering used to measure how far apart two bands are. A group spanning more
#: than one step is usually unfair even when each member is individually fine.
BAND_ORDER: tuple[FrequencyBand, ...] = tuple(band for band, _ in DEFAULT_BANDS)


def band_for_zipf(
    zipf: float, bands: tuple[tuple[FrequencyBand, float], ...] = DEFAULT_BANDS
) -> FrequencyBand:
    for band, threshold in bands:
        if zipf >= threshold:
            return band
    return FrequencyBand.OBSCURE


def band_distance(a: FrequencyBand, b: FrequencyBand) -> int:
    """Steps between two bands, ignoring direction."""
    return abs(BAND_ORDER.index(a) - BAND_ORDER.index(b))


def band_spread(bands: list[FrequencyBand]) -> int:
    """Widest gap across a group of bands. Zero when all members match."""
    if not bands:
        return 0
    positions = [BAND_ORDER.index(b) for b in bands]
    return max(positions) - min(positions)


@dataclass(frozen=True, slots=True)
class FrequencyScore:
    term: str
    lang: str
    zipf: float
    band: FrequencyBand


@dataclass(frozen=True, slots=True)
class FrequencyDescriptor:
    name: str
    version: str
    retrieved_at: dt.datetime

    def __post_init__(self) -> None:
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must be timezone-aware")


@runtime_checkable
class FrequencyProvider(Protocol):
    """Supplies corpus frequency for a term."""

    def describe(self) -> FrequencyDescriptor: ...

    def score(self, term: str, lang: str = "en") -> FrequencyScore | None:
        """Frequency for a term, or ``None`` when the dataset has no entry."""


class TableFrequencyProvider:
    """Reads an exported table of Zipf scores.

    The production path: ``tools/export_frequency.py`` runs once with
    ``wordfreq`` installed and writes this table. The engine never imports
    ``wordfreq`` itself.
    """

    def __init__(
        self,
        scores: dict[str, float],
        *,
        name: str,
        version: str,
        retrieved_at: dt.datetime,
        lang: str = "en",
        bands: tuple[tuple[FrequencyBand, float], ...] = DEFAULT_BANDS,
    ) -> None:
        self._scores = {term.lower(): value for term, value in scores.items()}
        self._descriptor = FrequencyDescriptor(name, version, retrieved_at)
        self._lang = lang
        self._bands = bands

    @classmethod
    def from_file(cls, path: str | Path) -> "TableFrequencyProvider":
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            scores={k: float(v) for k, v in document["scores"].items()},
            name=document.get("name", "wordfreq"),
            version=str(document.get("version", "unversioned")),
            retrieved_at=dt.datetime.fromisoformat(document["retrieved_at"]),
            lang=document.get("lang", "en"),
        )

    def describe(self) -> FrequencyDescriptor:
        return self._descriptor

    def score(self, term: str, lang: str = "en") -> FrequencyScore | None:
        if lang != self._lang:
            return None
        value = self._scores.get(term.lower())
        if value is None:
            # Multi-word terms are scored by their rarest component: a phrase
            # is at least as hard to recognise as its hardest word.
            parts = [p for p in term.lower().split() if p]
            component_scores = [self._scores[p] for p in parts if p in self._scores]
            if len(component_scores) != len(parts) or not parts:
                return None
            value = min(component_scores)
        return FrequencyScore(
            term=term,
            lang=lang,
            zipf=value,
            band=band_for_zipf(value, self._bands),
        )


class NullFrequencyProvider:
    """Supplies no scores. Used where frequency is genuinely irrelevant.

    Explicit rather than passing ``None`` around, so a snapshot built without
    frequency data says so in its metadata instead of silently omitting it.
    """

    def __init__(self, now: dt.datetime | None = None) -> None:
        self._descriptor = FrequencyDescriptor(
            name="none",
            version="0",
            retrieved_at=now or dt.datetime.now(dt.timezone.utc),
        )

    def describe(self) -> FrequencyDescriptor:
        return self._descriptor

    def score(self, term: str, lang: str = "en") -> FrequencyScore | None:
        return None
