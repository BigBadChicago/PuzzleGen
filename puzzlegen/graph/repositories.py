"""Typed repositories.

Each repository owns one collection, knows which fields are indexed, and is
the only place that converts between a pydantic model and a stored document.
Nothing above this module may hold a :class:`DocumentStore`; that restriction
is what lets the storage backend be replaced without touching the content
service, the engine or any game.

Four repositories named in the architecture (puzzles, manifests, sessions,
reviews) are declared here as protocols but implemented in later phases, when
their models exist. They are declared now so that storage is not reopened
later: the generic base below is already sufficient for all four.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator, Sequence
from typing import Any, ClassVar, Generic, Protocol, TypeVar

from pydantic import BaseModel

from ..core.errors import ConflictError, NotFoundError, SchemaVersionError
from ..core.types import DependencyRefKind, FrequencyBand, ReviewStatus
from ..core.versions import CONTENT_SCHEMA_VERSION
from .models import (
    Category,
    DependencyRecord,
    EmbeddingRecord,
    Entity,
    Fact,
    FrequencyRecord,
    Relationship,
    SnapshotMeta,
    Source,
)
from .store import DocumentStore, Query

T = TypeVar("T", bound=BaseModel)


class DocumentRepository(Generic[T]):
    """Model-aware wrapper over one document-store collection."""

    collection: ClassVar[str]
    model: ClassVar[type[BaseModel]]
    indexed_fields: ClassVar[tuple[str, ...]] = ()

    def __init__(self, store: DocumentStore) -> None:
        self._store = store
        self._store.declare_collection(self.collection, self.indexed_fields)

    # -- subclass hooks ---------------------------------------------------

    def _index_values(self, record: T) -> dict[str, list[str]]:
        """Indexable values for one record. Subclasses override."""
        return {}

    def _identity(self, record: T) -> str:
        return getattr(record, "id")

    # -- serialisation ----------------------------------------------------

    def _dump(self, record: T) -> dict[str, Any]:
        return record.model_dump(mode="json")

    def _load(self, document: dict[str, Any]) -> T:
        version = document.get("schema_version")
        if version is not None and version != CONTENT_SCHEMA_VERSION:
            raise SchemaVersionError(
                f"{self.collection} document written with content schema "
                f"v{version}, engine expects v{CONTENT_SCHEMA_VERSION}"
            )
        return self.model.model_validate(document)  # type: ignore[return-value]

    # -- writes -----------------------------------------------------------

    def put(self, record: T) -> None:
        self._store.put(
            self.collection,
            self._identity(record),
            self._dump(record),
            self._index_values(record),
        )

    def put_many(self, records: Sequence[T]) -> int:
        """Write a batch atomically. Returns the number written."""
        with self._store.transaction():
            for record in records:
                self.put(record)
        return len(records)

    def insert(self, record: T) -> None:
        """Write only if absent. Used where overwrite would be a bug."""
        identity = self._identity(record)
        if self._store.get(self.collection, identity) is not None:
            raise ConflictError(f"{self.collection}/{identity} already exists")
        self.put(record)

    def delete(self, record_id: str) -> bool:
        return self._store.delete(self.collection, record_id)

    # -- reads ------------------------------------------------------------

    def get(self, record_id: str) -> T | None:
        document = self._store.get(self.collection, record_id)
        return self._load(document) if document is not None else None

    def require(self, record_id: str) -> T:
        record = self.get(record_id)
        if record is None:
            raise NotFoundError(self.collection, record_id)
        return record

    def get_many(self, record_ids: Sequence[str]) -> dict[str, T]:
        found = self._store.get_many(self.collection, record_ids)
        return {k: self._load(v) for k, v in found.items()}

    def query(self, query: Query) -> list[T]:
        return [self._load(d) for d in self._store.query(self.collection, query)]

    def find(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        any_of: dict[str, Sequence[str]] | None = None,
        **equals: str,
    ) -> list[T]:
        """Convenience wrapper: keyword arguments become equality filters."""
        return self.query(
            Query(
                equals={k: str(v) for k, v in equals.items() if v is not None},
                any_of=any_of or {},
                limit=limit,
                offset=offset,
            )
        )

    def iter_all(self) -> Iterator[T]:
        for document in self._store.iter_all(self.collection):
            yield self._load(document)

    def count(self, query: Query | None = None) -> int:
        return self._store.count(self.collection, query)


class _GovernedRepository(DocumentRepository[T]):
    """Adds the lifecycle queries every governed collection needs."""

    def _governed_index(self, record: Any) -> dict[str, list[str]]:
        values: dict[str, list[str]] = {
            "status": [str(record.status)],
            "freshness_class": [str(record.freshness_class)],
        }
        if record.next_review_at is not None:
            # Date granularity is enough: review sweeps run daily, and a
            # coarser index key keeps the index table small.
            values["next_review_date"] = [record.next_review_at.date().isoformat()]
        return values

    def active(self, limit: int | None = None) -> list[T]:
        return self.find(status=str(ReviewStatus.ACTIVE), limit=limit)

    def by_status(self, status: ReviewStatus, limit: int | None = None) -> list[T]:
        return self.find(status=str(status), limit=limit)

    def due_for_review(self, as_of: dt.datetime, limit: int | None = None) -> list[T]:
        """Records whose next review date has arrived.

        Implemented as an any_of over the days up to ``as_of`` rather than a
        range query, because the store interface deliberately offers no range
        operator. Callers pass a bounded horizon; the curator sweep uses 400
        days, which covers anything that has slipped a full year.
        """
        horizon = 400
        as_of_date = as_of.date()
        days = [
            (as_of_date - dt.timedelta(days=offset)).isoformat()
            for offset in range(horizon)
        ]
        return self.query(
            Query(any_of={"next_review_date": days}, limit=limit)
        )


class SourceRepository(DocumentRepository[Source]):
    collection = "sources"
    model = Source
    indexed_fields = ("kind", "name", "deprecated")

    def _index_values(self, record: Source) -> dict[str, list[str]]:
        return {
            "kind": [str(record.kind)],
            "name": [record.name],
            "deprecated": [str(record.deprecated).lower()],
        }

    def deprecated(self) -> list[Source]:
        return self.find(deprecated="true")


class CategoryRepository(_GovernedRepository[Category]):
    collection = "categories"
    model = Category
    indexed_fields = (
        "status",
        "freshness_class",
        "next_review_date",
        "taxonomy",
        "parent_id",
        "depth",
        "min_depth",
        "canonical_name",
        "retired",
    )

    def _index_values(self, record: Category) -> dict[str, list[str]]:
        values = self._governed_index(record)
        values["taxonomy"] = [record.taxonomy]
        values["depth"] = [str(record.depth)]
        values["min_depth"] = [str(record.min_depth)]
        values["canonical_name"] = [record.canonical_name]
        values["retired"] = [str(record.retired).lower()]
        if record.parent_ids:
            values["parent_id"] = list(record.parent_ids)
        return values

    def put(self, record: Category) -> None:
        """Write a category, requiring every parent to already exist.

        This single existence check is the whole cycle-prevention mechanism.
        A cycle would need a category to be stored before one of its own
        ancestors, which this refuses, so no traversal-based checker is
        needed on any write path.
        """
        if record.parent_ids:
            present = self._store.get_many(self.collection, list(record.parent_ids))
            missing = sorted(set(record.parent_ids) - set(present))
            if missing:
                raise ConflictError(
                    f"category {record.id} names parents that do not exist yet: "
                    f"{missing}"
                )
        super().put(record)

    def children(self, parent_id: str) -> list[Category]:
        return self.find(parent_id=parent_id)

    def roots(self, taxonomy: str = "default") -> list[Category]:
        return self.find(taxonomy=taxonomy, depth="0")

    def at_depth(self, depth: int, taxonomy: str = "default") -> list[Category]:
        return self.find(taxonomy=taxonomy, depth=str(depth))

    def by_name(self, canonical_name: str) -> list[Category]:
        return self.find(canonical_name=canonical_name)

    def live(self, taxonomy: str = "default") -> list[Category]:
        """Categories that have not been retired and replaced."""
        return self.find(taxonomy=taxonomy, retired="false")

    def ancestors(self, category_id: str) -> list[Category]:
        """Every category above this one, shallowest first, de-duplicated.

        Walks the full parent set rather than a single chain. The ``seen`` set
        is not cycle protection, which the write guard already provides; it
        stops a shared ancestor reachable by two paths being returned twice.
        """
        start = self.get(category_id)
        if start is None:
            return []

        collected: dict[str, Category] = {}
        frontier = list(start.parent_ids)
        seen: set[str] = set(frontier)
        while frontier:
            batch = self.get_many(sorted(frontier))
            frontier = []
            for parent in batch.values():
                collected[parent.id] = parent
                for grandparent in parent.parent_ids:
                    if grandparent not in seen:
                        seen.add(grandparent)
                        frontier.append(grandparent)

        return sorted(collected.values(), key=lambda c: (c.depth, c.id))

    def shared_ancestors(self, category_ids: Sequence[str]) -> list[Category]:
        """Categories that sit above every one of the given categories.

        The basis of "are these genuinely siblings" checks: a group whose only
        shared ancestor is the taxonomy root is not a sibling group.
        """
        if not category_ids:
            return []
        sets = [
            {ancestor.id for ancestor in self.ancestors(cid)} for cid in category_ids
        ]
        common = set.intersection(*sets) if sets else set()
        found = self.get_many(sorted(common))
        return sorted(found.values(), key=lambda c: (c.depth, c.id))


class EntityRepository(_GovernedRepository[Entity]):
    collection = "entities"
    model = Entity
    indexed_fields = (
        "status",
        "freshness_class",
        "next_review_date",
        "lang",
        "name",
        "frequency_band",
    )

    def _index_values(self, record: Entity) -> dict[str, list[str]]:
        values = self._governed_index(record)
        values["lang"] = [record.lang]
        # Canonical name and aliases share one index so a lookup by any name
        # the entity answers to is a single query.
        values["name"] = [n.lower() for n in record.names()]
        if record.frequency_band is not None:
            values["frequency_band"] = [str(record.frequency_band)]
        return values

    def by_name(self, name: str, lang: str = "en") -> list[Entity]:
        return self.find(name=name.lower(), lang=lang)

    def by_frequency_band(
        self, band: FrequencyBand, *, active_only: bool = True, limit: int | None = None
    ) -> list[Entity]:
        filters: dict[str, str] = {"frequency_band": str(band)}
        if active_only:
            filters["status"] = str(ReviewStatus.ACTIVE)
        return self.find(limit=limit, **filters)


class FactRepository(_GovernedRepository[Fact]):
    collection = "facts"
    model = Fact
    indexed_fields = (
        "status",
        "freshness_class",
        "next_review_date",
        "subject_id",
        "predicate",
        "subject_predicate",
        "value_key",
    )

    def _index_values(self, record: Fact) -> dict[str, list[str]]:
        values = self._governed_index(record)
        values["subject_id"] = [record.subject_id]
        values["predicate"] = [record.predicate]
        # Composite key so "this entity's conservation status" is one indexed
        # lookup rather than a filter over everything known about the entity.
        values["subject_predicate"] = [f"{record.subject_id}|{record.predicate}"]
        values["value_key"] = [record.value.repr_key()]
        return values

    def by_subject(self, subject_id: str) -> list[Fact]:
        return self.find(subject_id=subject_id)

    def by_predicate(self, predicate: str, limit: int | None = None) -> list[Fact]:
        return self.find(predicate=predicate, limit=limit)

    def by_subject_predicate(self, subject_id: str, predicate: str) -> list[Fact]:
        return self.find(subject_predicate=f"{subject_id}|{predicate}")

    def subjects_with_value(self, predicate: str, value_key: str) -> list[Fact]:
        return self.find(predicate=predicate, value_key=value_key)


class RelationshipRepository(_GovernedRepository[Relationship]):
    collection = "relationships"
    model = Relationship
    indexed_fields = (
        "status",
        "freshness_class",
        "next_review_date",
        "subject_id",
        "object_id",
        "predicate",
        "subject_predicate",
        "object_predicate",
        "endpoint",
    )

    def _index_values(self, record: Relationship) -> dict[str, list[str]]:
        values = self._governed_index(record)
        values["subject_id"] = [record.subject_id]
        values["object_id"] = [record.object_id]
        values["predicate"] = [record.predicate]
        values["subject_predicate"] = [f"{record.subject_id}|{record.predicate}"]
        values["object_predicate"] = [f"{record.object_id}|{record.predicate}"]
        # Either endpoint, so an undirected neighbour query is one lookup.
        values["endpoint"] = [record.subject_id, record.object_id]
        return values

    def by_subject(self, subject_id: str, predicate: str | None = None) -> list[Relationship]:
        if predicate is None:
            return self.find(subject_id=subject_id)
        return self.find(subject_predicate=f"{subject_id}|{predicate}")

    def by_object(self, object_id: str, predicate: str | None = None) -> list[Relationship]:
        if predicate is None:
            return self.find(object_id=object_id)
        return self.find(object_predicate=f"{object_id}|{predicate}")

    def touching(self, record_id: str) -> list[Relationship]:
        return self.find(endpoint=record_id)


class FrequencyRepository(DocumentRepository[FrequencyRecord]):
    collection = "frequencies"
    model = FrequencyRecord
    indexed_fields = ("entity_id", "band", "lang", "source_name")

    def _identity(self, record: FrequencyRecord) -> str:
        # Keyed by entity, language and source so two frequency datasets can
        # coexist during a migration without overwriting each other.
        return f"{record.entity_id}|{record.lang}|{record.source_name}"

    def _index_values(self, record: FrequencyRecord) -> dict[str, list[str]]:
        return {
            "entity_id": [record.entity_id],
            "band": [str(record.band)],
            "lang": [record.lang],
            "source_name": [record.source_name],
        }

    def for_entity(self, entity_id: str, lang: str = "en") -> list[FrequencyRecord]:
        return self.find(entity_id=entity_id, lang=lang)


class EmbeddingRepository(DocumentRepository[EmbeddingRecord]):
    collection = "embeddings"
    model = EmbeddingRecord
    indexed_fields = ("entity_id", "model_name", "model_version")

    def _identity(self, record: EmbeddingRecord) -> str:
        return f"{record.entity_id}|{record.model_name}|{record.model_version}"

    def _index_values(self, record: EmbeddingRecord) -> dict[str, list[str]]:
        return {
            "entity_id": [record.entity_id],
            "model_name": [record.model_name],
            "model_version": [record.model_version],
        }

    def for_entity(self, entity_id: str) -> list[EmbeddingRecord]:
        return self.find(entity_id=entity_id)

    def for_model(
        self, model_name: str, model_version: str, entity_ids: Sequence[str]
    ) -> dict[str, EmbeddingRecord]:
        keys = [f"{e}|{model_name}|{model_version}" for e in entity_ids]
        found = self.get_many(keys)
        return {record.entity_id: record for record in found.values()}


class SnapshotRepository(DocumentRepository[SnapshotMeta]):
    collection = "snapshots"
    model = SnapshotMeta
    indexed_fields = ("label", "sealed")

    def _index_values(self, record: SnapshotMeta) -> dict[str, list[str]]:
        return {"label": [record.label], "sealed": [str(record.sealed).lower()]}

    def by_label(self, label: str) -> SnapshotMeta | None:
        found = self.find(label=label)
        return found[0] if found else None

    def sealed(self) -> list[SnapshotMeta]:
        return self.find(sealed="true")


class DependencyRepository(DocumentRepository[DependencyRecord]):
    collection = "dependencies"
    model = DependencyRecord
    indexed_fields = (
        "manifest_id",
        "puzzle_id",
        "game_id",
        "day_key",
        "ref_id",
        "ref_kind",
    )

    def _index_values(self, record: DependencyRecord) -> dict[str, list[str]]:
        return {
            "manifest_id": [record.manifest_id],
            "puzzle_id": [record.puzzle_id],
            "game_id": [record.game_id],
            "day_key": [record.day_key],
            "ref_id": [record.ref_id],
            "ref_kind": [str(record.ref_kind)],
        }

    def dependents_of(self, ref_id: str) -> list[DependencyRecord]:
        """Every published puzzle that used this graph record."""
        return self.find(ref_id=ref_id)

    def dependencies_of(self, manifest_id: str) -> list[DependencyRecord]:
        return self.find(manifest_id=manifest_id)

    def blast_radius(self, ref_id: str) -> dict[str, Any]:
        """Impact summary for one record, for review prioritisation."""
        edges = self.dependents_of(ref_id)
        return {
            "ref_id": ref_id,
            "manifest_count": len({e.manifest_id for e in edges}),
            "puzzle_count": len({e.puzzle_id for e in edges}),
            "game_ids": sorted({e.game_id for e in edges}),
            "day_keys": sorted({e.day_key for e in edges}),
        }

    def record_refs(
        self,
        *,
        manifest_id: str,
        puzzle_id: str,
        game_id: str,
        day_key: str,
        refs: Sequence[tuple[DependencyRefKind, str]],
        created_at: dt.datetime,
    ) -> int:
        records = [
            DependencyRecord.build(
                manifest_id=manifest_id,
                puzzle_id=puzzle_id,
                game_id=game_id,
                day_key=day_key,
                ref_kind=kind,
                ref_id=ref_id,
                created_at=created_at,
            )
            for kind, ref_id in refs
        ]
        return self.put_many(records)


# -- Protocols for repositories implemented in later phases ----------------


class PuzzleRepositoryProtocol(Protocol):
    """Stores assembled, pre-publication puzzles. Implemented in phase 4."""

    def put(self, record: Any) -> None: ...
    def get(self, record_id: str) -> Any | None: ...
    def find(self, **equals: str) -> list[Any]: ...


class ManifestRepositoryProtocol(Protocol):
    """Stores immutable published manifests. Implemented in phase 4."""

    def put(self, record: Any) -> None: ...
    def get(self, record_id: str) -> Any | None: ...
    def for_day(self, day_key: str) -> list[Any]: ...


class SessionRepositoryProtocol(Protocol):
    """Stores play sessions and their telemetry. Implemented in phase 6."""

    def put(self, record: Any) -> None: ...
    def get(self, record_id: str) -> Any | None: ...
    def for_player(self, player_key: str) -> list[Any]: ...


class ReviewRepositoryProtocol(Protocol):
    """Stores the human review queue. Implemented in phase 9."""

    def put(self, record: Any) -> None: ...
    def get(self, record_id: str) -> Any | None: ...
    def open_items(self, limit: int | None = None) -> list[Any]: ...


class GraphRepositories:
    """Every graph repository bound to one store.

    Constructed once and passed down. Layers above hold this object, never the
    store itself, so there is no route from the content service to raw SQL.
    """

    def __init__(self, store: DocumentStore) -> None:
        self._store = store
        self.sources = SourceRepository(store)
        self.categories = CategoryRepository(store)
        self.entities = EntityRepository(store)
        self.facts = FactRepository(store)
        self.relationships = RelationshipRepository(store)
        self.frequencies = FrequencyRepository(store)
        self.embeddings = EmbeddingRepository(store)
        self.snapshots = SnapshotRepository(store)
        self.dependencies = DependencyRepository(store)

    def transaction(self):
        return self._store.transaction()

    def record_counts(self) -> dict[str, int]:
        return {
            repo.collection: repo.count()
            for repo in (
                self.sources,
                self.categories,
                self.entities,
                self.facts,
                self.relationships,
                self.frequencies,
                self.embeddings,
                self.dependencies,
            )
        }

    def close(self) -> None:
        self._store.close()
