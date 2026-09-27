"""The boundary between external knowledge and the internal graph.

A provider speaks in its own vocabulary and its own identifier space. It emits
``Raw*`` records keyed by provider-native strings, and the normaliser converts
those into internal records with minted ids. Nothing downstream of the
normaliser ever sees a provider key except inside provenance, which is exactly
what makes a provider replaceable.

Import, not live query. The protocol is built around iterating a provider's
content into a snapshot rather than querying it during generation or play,
because a puzzle that consulted a live source could not be regenerated once
that source changed. ``fetch_entity`` exists for incremental refresh of a
single record during a re-verification sweep, and is never called from the
generation path.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..core.types import (
    Confidence,
    FreshnessClass,
    ProvenanceClass,
    SourceKind,
)

#: A provider-native key. Opaque to everything except the provider that
#: produced it and the normaliser that maps it to an internal id.
ProviderKey = str


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    """Identity of a provider release, recorded on every fact it supplies."""

    name: str
    kind: SourceKind
    version: str
    retrieved_at: dt.datetime
    url: str | None = None
    deprecated: bool = False
    #: Set when the provider knows its own data is no longer maintained
    #: upstream, so the curator sees the problem before a puzzle does.
    upstream_last_updated: dt.date | None = None

    def __post_init__(self) -> None:
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ProviderFreshness:
    """A provider's own assessment of whether it is still trustworthy."""

    healthy: bool
    checked_at: dt.datetime
    detail: str = ""
    #: Days since the upstream dataset last changed, where knowable.
    age_days: int | None = None


@dataclass(frozen=True, slots=True)
class RawCategory:
    key: ProviderKey
    name: str
    parent_keys: tuple[ProviderKey, ...] = ()
    gloss: str | None = None
    lang: str = "en"
    #: ``(min_depth, depth)`` where the provider already maintains an acyclic
    #: hierarchy and has computed them. WordNet does; a hand-authored file
    #: does not, and leaves this ``None`` so the normaliser derives them.
    depth_hint: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class RawEntity:
    key: ProviderKey
    name: str
    aliases: tuple[str, ...] = ()
    definition: str | None = None
    lang: str = "en"
    #: Categories this entity belongs to, emitted as membership rather than as
    #: a field on the entity, so each membership carries its own provenance.
    category_keys: tuple[ProviderKey, ...] = ()
    confidence: Confidence = 0.9


@dataclass(frozen=True, slots=True)
class RawFact:
    subject_key: ProviderKey
    predicate: str
    value: Any
    unit: str | None = None
    lang: str = "en"
    freshness_class: FreshnessClass = FreshnessClass.STATIC
    provenance_class: ProvenanceClass = ProvenanceClass.SOURCED
    confidence: Confidence = 0.9
    assertion_date: dt.date | None = None
    reviewer: str | None = None
    #: Provider keys this assertion was derived from. Required when
    #: ``provenance_class`` is COMPUTED.
    derived_from_keys: tuple[ProviderKey, ...] = ()


@dataclass(frozen=True, slots=True)
class RawRelationship:
    subject_key: ProviderKey
    predicate: str
    object_key: ProviderKey
    symmetric: bool = False
    freshness_class: FreshnessClass = FreshnessClass.SLOW_CHANGING
    provenance_class: ProvenanceClass = ProvenanceClass.SOURCED
    confidence: Confidence = 0.9
    reviewer: str | None = None
    derived_from_keys: tuple[ProviderKey, ...] = ()
    #: True when the object key names a category rather than an entity. The
    #: normaliser cannot infer this, because both are opaque strings.
    object_is_category: bool = False


@dataclass(frozen=True, slots=True)
class ProviderBundle:
    """Everything one provider contributes to a snapshot."""

    descriptor: ProviderDescriptor
    categories: tuple[RawCategory, ...] = ()
    entities: tuple[RawEntity, ...] = ()
    facts: tuple[RawFact, ...] = ()
    relationships: tuple[RawRelationship, ...] = ()
    warnings: tuple[str, ...] = field(default=())

    def counts(self) -> dict[str, int]:
        return {
            "categories": len(self.categories),
            "entities": len(self.entities),
            "facts": len(self.facts),
            "relationships": len(self.relationships),
        }


@runtime_checkable
class ContentProvider(Protocol):
    """A source of raw knowledge, normalised by the content service."""

    def describe(self) -> ProviderDescriptor:
        """Identity and version of this provider release."""

    def freshness_status(self) -> ProviderFreshness:
        """Whether this provider is still considered trustworthy."""

    def load(self) -> ProviderBundle:
        """Every record this provider contributes, for snapshot import."""

    def fetch_entity(self, key: ProviderKey) -> RawEntity | None:
        """One entity by provider key, for re-verification sweeps only.

        Never called during puzzle generation. A generation path that reached
        a live provider would break snapshot reproducibility.
        """


class ProviderError(Exception):
    """A provider could not supply its content."""


def validate_bundle(bundle: ProviderBundle) -> list[str]:
    """Structural problems in a bundle, as human-readable strings.

    Returned rather than raised so the snapshot builder can report every
    problem in one pass instead of failing on the first one, which matters
    when a curator is fixing a hand-authored seed file.
    """
    problems: list[str] = []

    category_keys = {c.key for c in bundle.categories}
    entity_keys = {e.key for e in bundle.entities}

    if len(category_keys) != len(bundle.categories):
        problems.append("duplicate category keys")
    if len(entity_keys) != len(bundle.entities):
        problems.append("duplicate entity keys")

    for category in bundle.categories:
        for parent in category.parent_keys:
            if parent not in category_keys:
                problems.append(
                    f"category {category.key!r} names unknown parent {parent!r}"
                )
        if category.key in category.parent_keys:
            problems.append(f"category {category.key!r} is its own parent")

    for entity in bundle.entities:
        for key in entity.category_keys:
            if key not in category_keys:
                problems.append(
                    f"entity {entity.key!r} names unknown category {key!r}"
                )

    for fact in bundle.facts:
        if fact.subject_key not in entity_keys:
            problems.append(
                f"fact {fact.predicate!r} names unknown subject {fact.subject_key!r}"
            )
        if fact.provenance_class is ProvenanceClass.COMPUTED and not fact.derived_from_keys:
            problems.append(
                f"computed fact {fact.predicate!r} on {fact.subject_key!r} "
                "records no derivation"
            )
        if fact.provenance_class is ProvenanceClass.JUDGED and not fact.reviewer:
            problems.append(
                f"judged fact {fact.predicate!r} on {fact.subject_key!r} "
                "names no reviewer"
            )

    for rel in bundle.relationships:
        if rel.subject_key not in entity_keys | category_keys:
            problems.append(f"relationship names unknown subject {rel.subject_key!r}")
        target = category_keys if rel.object_is_category else entity_keys
        if rel.object_key not in target:
            problems.append(f"relationship names unknown object {rel.object_key!r}")
        if rel.subject_key == rel.object_key:
            problems.append(f"relationship loops on {rel.subject_key!r}")

    return problems


def order_categories(categories: Sequence[RawCategory]) -> list[RawCategory]:
    """Parents before children, so every write finds its parents present.

    A topological sort, and simultaneously the import-time cycle check: any
    category left unplaced after no further progress is possible is part of a
    cycle, which is reported rather than silently dropped.
    """
    remaining = {c.key: c for c in categories}
    placed: set[ProviderKey] = set()
    ordered: list[RawCategory] = []

    while remaining:
        ready = sorted(
            (c for c in remaining.values() if set(c.parent_keys) <= placed),
            key=lambda c: c.key,
        )
        if not ready:
            raise ProviderError(
                "cycle or missing parent among categories: "
                f"{sorted(remaining)}"
            )
        for category in ready:
            ordered.append(category)
            placed.add(category.key)
            del remaining[category.key]

    return ordered


def iter_provider_keys(bundle: ProviderBundle) -> Iterable[ProviderKey]:
    """Every provider key the bundle defines. Used for collision detection."""
    yield from (c.key for c in bundle.categories)
    yield from (e.key for e in bundle.entities)
