"""The internal representation of the shared knowledge graph.

Design decision, and the most consequential one in this module: the graph is
normalised. An :class:`Entity` carries identity and lifecycle only. Every
assertion about an entity is a :class:`Fact` (entity to literal) or a
:class:`Relationship` (entity to entity), each with its own provenance,
freshness class and review status.

The conceptual example in the design brief shows types and properties inline
on the entity. That shape is produced on read by the content service as an
``EntityView``; it is not the storage shape. Storing it inline would give one
provenance record for a bundle of assertions that are sourced differently and
go stale at different rates, which makes per-fact freshness and per-fact blast
radius impossible.

Every model is frozen. Records are replaced, never mutated, so a stored
document always corresponds to exactly the bytes that were hashed.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..core import ids
from ..core.types import (
    Confidence,
    DependencyRefKind,
    FreshnessClass,
    FrequencyBand,
    ProvenanceClass,
    ReviewStatus,
    SourceKind,
    ValueKind,
)
from ..core.versions import CONTENT_SCHEMA_VERSION

Lang = Annotated[str, Field(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")]
Predicate = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


def _utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware UTC")
    return value.astimezone(dt.timezone.utc)


class Source(_Frozen):
    """An origin of assertions, versioned and independently retirable."""

    id: str
    name: str = Field(min_length=1, max_length=120)
    kind: SourceKind
    version: str = Field(min_length=1, max_length=64)
    retrieved_at: dt.datetime
    url: str | None = None
    #: Set when a source is abandoned upstream. Facts whose only provenance is
    #: a deprecated source are rejected by the generation gates rather than
    #: deleted, so the curator can see what would break before it does.
    deprecated: bool = False
    replacement_source_id: str | None = None
    schema_version: int = CONTENT_SCHEMA_VERSION

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.id, ids.SOURCE)
        if self.replacement_source_id is not None:
            ids.require(self.replacement_source_id, ids.SOURCE)
            if not self.deprecated:
                raise ValueError("replacement_source_id requires deprecated=True")
        object.__setattr__(self, "retrieved_at", _utc(self.retrieved_at))
        return self


class Provenance(_Frozen):
    """Why one assertion is believed, from one source."""

    source_id: str
    provenance_class: ProvenanceClass
    #: The provider's own identifier, recorded for traceability and re-fetch.
    #: Never used as a primary key anywhere in the system.
    source_ref: str | None = None
    source_url: str | None = None
    retrieval_date: dt.datetime
    #: When the source itself claims the assertion became true, where known.
    assertion_date: dt.date | None = None
    verification_date: dt.datetime | None = None
    verification_method: str | None = None
    confidence: Confidence = 0.5
    reviewer: str | None = None
    #: For COMPUTED provenance: the records this assertion was derived from.
    #: Retraction of any input quarantines the derived assertion.
    derived_from: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.source_id, ids.SOURCE)
        object.__setattr__(self, "retrieval_date", _utc(self.retrieval_date))
        if self.verification_date is not None:
            object.__setattr__(self, "verification_date", _utc(self.verification_date))
        if self.provenance_class is ProvenanceClass.COMPUTED and not self.derived_from:
            raise ValueError("COMPUTED provenance must record derived_from")
        if self.provenance_class is ProvenanceClass.JUDGED and self.reviewer is None:
            raise ValueError("JUDGED provenance must name a reviewer")
        return self


class _Governed(_Frozen):
    """Fields and lifecycle rules shared by every governed graph record."""

    id: str
    status: ReviewStatus = ReviewStatus.CANDIDATE
    freshness_class: FreshnessClass = FreshnessClass.STATIC
    confidence: Confidence = 0.5
    provenance: tuple[Provenance, ...] = ()
    created_at: dt.datetime
    updated_at: dt.datetime
    verified_at: dt.datetime | None = None
    next_review_at: dt.datetime | None = None
    #: Free-form curator note, surfaced in the review queue.
    note: str | None = None
    schema_version: int = CONTENT_SCHEMA_VERSION

    @model_validator(mode="after")
    def _check_governance(self) -> Self:
        for field in ("created_at", "updated_at", "verified_at", "next_review_at"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _utc(value))

        if self.status is ReviewStatus.ACTIVE:
            if not self.provenance:
                raise ValueError("an ACTIVE record must carry provenance")
            if self.freshness_class is not FreshnessClass.STATIC:
                if self.verified_at is None or self.next_review_at is None:
                    raise ValueError(
                        "an ACTIVE non-static record must have verified_at "
                        "and next_review_at"
                    )
            if any(
                p.provenance_class is ProvenanceClass.JUDGED for p in self.provenance
            ) and not any(p.reviewer for p in self.provenance):
                raise ValueError("a JUDGED record cannot be ACTIVE without a reviewer")

        if self.provenance:
            ceiling = max(p.confidence for p in self.provenance)
            if self.confidence > ceiling + 1e-9:
                raise ValueError(
                    "record confidence cannot exceed its best provenance confidence"
                )
        return self

    def is_usable(self) -> bool:
        return self.status is ReviewStatus.ACTIVE

    def is_due_for_review(self, now: dt.datetime) -> bool:
        return self.next_review_at is not None and _utc(now) >= self.next_review_at

    def provenance_classes(self) -> frozenset[ProvenanceClass]:
        return frozenset(p.provenance_class for p in self.provenance)


class Category(_Governed):
    """A node in a taxonomy.

    A category may have several parents, because real taxonomies fork: WordNet
    routinely places one synset under two hypernyms. Two costs normally come
    with that, and both are designed out rather than paid:

    * Cycles. ``parent_ids`` is fixed at construction and a category may only
      name parents that already exist, enforced on write by the repository.
      A cycle would require a category to predate its own ancestor, so cycles
      are structurally impossible and no traversal-based checker is needed.
    * Ambiguous depth. ``depth`` remains a single integer, the longest path to
      a root, computed once from parents that are already final. ``min_depth``
      records the shortest path alongside it for games that care about forked
      ancestry; nothing else reads it, so every existing depth-based query is
      unchanged.

    A category that later needs another parent is retired and replaced rather
    than edited, mirroring how a deprecated :class:`Source` names its
    successor.
    """

    record: Literal["category"] = "category"
    canonical_name: str = Field(min_length=1, max_length=160)
    lang: Lang = "en"
    taxonomy: str = Field(default="default", pattern=r"^[a-z][a-z0-9_]{0,31}$")
    parent_ids: tuple[str, ...] = ()
    #: Longest distance to a root. The single depth value the rest of the
    #: system reads, denormalised because sibling queries read it constantly.
    depth: int = Field(ge=0, le=64)
    #: Shortest distance to a root. Equal to ``depth`` in a single-parent
    #: taxonomy; differs only where ancestry forks.
    min_depth: int = Field(ge=0, le=64)
    gloss: str | None = None
    retired: bool = False
    replacement_category_id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.id, ids.CATEGORY)
        if len(set(self.parent_ids)) != len(self.parent_ids):
            raise ValueError("parent_ids must be distinct")
        for parent_id in self.parent_ids:
            ids.require(parent_id, ids.CATEGORY)
            if parent_id == self.id:
                raise ValueError("a category cannot be its own parent")
        if not self.parent_ids:
            if self.depth != 0 or self.min_depth != 0:
                raise ValueError("a category with no parents must have depth 0")
        else:
            if self.depth < 1 or self.min_depth < 1:
                raise ValueError("a category with parents must have depth >= 1")
            if self.min_depth > self.depth:
                raise ValueError("min_depth cannot exceed depth")
        if self.replacement_category_id is not None:
            ids.require(self.replacement_category_id, ids.CATEGORY)
            if not self.retired:
                raise ValueError("replacement_category_id requires retired=True")
        return self

    @property
    def is_root(self) -> bool:
        return not self.parent_ids

    @property
    def has_forked_ancestry(self) -> bool:
        return len(self.parent_ids) > 1 or self.min_depth != self.depth

    @classmethod
    def build(
        cls,
        *,
        canonical_name: str,
        parents: Sequence["Category"] = (),
        created_at: dt.datetime,
        lang: str = "en",
        taxonomy: str = "default",
        depth_override: tuple[int, int] | None = None,
        **kwargs: Any,
    ) -> "Category":
        """Construct a category, deriving depth from already-final parents.

        ``depth_override`` exists for providers that already publish correct
        depths, notably WordNet, whose own hypernym graph is maintained
        acyclic upstream. Copying two integers the provider already computed
        is cheaper and more faithful than recomputing them here.
        """
        for parent in parents:
            if parent.taxonomy != taxonomy:
                raise ValueError("a category's parents must share its taxonomy")
            if parent.retired:
                raise ValueError("a retired category cannot be a parent")

        if depth_override is not None:
            min_depth, depth = depth_override
        elif parents:
            depth = 1 + max(p.depth for p in parents)
            min_depth = 1 + min(p.min_depth for p in parents)
        else:
            depth = min_depth = 0

        return cls(
            id=kwargs.pop("id", ids.for_category(canonical_name, lang)),
            canonical_name=canonical_name,
            lang=lang,
            taxonomy=taxonomy,
            parent_ids=tuple(p.id for p in parents),
            depth=depth,
            min_depth=min_depth,
            created_at=created_at,
            updated_at=kwargs.pop("updated_at", created_at),
            **kwargs,
        )


class Entity(_Governed):
    """A thing the graph knows about. Identity and lifecycle only."""

    record: Literal["entity"] = "entity"
    canonical_name: str = Field(min_length=1, max_length=160)
    lang: Lang = "en"
    aliases: tuple[str, ...] = ()
    #: Short human-readable gloss. Used by vocabulary games as clue material
    #: and by the review queue so a reviewer knows what they are looking at.
    definition: str | None = None
    #: Coarse banding of the canonical name's corpus frequency, copied from
    #: the frequency provider at snapshot build time. The raw score and its
    #: source live in :class:`FrequencyRecord`; only the band is read by gates.
    frequency_band: FrequencyBand | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.id, ids.ENTITY)
        if len(set(self.aliases)) != len(self.aliases):
            raise ValueError("aliases must be distinct")
        if self.canonical_name in self.aliases:
            raise ValueError("canonical_name must not repeat in aliases")
        return self

    def names(self) -> tuple[str, ...]:
        return (self.canonical_name, *self.aliases)


class FactValue(_Frozen):
    """A typed literal. Discriminated so canonical JSON is unambiguous."""

    kind: ValueKind
    text: str | None = None
    integer: int | None = None
    number: float | None = None
    boolean: bool | None = None
    date: dt.date | None = None
    unit: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        slots = {
            ValueKind.STRING: self.text,
            ValueKind.INTEGER: self.integer,
            ValueKind.NUMBER: self.number,
            ValueKind.BOOLEAN: self.boolean,
            ValueKind.DATE: self.date,
        }
        populated = [k for k, v in slots.items() if v is not None]
        if populated != [self.kind]:
            raise ValueError(
                f"FactValue of kind {self.kind} must populate exactly its own slot"
            )
        if self.unit is not None and self.kind not in (
            ValueKind.INTEGER,
            ValueKind.NUMBER,
        ):
            raise ValueError("unit is only meaningful on numeric values")
        return self

    def as_python(self) -> Any:
        return {
            ValueKind.STRING: self.text,
            ValueKind.INTEGER: self.integer,
            ValueKind.NUMBER: self.number,
            ValueKind.BOOLEAN: self.boolean,
            ValueKind.DATE: self.date,
        }[self.kind]

    def repr_key(self) -> str:
        """Stable text form, used as part of the fact's natural key."""
        value = self.as_python()
        text = value.isoformat() if isinstance(value, dt.date) else str(value)
        return f"{self.kind}:{text}" + (f":{self.unit}" if self.unit else "")

    @classmethod
    def of(cls, value: Any, unit: str | None = None) -> "FactValue":
        if isinstance(value, bool):
            return cls(kind=ValueKind.BOOLEAN, boolean=value)
        if isinstance(value, int):
            return cls(kind=ValueKind.INTEGER, integer=value, unit=unit)
        if isinstance(value, float):
            return cls(kind=ValueKind.NUMBER, number=value, unit=unit)
        if isinstance(value, dt.date):
            return cls(kind=ValueKind.DATE, date=value)
        if isinstance(value, str):
            return cls(kind=ValueKind.STRING, text=value)
        raise TypeError(f"unsupported fact value type: {type(value).__name__}")


class Fact(_Governed):
    """An assertion attaching a literal value to an entity."""

    record: Literal["fact"] = "fact"
    subject_id: str
    predicate: Predicate
    value: FactValue
    lang: Lang = "en"

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.id, ids.FACT)
        ids.require(self.subject_id, ids.ENTITY)
        expected = ids.for_fact(self.subject_id, self.predicate, self.value.repr_key())
        if self.id != expected:
            raise ValueError(
                "fact id must be derived from subject, predicate and value"
            )
        return self

    @classmethod
    def build(
        cls,
        *,
        subject_id: str,
        predicate: str,
        value: FactValue,
        created_at: dt.datetime,
        **kwargs: Any,
    ) -> "Fact":
        return cls(
            id=ids.for_fact(subject_id, predicate, value.repr_key()),
            subject_id=subject_id,
            predicate=predicate,
            value=value,
            created_at=created_at,
            updated_at=kwargs.pop("updated_at", created_at),
            **kwargs,
        )


class Relationship(_Governed):
    """An assertion connecting two entities, or an entity to a category."""

    record: Literal["relationship"] = "relationship"
    subject_id: str
    predicate: Predicate
    object_id: str
    #: When true the inverse holds with the same predicate. Recorded rather
    #: than inferred so that neighbour queries need no predicate registry.
    symmetric: bool = False

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.id, ids.RELATIONSHIP)
        if ids.kind_of(self.subject_id) not in (ids.ENTITY, ids.CATEGORY):
            raise ValueError("relationship subject must be an entity or category")
        if ids.kind_of(self.object_id) not in (ids.ENTITY, ids.CATEGORY):
            raise ValueError("relationship object must be an entity or category")
        if self.subject_id == self.object_id:
            raise ValueError("a relationship must connect two distinct records")
        expected = ids.for_relationship(self.subject_id, self.predicate, self.object_id)
        if self.id != expected:
            raise ValueError(
                "relationship id must be derived from subject, predicate and object"
            )
        return self

    @classmethod
    def build(
        cls,
        *,
        subject_id: str,
        predicate: str,
        object_id: str,
        created_at: dt.datetime,
        **kwargs: Any,
    ) -> "Relationship":
        return cls(
            id=ids.for_relationship(subject_id, predicate, object_id),
            subject_id=subject_id,
            predicate=predicate,
            object_id=object_id,
            created_at=created_at,
            updated_at=kwargs.pop("updated_at", created_at),
            **kwargs,
        )


class FrequencyRecord(_Frozen):
    """A corpus frequency score with the provenance of the score itself.

    Separate from the entity because the frequency dataset has its own version
    and retrieval date, and replacing the dataset must not rewrite entities.
    """

    entity_id: str
    lang: Lang = "en"
    #: Zipf-style score where available, typically on [0, 8].
    zipf: float = Field(ge=0.0, le=10.0)
    band: FrequencyBand
    source_name: str
    source_version: str
    retrieved_at: dt.datetime
    schema_version: int = CONTENT_SCHEMA_VERSION

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.entity_id, ids.ENTITY)
        object.__setattr__(self, "retrieved_at", _utc(self.retrieved_at))
        return self


class EmbeddingRecord(_Frozen):
    """A precomputed semantic vector for an entity.

    Vectors are computed once at snapshot build time and frozen into the
    snapshot. The engine therefore has no model dependency at generation or
    play time, and a model upgrade is a snapshot rebuild rather than a silent
    change to puzzles that were already published.
    """

    entity_id: str
    model_name: str = Field(min_length=1, max_length=120)
    model_version: str = Field(min_length=1, max_length=64)
    dimensions: int = Field(ge=2, le=4096)
    #: Stored as a plain list so the record canonicalises and hashes without
    #: depending on numpy being installed in the engine environment.
    vector: tuple[float, ...]
    normalized: bool = True
    computed_at: dt.datetime
    schema_version: int = CONTENT_SCHEMA_VERSION

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.entity_id, ids.ENTITY)
        if len(self.vector) != self.dimensions:
            raise ValueError("vector length must equal declared dimensions")
        if any(v != v or v in (float("inf"), float("-inf")) for v in self.vector):
            raise ValueError("vector must be finite")
        object.__setattr__(self, "computed_at", _utc(self.computed_at))
        return self


class SnapshotMeta(_Frozen):
    """Identity of one immutable content snapshot."""

    id: str
    label: str = Field(min_length=1, max_length=64)
    created_at: dt.datetime
    schema_version: int = CONTENT_SCHEMA_VERSION
    #: source_id -> version, so a manifest records exactly which provider
    #: releases produced the content it was generated against.
    source_versions: dict[str, str] = Field(default_factory=dict)
    embedding_model: str | None = None
    embedding_model_version: str | None = None
    frequency_source: str | None = None
    frequency_source_version: str | None = None
    record_counts: dict[str, int] = Field(default_factory=dict)
    #: Hash over every record in the snapshot. A snapshot whose recomputed
    #: hash differs from this value is refused, because reproducibility claims
    #: made against it would be false.
    content_hash: str = ""
    sealed: bool = False

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.id, ids.SNAPSHOT)
        object.__setattr__(self, "created_at", _utc(self.created_at))
        if self.sealed and not self.content_hash:
            raise ValueError("a sealed snapshot must carry a content hash")
        return self


class DependencyRecord(_Frozen):
    """One edge from a published puzzle to a graph record it relies on.

    This is what makes blast radius answerable. Without an explicit edge per
    referenced record, invalidating a fact would require re-reading every
    manifest ever published.
    """

    id: str
    manifest_id: str
    puzzle_id: str
    game_id: str
    day_key: str
    ref_kind: DependencyRefKind
    ref_id: str
    created_at: dt.datetime
    schema_version: int = CONTENT_SCHEMA_VERSION

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.id, ids.DEPENDENCY)
        ids.require(self.manifest_id, ids.MANIFEST)
        ids.require(self.puzzle_id, ids.PUZZLE)
        expected_kind = {
            DependencyRefKind.ENTITY: ids.ENTITY,
            DependencyRefKind.FACT: ids.FACT,
            DependencyRefKind.RELATIONSHIP: ids.RELATIONSHIP,
            DependencyRefKind.CATEGORY: ids.CATEGORY,
        }[self.ref_kind]
        ids.require(self.ref_id, expected_kind)
        object.__setattr__(self, "created_at", _utc(self.created_at))
        return self

    @classmethod
    def build(
        cls,
        *,
        manifest_id: str,
        puzzle_id: str,
        game_id: str,
        day_key: str,
        ref_kind: DependencyRefKind,
        ref_id: str,
        created_at: dt.datetime,
    ) -> "DependencyRecord":
        return cls(
            id=ids.for_dependency(manifest_id, str(ref_kind), ref_id),
            manifest_id=manifest_id,
            puzzle_id=puzzle_id,
            game_id=game_id,
            day_key=day_key,
            ref_kind=ref_kind,
            ref_id=ref_id,
            created_at=created_at,
        )


#: Every governed record type, for generic lifecycle handling.
GovernedRecord = Category | Entity | Fact | Relationship
