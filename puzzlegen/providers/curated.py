"""A provider over a hand-authored JSON file.

This is the provider that makes the system usable without any external
dataset: it is how a curator adds facts nothing else supplies, and how the
reference games get content that is deliberately shaped for puzzles.

The file format is plain and reviewable in a pull request. Everything it can
express is exactly what the provider protocol can carry, so a curated file and
a WordNet import are indistinguishable to every layer above.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from ..core.types import FreshnessClass, ProvenanceClass, SourceKind
from .base import (
    ProviderBundle,
    ProviderDescriptor,
    ProviderError,
    ProviderFreshness,
    ProviderKey,
    RawCategory,
    RawEntity,
    RawFact,
    RawRelationship,
)

SCHEMA_KEY = "curated_schema"
SUPPORTED_SCHEMA = 1


class CuratedJSONProvider:
    """Reads one curated content document."""

    def __init__(
        self,
        path: str | Path,
        *,
        name: str = "curated",
        now: dt.datetime | None = None,
    ) -> None:
        self._path = Path(path)
        self._name = name
        self._now = now or dt.datetime.now(dt.timezone.utc)
        self._document: dict[str, Any] | None = None

    # -- loading ----------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        if self._document is not None:
            return self._document
        if not self._path.exists():
            raise ProviderError(f"curated content file not found: {self._path}")
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProviderError(f"curated content file is not valid JSON: {exc}") from exc
        if not isinstance(document, dict):
            raise ProviderError("curated content file must contain a JSON object")

        schema = document.get(SCHEMA_KEY, SUPPORTED_SCHEMA)
        if schema != SUPPORTED_SCHEMA:
            raise ProviderError(
                f"curated file uses schema v{schema}, expected v{SUPPORTED_SCHEMA}"
            )
        self._document = document
        return document

    # -- protocol ---------------------------------------------------------

    def describe(self) -> ProviderDescriptor:
        document = self._read()
        return ProviderDescriptor(
            name=self._name,
            kind=SourceKind.CURATED_INTERNAL,
            version=str(document.get("version", "unversioned")),
            retrieved_at=self._now,
            url=document.get("url"),
            upstream_last_updated=_maybe_date(document.get("updated")),
        )

    def freshness_status(self) -> ProviderFreshness:
        """A curated file is as fresh as its declared update date.

        Reported rather than assumed healthy: a seed file nobody has touched
        in two years is a real risk, and the curator dashboard should say so.
        """
        document = self._read()
        updated = _maybe_date(document.get("updated"))
        if updated is None:
            return ProviderFreshness(
                healthy=True,
                checked_at=self._now,
                detail="curated file declares no update date",
            )
        age = (self._now.date() - updated).days
        return ProviderFreshness(
            healthy=age <= 365,
            checked_at=self._now,
            age_days=age,
            detail=f"curated file last updated {updated.isoformat()}",
        )

    def load(self) -> ProviderBundle:
        document = self._read()
        return ProviderBundle(
            descriptor=self.describe(),
            categories=tuple(_category(row) for row in document.get("categories", [])),
            entities=tuple(_entity(row) for row in document.get("entities", [])),
            facts=tuple(_fact(row) for row in document.get("facts", [])),
            relationships=tuple(
                _relationship(row) for row in document.get("relationships", [])
            ),
            warnings=tuple(document.get("warnings", [])),
        )

    def fetch_entity(self, key: ProviderKey) -> RawEntity | None:
        for row in self._read().get("entities", []):
            if row.get("key") == key:
                return _entity(row)
        return None


def _maybe_date(value: Any) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError as exc:
        raise ProviderError(f"not an ISO date: {value!r}") from exc


def _require(row: dict[str, Any], field: str) -> Any:
    if field not in row:
        raise ProviderError(f"curated row is missing required field {field!r}: {row}")
    return row[field]


def _category(row: dict[str, Any]) -> RawCategory:
    depth_hint = row.get("depth_hint")
    return RawCategory(
        key=_require(row, "key"),
        name=_require(row, "name"),
        parent_keys=tuple(row.get("parents", ())),
        gloss=row.get("gloss"),
        lang=row.get("lang", "en"),
        depth_hint=tuple(depth_hint) if depth_hint else None,  # type: ignore[arg-type]
    )


def _entity(row: dict[str, Any]) -> RawEntity:
    return RawEntity(
        key=_require(row, "key"),
        name=_require(row, "name"),
        aliases=tuple(row.get("aliases", ())),
        definition=row.get("definition"),
        lang=row.get("lang", "en"),
        category_keys=tuple(row.get("categories", ())),
        confidence=float(row.get("confidence", 0.9)),
    )


def _fact(row: dict[str, Any]) -> RawFact:
    return RawFact(
        subject_key=_require(row, "subject"),
        predicate=_require(row, "predicate"),
        value=_require(row, "value"),
        unit=row.get("unit"),
        lang=row.get("lang", "en"),
        freshness_class=FreshnessClass(row.get("freshness", "STATIC")),
        provenance_class=ProvenanceClass(row.get("provenance", "SOURCED")),
        confidence=float(row.get("confidence", 0.9)),
        assertion_date=_maybe_date(row.get("asserted")),
        reviewer=row.get("reviewer"),
        derived_from_keys=tuple(row.get("derived_from", ())),
    )


def _relationship(row: dict[str, Any]) -> RawRelationship:
    return RawRelationship(
        subject_key=_require(row, "subject"),
        predicate=_require(row, "predicate"),
        object_key=_require(row, "object"),
        symmetric=bool(row.get("symmetric", False)),
        freshness_class=FreshnessClass(row.get("freshness", "SLOW_CHANGING")),
        provenance_class=ProvenanceClass(row.get("provenance", "SOURCED")),
        confidence=float(row.get("confidence", 0.9)),
        reviewer=row.get("reviewer"),
        derived_from_keys=tuple(row.get("derived_from", ())),
        object_is_category=bool(row.get("object_is_category", False)),
    )
