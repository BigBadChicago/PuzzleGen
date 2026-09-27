"""Fixtures shared by every test module.

The store fixture is parametrized over both backends. Every storage test
therefore runs twice, which is the mechanism that keeps the in-memory backend
from drifting away from SQLite.
"""

from __future__ import annotations

import datetime as dt

import pytest

from puzzlegen.core import ids
from puzzlegen.core.types import (
    FreshnessClass,
    FrequencyBand,
    ProvenanceClass,
    ReviewStatus,
    SourceKind,
)
from puzzlegen.graph.memory_store import InMemoryDocumentStore
from puzzlegen.graph.models import (
    Category,
    Entity,
    Fact,
    FactValue,
    Provenance,
    Relationship,
    Source,
)
from puzzlegen.graph.repositories import GraphRepositories
from puzzlegen.graph.sqlite_store import SqliteDocumentStore

NOW = dt.datetime(2026, 9, 26, 12, 0, 0, tzinfo=dt.timezone.utc)
LATER = NOW + dt.timedelta(days=365)


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        backend = InMemoryDocumentStore()
    else:
        backend = SqliteDocumentStore(tmp_path / "graph.sqlite3")
    yield backend
    backend.close()


@pytest.fixture
def repos(store) -> GraphRepositories:
    return GraphRepositories(store)


@pytest.fixture
def curated_source() -> Source:
    return Source(
        id=ids.for_source("curated", "2026.09"),
        name="curated",
        kind=SourceKind.CURATED_INTERNAL,
        version="2026.09",
        retrieved_at=NOW,
    )


def sourced_provenance(source: Source, confidence: float = 0.95) -> Provenance:
    return Provenance(
        source_id=source.id,
        provenance_class=ProvenanceClass.SOURCED,
        source_ref="curated/tiger",
        retrieval_date=NOW,
        verification_date=NOW,
        verification_method="curator import",
        confidence=confidence,
    )


def make_entity(
    name: str,
    source: Source,
    *,
    status: ReviewStatus = ReviewStatus.ACTIVE,
    band: FrequencyBand = FrequencyBand.COMMON,
    aliases: tuple[str, ...] = (),
    freshness: FreshnessClass = FreshnessClass.STATIC,
) -> Entity:
    return Entity(
        id=ids.for_entity(name, "en"),
        canonical_name=name,
        aliases=aliases,
        status=status,
        freshness_class=freshness,
        frequency_band=band,
        confidence=0.95,
        provenance=(sourced_provenance(source),),
        created_at=NOW,
        updated_at=NOW,
        verified_at=NOW,
        next_review_at=LATER,
    )


def make_category(
    name: str,
    source: Source,
    *,
    parent: Category | None = None,
    parents: tuple[Category, ...] = (),
) -> Category:
    chosen = parents or ((parent,) if parent else ())
    return Category.build(
        canonical_name=name,
        parents=chosen,
        created_at=NOW,
        status=ReviewStatus.ACTIVE,
        confidence=0.95,
        provenance=(sourced_provenance(source),),
    )


def make_fact(
    entity: Entity,
    predicate: str,
    value,
    source: Source,
    *,
    freshness: FreshnessClass = FreshnessClass.STATIC,
    status: ReviewStatus = ReviewStatus.ACTIVE,
) -> Fact:
    non_static = freshness is not FreshnessClass.STATIC
    return Fact.build(
        subject_id=entity.id,
        predicate=predicate,
        value=FactValue.of(value),
        created_at=NOW,
        status=status,
        freshness_class=freshness,
        confidence=0.9,
        provenance=(sourced_provenance(source),),
        verified_at=NOW if non_static else None,
        next_review_at=LATER if non_static else None,
    )


def make_relationship(
    subject: Entity,
    predicate: str,
    obj,
    source: Source,
) -> Relationship:
    return Relationship.build(
        subject_id=subject.id,
        predicate=predicate,
        object_id=obj.id,
        created_at=NOW,
        status=ReviewStatus.ACTIVE,
        confidence=0.9,
        provenance=(sourced_provenance(source),),
    )
