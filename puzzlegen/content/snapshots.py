"""Content snapshots.

A daily puzzle must regenerate identically years later, which is impossible if
generation reads a source that can change. So generation reads a snapshot: an
immutable, hashed set of graph records built once from providers and then
sealed.

The lifecycle is deliberately three steps rather than one:

    import  ->  activate  ->  seal

Import writes records that are explicitly not usable. Activation applies a
stated policy and promotes what qualifies. Sealing computes a content hash and
freezes the snapshot. Collapsing these would let a provider's output reach a
puzzle without any policy having been applied to it, which is the thing P3
exists to prevent.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..core import ids
from ..core.errors import ContentError
from ..core.hashing import stable_hash
from ..core.types import (
    FrequencyBand,
    ProvenanceClass,
    ReviewStatus,
    SourceKind,
)
from ..core.versions import CONTENT_SCHEMA_VERSION
from ..graph.models import EmbeddingRecord, FrequencyRecord, SnapshotMeta
from ..graph.repositories import GraphRepositories
from ..providers.base import ContentProvider
from ..providers.embeddings import EmbeddingProvider
from ..providers.frequency import FrequencyProvider, NullFrequencyProvider
from .normalizer import NormalizedBundle, Normalizer, merge_governed


@dataclass(frozen=True, slots=True)
class ActivationPolicy:
    """What automated import is allowed to promote without a human.

    Defaults are strict in the one place it matters: a JUDGED assertion is
    never auto-approved, because automation can confirm that a source said
    something but not that an interpretation is correct.
    """

    minimum_confidence: float = 0.75
    auto_approve_classes: frozenset[ProvenanceClass] = frozenset(
        {ProvenanceClass.SOURCED, ProvenanceClass.COMPUTED}
    )
    #: Providers whose content always waits for review regardless of class.
    manual_review_sources: frozenset[str] = frozenset()
    require_frequency_for_entities: bool = False
    #: Whether a record the review ledger has already carried to APPROVED may
    #: activate despite its provenance class. Without this the two gates would
    #: not be two gates: JUDGED content could gather any number of credited
    #: accepts and still never reach a puzzle.
    admit_approved: bool = True

    def permits(self, record) -> tuple[bool, str]:
        if not record.provenance:
            return False, "no provenance"
        if record.confidence < self.minimum_confidence:
            return False, (
                f"confidence {record.confidence:.2f} below "
                f"{self.minimum_confidence:.2f}"
            )
        sources = {p.source_id for p in record.provenance}
        if sources & self.manual_review_sources:
            return False, "source requires manual review"
        if record.status is ReviewStatus.APPROVED and self.admit_approved:
            # The class check asks whether a human has judged the
            # interpretation. Ten credited accepts are that human, recorded in
            # a ledger, so asking again here would be asking twice and
            # answering no. The confidence floor and the manual-review source
            # list above still apply.
            return True, "approved by review"
        classes = record.provenance_classes()
        blocked = classes - self.auto_approve_classes
        if blocked:
            return False, f"provenance class requires review: {sorted(blocked)}"
        return True, "auto-approved"


@dataclass
class ImportReport:
    """What one snapshot build did, for the curator and for telemetry."""

    snapshot_id: str
    label: str
    provider_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    merged: dict[str, int] = field(default_factory=dict)
    activated: dict[str, int] = field(default_factory=dict)
    withheld: dict[str, int] = field(default_factory=dict)
    withheld_reasons: dict[str, int] = field(default_factory=dict)
    frequency_scored: int = 0
    frequency_missing: int = 0
    embeddings_written: int = 0
    warnings: list[str] = field(default_factory=list)

    def total_activated(self) -> int:
        return sum(self.activated.values())


def embedding_text(entity) -> str:
    """The text an entity is embedded as.

    Embed what a game would actually show, not the internal id, and include the
    definition when present: two entities named identically are distinguished
    by their gloss, not their label.

    Exported because the build tool has to write the export command's input
    list, and any second implementation of this string drifts from this one the
    first time a merge picks a different sense's gloss. The embedding table is
    keyed by exactly this text.
    """
    if entity.definition:
        return f"{entity.canonical_name}: {entity.definition}"
    return entity.canonical_name


class SnapshotBuilder:
    """Builds one snapshot from a set of providers."""

    def __init__(
        self,
        repos: GraphRepositories,
        *,
        now: dt.datetime | None = None,
    ) -> None:
        self._repos = repos
        self._now = now or dt.datetime.now(dt.timezone.utc)

    # -- step 1: import ---------------------------------------------------

    def import_provider(
        self,
        provider: ContentProvider,
        *,
        taxonomy: str = "default",
        entity_identity: str = "lemma",
        report: ImportReport | None = None,
    ) -> NormalizedBundle:
        """Read one provider into the graph as non-usable records."""
        descriptor = provider.describe()
        freshness = provider.freshness_status()
        bundle = provider.load()

        normalizer = Normalizer(
            taxonomy=taxonomy, now=self._now, entity_identity=entity_identity
        )
        normalized = normalizer.normalize(bundle)

        if report is not None:
            # Summed, not assigned. Several exports share one provider name
            # (five WordNet roots are all "wordnet:oewn"), so assignment kept
            # only the last file's counts and the report understated the build
            # by four fifths while every record was in fact written.
            seen = report.provider_counts.setdefault(descriptor.name, {})
            for key, value in normalized.counts().items():
                seen[key] = seen.get(key, 0) + value
            report.warnings.extend(normalized.warnings)
            if not freshness.healthy:
                report.warnings.append(
                    f"provider {descriptor.name!r} reports stale: {freshness.detail}"
                )

        with self._repos.transaction():
            self._repos.sources.put(normalized.source)
            merged = self._write_merged(normalized, report)

        if report is not None:
            for key, value in merged.items():
                report.merged[key] = report.merged.get(key, 0) + value

        return normalized

    def _merge_category(self, existing, incoming, report):
        """Merge a category, keeping the parents of every sense that named it.

        A category is identified by its name, so two senses of one word
        exported under different roots become one category: ``viola`` the
        plant under ``herb`` and ``viola`` the instrument under ``bowed
        stringed instrument``. Keeping only the first import's parents homed
        the other sense in the wrong family or nowhere, which is precisely the
        double meaning this project is built on. The parents are therefore
        unioned, and the category ends up under both.

        Two costs come with that and are paid here rather than left implicit:

        * ``CategoryRepository.put`` refusing an absent parent is no longer
          the whole of cycle prevention, because both records exist by the
          time an edge is added. Every added parent is checked against the
          existing category's descendants first, and one that would close a
          loop is refused and reported rather than written.
        * ``depth`` is denormalised and derived from parents, so a category
          that gains a deeper parent changes its own depth and that of
          everything beneath it. Those depths are recomputed, nearest first.
        """
        combined = merge_governed(existing, incoming)
        added = [p for p in incoming.parent_ids if p not in existing.parent_ids]
        if not added:
            return combined

        below = set(self._repos.categories.descendant_ids(existing.id))
        safe, refused = [], []
        for parent_id in added:
            if parent_id == existing.id or parent_id in below:
                refused.append(parent_id)
            else:
                safe.append(parent_id)

        if refused and report is not None:
            names = ", ".join(sorted(refused))
            report.warnings.append(
                f"category {existing.canonical_name!r} ({existing.id}): refused "
                f"parents that would make it its own ancestor: {names}"
            )
        if not safe:
            return combined

        parents = (*existing.parent_ids, *safe)
        combined = combined.model_copy(update={"parent_ids": parents, **self._depths(parents)})
        self._repos.categories.put(combined)
        self._repair_depths(existing.id)
        return combined

    def _depths(self, parent_ids) -> dict[str, int]:
        """Depth and min_depth derived from the stored parents."""
        parents = [self._repos.categories.get(p) for p in parent_ids]
        present = [p for p in parents if p is not None]
        if not present:
            return {"depth": 0, "min_depth": 0}
        return {
            "depth": 1 + max(p.depth for p in present),
            "min_depth": 1 + min(p.min_depth for p in present),
        }

    def _repair_depths(self, category_id: str) -> None:
        """Recompute depth for everything under a category that just moved.

        Nearest first, so each category is recomputed after its own parents.
        Wu-Palmer similarity and the "most specific shared category" rule both
        read depth, so a stale value is a wrong similarity score rather than a
        cosmetic error.
        """
        for descendant_id in self._repos.categories.descendant_ids(category_id):
            record = self._repos.categories.get(descendant_id)
            if record is None:
                continue
            depths = self._depths(record.parent_ids)
            if (record.depth, record.min_depth) != (depths["depth"], depths["min_depth"]):
                self._repos.categories.put(record.model_copy(update=depths))

    def _write_merged(
        self, normalized: NormalizedBundle, report: ImportReport | None
    ) -> dict[str, int]:
        """Write records, merging with anything already present.

        Categories are written first and in parents-first order, because the
        repository refuses a category whose parents are absent.
        """
        merged = {"categories": 0, "entities": 0, "facts": 0, "relationships": 0}

        for category in normalized.categories:
            existing = self._repos.categories.get(category.id)
            if existing is None:
                self._repos.categories.put(category)
            else:
                self._repos.categories.put(self._merge_category(existing, category, report))
                merged["categories"] += 1

        for name, records, repo in (
            ("entities", normalized.entities, self._repos.entities),
            ("facts", normalized.facts, self._repos.facts),
            ("relationships", normalized.relationships, self._repos.relationships),
        ):
            for record in records:
                existing = repo.get(record.id)
                if existing is None:
                    repo.put(record)
                else:
                    repo.put(merge_governed(existing, record))
                    merged[name] += 1

        return merged

    # -- step 2: enrichment -----------------------------------------------

    def attach_frequencies(
        self,
        provider: FrequencyProvider,
        *,
        report: ImportReport | None = None,
    ) -> int:
        """Score every entity and record the band on the entity itself.

        The band is denormalised onto the entity because gates read it on
        every candidate; the score, its source and its version stay in a
        separate record so replacing the dataset never rewrites entities'
        provenance.
        """
        descriptor = provider.describe()
        written = 0
        missing = 0

        with self._repos.transaction():
            for entity in self._repos.entities.iter_all():
                score = provider.score(entity.canonical_name, entity.lang)
                if score is None:
                    missing += 1
                    continue
                self._repos.frequencies.put(
                    FrequencyRecord(
                        entity_id=entity.id,
                        lang=entity.lang,
                        zipf=score.zipf,
                        band=score.band,
                        source_name=descriptor.name,
                        source_version=descriptor.version,
                        retrieved_at=descriptor.retrieved_at,
                    )
                )
                if entity.frequency_band is not score.band:
                    self._repos.entities.put(
                        entity.model_copy(
                            update={
                                "frequency_band": score.band,
                                "updated_at": self._now,
                            }
                        )
                    )
                written += 1

        if report is not None:
            report.frequency_scored = written
            report.frequency_missing = missing
        return written

    def attach_embeddings(
        self,
        provider: EmbeddingProvider,
        *,
        report: ImportReport | None = None,
    ) -> int:
        """Compute and freeze one vector per entity."""
        descriptor = provider.describe()
        entities = list(self._repos.entities.iter_all())
        if not entities:
            return 0

        texts = [embedding_text(e) for e in entities]
        vectors = provider.embed(texts)
        if len(vectors) != len(entities):
            raise ContentError("embedding provider returned the wrong number of vectors")

        records = [
            EmbeddingRecord(
                entity_id=entity.id,
                model_name=descriptor.model_name,
                model_version=descriptor.model_version,
                dimensions=descriptor.dimensions,
                vector=tuple(vector),
                computed_at=descriptor.computed_at,
            )
            for entity, vector in zip(entities, vectors)
        ]
        self._repos.embeddings.put_many(records)

        if report is not None:
            report.embeddings_written = len(records)
            if descriptor.is_development_only:
                report.warnings.append(
                    "snapshot built with development embeddings: "
                    "embedding-based gates carry no semantic signal"
                )
        return len(records)

    # -- step 3: activation -----------------------------------------------

    def activate(
        self,
        policy: ActivationPolicy | None = None,
        *,
        report: ImportReport | None = None,
    ) -> dict[str, int]:
        """Promote qualifying records to ACTIVE.

        Order matters. A relationship whose endpoints are not active would be
        an active edge into unusable content, so categories and entities are
        promoted first and edges are only promoted when both endpoints made
        it. The same applies to facts and their subjects.
        """
        policy = policy or ActivationPolicy()
        activated = {"categories": 0, "entities": 0, "facts": 0, "relationships": 0}
        withheld: dict[str, int] = {}
        reasons: dict[str, int] = {}

        def consider(record, collection: str, extra_ok: bool = True) -> bool:
            permitted, reason = policy.permits(record)
            if not (permitted and extra_ok):
                withheld[collection] = withheld.get(collection, 0) + 1
                key = reason if not permitted else "endpoint not active"
                reasons[key] = reasons.get(key, 0) + 1
                return False
            return True

        with self._repos.transaction():
            # Parents first: a category is only usable if its ancestry is.
            pending = [
                c
                for c in self._repos.categories.iter_all()
                if c.status is not ReviewStatus.ACTIVE and not c.retired
            ]
            for category in sorted(pending, key=lambda c: (c.depth, c.id)):
                parents_ok = all(
                    (parent := self._repos.categories.get(pid)) is not None
                    and parent.status is ReviewStatus.ACTIVE
                    for pid in category.parent_ids
                )
                if consider(category, "categories", parents_ok):
                    self._repos.categories.put(self._promote(category))
                    activated["categories"] += 1

            for entity in self._repos.entities.iter_all():
                if entity.status is ReviewStatus.ACTIVE:
                    continue
                frequency_ok = (
                    not policy.require_frequency_for_entities
                    or entity.frequency_band is not None
                )
                if consider(entity, "entities", frequency_ok):
                    self._repos.entities.put(self._promote(entity))
                    activated["entities"] += 1

            for fact in self._repos.facts.iter_all():
                if fact.status is ReviewStatus.ACTIVE:
                    continue
                subject = self._repos.entities.get(fact.subject_id)
                subject_ok = subject is not None and subject.status is ReviewStatus.ACTIVE
                if consider(fact, "facts", subject_ok):
                    self._repos.facts.put(self._promote(fact))
                    activated["facts"] += 1

            for rel in self._repos.relationships.iter_all():
                if rel.status is ReviewStatus.ACTIVE:
                    continue
                endpoints_ok = all(
                    self._endpoint_active(record_id)
                    for record_id in (rel.subject_id, rel.object_id)
                )
                if consider(rel, "relationships", endpoints_ok):
                    self._repos.relationships.put(self._promote(rel))
                    activated["relationships"] += 1

        if report is not None:
            report.activated = activated
            report.withheld = withheld
            report.withheld_reasons = reasons
        return activated

    def _endpoint_active(self, record_id: str) -> bool:
        kind = ids.kind_of(record_id)
        repo = self._repos.entities if kind == ids.ENTITY else self._repos.categories
        record = repo.get(record_id)
        return record is not None and record.status is ReviewStatus.ACTIVE

    def _promote(self, record):
        return record.model_copy(
            update={"status": ReviewStatus.ACTIVE, "updated_at": self._now}
        )

    # -- step 4: seal -----------------------------------------------------

    def seal(
        self,
        label: str,
        *,
        frequency: FrequencyProvider | None = None,
        embeddings: EmbeddingProvider | None = None,
        report: ImportReport | None = None,
    ) -> SnapshotMeta:
        """Freeze the snapshot and record its content hash."""
        frequency = frequency or NullFrequencyProvider(self._now)
        frequency_descriptor = frequency.describe()
        embedding_descriptor = embeddings.describe() if embeddings else None

        source_versions = {
            source.id: source.version for source in self._repos.sources.iter_all()
        }

        meta = SnapshotMeta(
            id=ids.for_snapshot(label),
            label=label,
            created_at=self._now,
            schema_version=CONTENT_SCHEMA_VERSION,
            source_versions=source_versions,
            embedding_model=(
                embedding_descriptor.model_name if embedding_descriptor else None
            ),
            embedding_model_version=(
                embedding_descriptor.model_version if embedding_descriptor else None
            ),
            frequency_source=frequency_descriptor.name,
            frequency_source_version=frequency_descriptor.version,
            record_counts=self._repos.record_counts(),
            content_hash=compute_content_hash(self._repos),
            sealed=True,
        )
        self._repos.snapshots.put(meta)
        if report is not None:
            report.snapshot_id = meta.id
            report.label = meta.label
        return meta

    # -- convenience ------------------------------------------------------

    def build(
        self,
        label: str,
        providers: Sequence[tuple[ContentProvider, dict]],
        *,
        frequency: FrequencyProvider | None = None,
        embeddings: EmbeddingProvider | None = None,
        policy: ActivationPolicy | None = None,
    ) -> tuple[SnapshotMeta, ImportReport]:
        """Run the whole lifecycle: import every provider, enrich, activate, seal."""
        report = ImportReport(snapshot_id="", label=label)

        for provider, options in providers:
            self.import_provider(provider, report=report, **options)

        if frequency is not None:
            self.attach_frequencies(frequency, report=report)
        if embeddings is not None:
            self.attach_embeddings(embeddings, report=report)

        self.activate(policy, report=report)
        meta = self.seal(
            label, frequency=frequency, embeddings=embeddings, report=report
        )
        return meta, report


def compute_content_hash(repos: GraphRepositories) -> str:
    """Hash of every governed record in the store.

    Excludes snapshot metadata itself, which contains the hash, and excludes
    dependency records, which are written after publication and would make the
    snapshot's hash change every time a puzzle is published against it.
    """
    parts: list[str] = []
    for name, repo in (
        ("sources", repos.sources),
        ("categories", repos.categories),
        ("entities", repos.entities),
        ("facts", repos.facts),
        ("relationships", repos.relationships),
        ("frequencies", repos.frequencies),
        ("embeddings", repos.embeddings),
    ):
        parts.append(name)
        parts.extend(
            stable_hash(record.model_dump(mode="json")) for record in repo.iter_all()
        )
    return stable_hash(parts)


def verify_snapshot(repos: GraphRepositories, meta: SnapshotMeta) -> None:
    """Refuse a snapshot whose content no longer matches its hash.

    Called before generation. A mismatch means something wrote to a sealed
    snapshot, and every reproducibility claim made against it is now false.
    """
    if not meta.sealed:
        raise ContentError(f"snapshot {meta.label!r} is not sealed")
    if meta.schema_version != CONTENT_SCHEMA_VERSION:
        raise ContentError(
            f"snapshot {meta.label!r} uses content schema v{meta.schema_version}, "
            f"engine expects v{CONTENT_SCHEMA_VERSION}"
        )
    actual = compute_content_hash(repos)
    if actual != meta.content_hash:
        raise ContentError(
            f"snapshot {meta.label!r} has been modified since sealing: "
            f"expected {meta.content_hash[:12]}, found {actual[:12]}"
        )
