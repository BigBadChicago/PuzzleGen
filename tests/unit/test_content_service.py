from __future__ import annotations

import datetime as dt

import pytest

from puzzlegen.content.freshness import (
    REVIEW_INTERVAL_DAYS,
    evaluate,
    next_review_at,
)
from puzzlegen.content.normalizer import (
    NormalizationError,
    Normalizer,
    merge_governed,
)
from puzzlegen.content.snapshots import (
    ActivationPolicy,
    ImportReport,
    SnapshotBuilder,
    compute_content_hash,
    verify_snapshot,
)
from puzzlegen.core.errors import ContentError
from puzzlegen.core.types import (
    FreshnessClass,
    FrequencyBand,
    ProvenanceClass,
    ReviewStatus,
    SourceKind,
)
from puzzlegen.providers.base import (
    ProviderBundle,
    ProviderDescriptor,
    RawCategory,
    RawEntity,
    RawFact,
    RawRelationship,
)
from puzzlegen.providers.curated import CuratedJSONProvider
from puzzlegen.providers.embeddings import DevHashEmbeddingProvider
from puzzlegen.providers.frequency import TableFrequencyProvider
from puzzlegen.providers.wordnet import WordNetLexiconProvider

from ..conftest import NOW

SEEDS = "content/seeds"


def descriptor(name: str = "test", version: str = "1") -> ProviderDescriptor:
    return ProviderDescriptor(
        name=name,
        kind=SourceKind.CURATED_INTERNAL,
        version=version,
        retrieved_at=NOW,
    )


def simple_bundle(name: str = "test") -> ProviderBundle:
    return ProviderBundle(
        descriptor=descriptor(name),
        categories=(
            RawCategory(key="c.animal", name="animal"),
            RawCategory(key="c.feline", name="feline", parent_keys=("c.animal",)),
        ),
        entities=(
            RawEntity(key="e.tiger", name="tiger", category_keys=("c.feline",)),
            RawEntity(key="e.lion", name="lion", category_keys=("c.feline",)),
        ),
        facts=(
            RawFact(subject_key="e.tiger", predicate="habitat", value="forest"),
        ),
        relationships=(
            RawRelationship(
                subject_key="e.tiger",
                predicate="shares_range_with",
                object_key="e.lion",
                symmetric=True,
            ),
        ),
    )


class TestFreshnessPolicy:
    def test_static_facts_never_schedule_a_review(self):
        assert next_review_at(FreshnessClass.STATIC, NOW) is None

    @pytest.mark.parametrize(
        "cls",
        [
            FreshnessClass.SLOW_CHANGING,
            FreshnessClass.PERIODIC,
            FreshnessClass.TIME_SENSITIVE,
            FreshnessClass.VOLATILE,
        ],
    )
    def test_non_static_facts_schedule_their_interval(self, cls):
        expected = NOW + dt.timedelta(days=REVIEW_INTERVAL_DAYS[cls])
        assert next_review_at(cls, NOW) == expected

    def test_intervals_shorten_as_volatility_rises(self):
        intervals = [
            REVIEW_INTERVAL_DAYS[c]
            for c in (
                FreshnessClass.SLOW_CHANGING,
                FreshnessClass.PERIODIC,
                FreshnessClass.TIME_SENSITIVE,
                FreshnessClass.VOLATILE,
            )
        ]
        assert intervals == sorted(intervals, reverse=True)

    def test_a_static_fact_is_always_fresh(self):
        verdict = evaluate(FreshnessClass.STATIC, None, None, NOW)
        assert verdict.fresh and not verdict.stale

    def test_an_unverified_non_static_fact_is_stale(self):
        verdict = evaluate(FreshnessClass.PERIODIC, None, None, NOW)
        assert verdict.stale and not verdict.fresh

    def test_a_fact_inside_its_window_is_fresh(self):
        review = next_review_at(FreshnessClass.PERIODIC, NOW)
        verdict = evaluate(FreshnessClass.PERIODIC, NOW, review, NOW)
        assert verdict.fresh and not verdict.due

    def test_a_slightly_overdue_fact_is_due_but_not_yet_stale(self):
        review = next_review_at(FreshnessClass.PERIODIC, NOW)
        later = review + dt.timedelta(days=10)
        verdict = evaluate(FreshnessClass.PERIODIC, NOW, review, later)
        assert verdict.due and verdict.fresh and not verdict.stale

    def test_a_badly_overdue_fact_is_stale(self):
        review = next_review_at(FreshnessClass.PERIODIC, NOW)
        later = review + dt.timedelta(days=200)
        verdict = evaluate(FreshnessClass.PERIODIC, NOW, review, later)
        assert verdict.stale and not verdict.fresh

    def test_volatile_facts_have_no_grace(self):
        review = next_review_at(FreshnessClass.VOLATILE, NOW)
        verdict = evaluate(
            FreshnessClass.VOLATILE, NOW, review, review + dt.timedelta(days=1)
        )
        assert verdict.stale


class TestNormalizer:
    def test_import_never_produces_active_records(self):
        with pytest.raises(ValueError):
            Normalizer(import_status=ReviewStatus.ACTIVE)

    def test_records_carry_provenance_and_a_source(self):
        result = Normalizer(now=NOW).normalize(simple_bundle())
        assert result.source.kind is SourceKind.CURATED_INTERNAL
        assert all(e.provenance for e in result.entities)
        assert all(
            p.source_id == result.source.id
            for e in result.entities
            for p in e.provenance
        )

    def test_no_imported_record_is_usable(self):
        result = Normalizer(now=NOW).normalize(simple_bundle())
        assert not any(
            r.is_usable()
            for r in (*result.entities, *result.categories, *result.facts)
        )

    def test_provider_keys_are_mapped_to_internal_ids(self):
        result = Normalizer(now=NOW).normalize(simple_bundle())
        assert result.key_map["e.tiger"].startswith("entity:")
        assert result.key_map["c.feline"].startswith("category:")

    def test_provider_key_is_kept_only_in_provenance(self):
        result = Normalizer(now=NOW).normalize(simple_bundle())
        tiger = next(e for e in result.entities if e.canonical_name == "tiger")
        assert "e.tiger" not in tiger.id
        assert any(p.source_ref == "e.tiger" for p in tiger.provenance)

    def test_category_depth_is_derived(self):
        result = Normalizer(now=NOW).normalize(simple_bundle())
        feline = next(c for c in result.categories if c.canonical_name == "feline")
        assert feline.depth == 1 and feline.min_depth == 1

    def test_membership_becomes_a_relationship_with_provenance(self):
        result = Normalizer(now=NOW).normalize(simple_bundle())
        memberships = [r for r in result.relationships if r.predicate == "is_a"]
        assert len(memberships) == 2
        assert all(r.provenance for r in memberships)

    def test_judged_facts_wait_for_review(self):
        bundle = ProviderBundle(
            descriptor=descriptor(),
            entities=(RawEntity(key="e", name="tiger"),),
            facts=(
                RawFact(
                    subject_key="e",
                    predicate="cultural_prominence",
                    value="high",
                    provenance_class=ProvenanceClass.JUDGED,
                    reviewer="curator:a",
                    confidence=0.7,
                ),
            ),
        )
        result = Normalizer(now=NOW).normalize(bundle)
        assert result.facts[0].status is ReviewStatus.PENDING_REVIEW

    def test_freshness_drives_the_review_date(self):
        bundle = ProviderBundle(
            descriptor=descriptor(),
            entities=(RawEntity(key="e", name="tiger"),),
            facts=(
                RawFact(
                    subject_key="e",
                    predicate="population",
                    value=3900,
                    freshness_class=FreshnessClass.PERIODIC,
                ),
            ),
        )
        fact = Normalizer(now=NOW).normalize(bundle).facts[0]
        assert fact.next_review_at == next_review_at(FreshnessClass.PERIODIC, NOW)

    def test_an_invalid_bundle_is_refused(self):
        bundle = ProviderBundle(
            descriptor=descriptor(),
            facts=(RawFact(subject_key="ghost", predicate="p", value=1),),
        )
        with pytest.raises(NormalizationError):
            Normalizer(now=NOW).normalize(bundle)

    def test_lemma_identity_merges_polysemous_senses(self):
        bundle = ProviderBundle(
            descriptor=descriptor(),
            categories=(
                RawCategory(key="c.a", name="animal"),
                RawCategory(key="c.b", name="vehicle"),
            ),
            entities=(
                RawEntity(key="s1", name="jaguar", category_keys=("c.a",)),
                RawEntity(key="s2", name="jaguar", category_keys=("c.b",)),
            ),
        )
        result = Normalizer(now=NOW, entity_identity="lemma").normalize(bundle)
        assert len({e.id for e in result.entities}) == 1

    def test_sense_identity_keeps_polysemous_senses_apart(self):
        bundle = ProviderBundle(
            descriptor=descriptor(),
            categories=(
                RawCategory(key="c.a", name="animal"),
                RawCategory(key="c.b", name="vehicle"),
            ),
            entities=(
                RawEntity(key="s1", name="jaguar", category_keys=("c.a",)),
                RawEntity(key="s2", name="jaguar", category_keys=("c.b",)),
            ),
        )
        result = Normalizer(now=NOW, entity_identity="sense").normalize(bundle)
        assert len({e.id for e in result.entities}) == 2

    def test_unknown_entity_identity_mode_is_refused(self):
        with pytest.raises(ValueError):
            Normalizer(entity_identity="synset")

    def test_wordnet_depth_hints_survive_normalization(self):
        provider = WordNetLexiconProvider(
            f"{SEEDS}/wordnet-mini.lexicon.json", now=NOW
        )
        result = Normalizer(
            now=NOW, taxonomy="wordnet", entity_identity="sense"
        ).normalize(provider.load())
        ailurid = next(c for c in result.categories if c.canonical_name == "ailurid")
        assert (ailurid.min_depth, ailurid.depth) == (4, 4)
        assert ailurid.has_forked_ancestry


class TestMerging:
    def test_provenance_accumulates_across_providers(self):
        a = Normalizer(now=NOW).normalize(simple_bundle("alpha"))
        b = Normalizer(now=NOW).normalize(simple_bundle("beta"))
        tiger_a = next(e for e in a.entities if e.canonical_name == "tiger")
        tiger_b = next(e for e in b.entities if e.canonical_name == "tiger")
        merged = merge_governed(tiger_a, tiger_b)
        assert len(merged.provenance) == 2
        assert merged.id == tiger_a.id

    def test_merging_is_idempotent(self):
        a = Normalizer(now=NOW).normalize(simple_bundle("alpha"))
        tiger = next(e for e in a.entities if e.canonical_name == "tiger")
        assert len(merge_governed(tiger, tiger).provenance) == 1

    def test_merge_takes_the_weakest_status(self):
        a = Normalizer(now=NOW).normalize(simple_bundle("alpha"))
        tiger = next(e for e in a.entities if e.canonical_name == "tiger")
        pending = tiger.model_copy(update={"status": ReviewStatus.PENDING_REVIEW})
        assert merge_governed(tiger, pending).status is ReviewStatus.PENDING_REVIEW

    def test_merge_unions_aliases(self):
        a = Normalizer(now=NOW).normalize(simple_bundle("alpha"))
        tiger = next(e for e in a.entities if e.canonical_name == "tiger")
        other = tiger.model_copy(update={"aliases": ("Panthera tigris",)})
        assert "Panthera tigris" in merge_governed(tiger, other).aliases

    def test_merge_refuses_mismatched_ids(self):
        a = Normalizer(now=NOW).normalize(simple_bundle())
        tiger, lion = a.entities[0], a.entities[1]
        with pytest.raises(NormalizationError):
            merge_governed(tiger, lion)


class TestSnapshotLifecycle:
    def _seed(self, repos, **kwargs):
        builder = SnapshotBuilder(repos, now=NOW)
        report = ImportReport(snapshot_id="", label="test")
        builder.import_provider(
            CuratedJSONProvider(f"{SEEDS}/animals.curated.json", now=NOW),
            report=report,
            **kwargs,
        )
        return builder, report

    def test_import_writes_records_that_cannot_be_used(self, repos):
        self._seed(repos)
        assert repos.entities.count() > 0
        assert repos.entities.active() == []

    def test_activation_promotes_qualifying_records(self, repos):
        builder, _ = self._seed(repos)
        builder.activate()
        assert len(repos.entities.active()) > 0
        assert len(repos.categories.active()) > 0

    def test_judged_facts_are_never_auto_activated(self, repos):
        builder, _ = self._seed(repos)
        builder.activate()
        judged = [
            f
            for f in repos.facts.iter_all()
            if ProvenanceClass.JUDGED in f.provenance_classes()
        ]
        assert judged
        assert all(f.status is not ReviewStatus.ACTIVE for f in judged)

    def test_low_confidence_records_are_withheld(self, repos):
        builder, report = self._seed(repos)
        builder.activate(ActivationPolicy(minimum_confidence=0.96), report=report)
        assert report.withheld.get("entities", 0) > 0
        assert any("confidence" in reason for reason in report.withheld_reasons)

    def test_a_relationship_is_not_activated_without_active_endpoints(self, repos):
        builder, report = self._seed(repos)
        # A threshold this high activates nothing, so no edge may activate.
        builder.activate(ActivationPolicy(minimum_confidence=0.999), report=report)
        assert repos.relationships.active() == []

    def test_parents_activate_before_children(self, repos):
        builder, _ = self._seed(repos)
        builder.activate()
        for category in repos.categories.active():
            for parent_id in category.parent_ids:
                assert repos.categories.require(parent_id).status is ReviewStatus.ACTIVE

    def test_frequency_bands_are_attached_to_entities(self, repos):
        builder, report = self._seed(repos)
        provider = TableFrequencyProvider(
            {"tiger": 4.6, "lion": 4.9, "petrel": 2.1},
            name="wordfreq",
            version="3.1",
            retrieved_at=NOW,
        )
        builder.attach_frequencies(provider, report=report)
        tiger = repos.entities.by_name("tiger")[0]
        assert tiger.frequency_band is FrequencyBand.COMMON
        assert report.frequency_missing > 0

    def test_frequency_records_keep_their_source_version(self, repos):
        builder, _ = self._seed(repos)
        builder.attach_frequencies(
            TableFrequencyProvider(
                {"tiger": 4.6}, name="wordfreq", version="3.1", retrieved_at=NOW
            )
        )
        tiger = repos.entities.by_name("tiger")[0]
        record = repos.frequencies.for_entity(tiger.id)[0]
        assert record.source_version == "3.1" and record.zipf == 4.6

    def test_requiring_frequency_withholds_unscored_entities(self, repos):
        builder, report = self._seed(repos)
        builder.attach_frequencies(
            TableFrequencyProvider(
                {"tiger": 4.6}, name="wordfreq", version="3.1", retrieved_at=NOW
            )
        )
        builder.activate(
            ActivationPolicy(require_frequency_for_entities=True), report=report
        )
        assert [e.canonical_name for e in repos.entities.active()] == ["tiger"]

    def test_embeddings_are_frozen_into_the_store(self, repos):
        builder, report = self._seed(repos)
        builder.attach_embeddings(DevHashEmbeddingProvider(now=NOW), report=report)
        assert report.embeddings_written == repos.entities.count()
        tiger = repos.entities.by_name("tiger")[0]
        assert repos.embeddings.for_entity(tiger.id)[0].dimensions == 32

    def test_development_embeddings_raise_a_warning(self, repos):
        builder, report = self._seed(repos)
        builder.attach_embeddings(DevHashEmbeddingProvider(now=NOW), report=report)
        assert any("development embeddings" in w for w in report.warnings)

    def test_sealing_records_versions_and_counts(self, repos):
        builder, _ = self._seed(repos)
        builder.activate()
        meta = builder.seal(
            "2026-09-26",
            frequency=TableFrequencyProvider(
                {"tiger": 4.6}, name="wordfreq", version="3.1", retrieved_at=NOW
            ),
            embeddings=DevHashEmbeddingProvider(now=NOW),
        )
        assert meta.sealed and meta.content_hash
        assert meta.frequency_source_version == "3.1"
        assert meta.embedding_model == "dev-hash"
        assert meta.record_counts["entities"] > 0
        assert meta.source_versions

    def test_a_sealed_snapshot_verifies(self, repos):
        builder, _ = self._seed(repos)
        builder.activate()
        meta = builder.seal("2026-09-26")
        verify_snapshot(repos, meta)

    def test_modifying_a_sealed_snapshot_is_detected(self, repos):
        builder, _ = self._seed(repos)
        builder.activate()
        meta = builder.seal("2026-09-26")
        tiger = repos.entities.by_name("tiger")[0]
        repos.entities.put(tiger.model_copy(update={"definition": "tampered"}))
        with pytest.raises(ContentError):
            verify_snapshot(repos, meta)

    def test_an_unsealed_snapshot_is_refused(self, repos):
        builder, _ = self._seed(repos)
        meta = builder.seal("2026-09-26")
        with pytest.raises(ContentError):
            verify_snapshot(repos, meta.model_copy(update={"sealed": False}))

    def test_publishing_a_puzzle_does_not_change_the_content_hash(self, repos):
        from puzzlegen.core import ids
        from puzzlegen.core.types import DependencyRefKind

        builder, _ = self._seed(repos)
        builder.activate()
        before = compute_content_hash(repos)
        tiger = repos.entities.by_name("tiger")[0]
        puzzle_id = ids.for_puzzle("2026-09-26", "grouping", "h")
        repos.dependencies.record_refs(
            manifest_id=ids.for_manifest(puzzle_id),
            puzzle_id=puzzle_id,
            game_id="grouping",
            day_key="2026-09-26",
            refs=[(DependencyRefKind.ENTITY, tiger.id)],
            created_at=NOW,
        )
        assert compute_content_hash(repos) == before

    def test_the_same_build_produces_the_same_hash(self, repos, store, tmp_path):
        from puzzlegen.graph.memory_store import InMemoryDocumentStore
        from puzzlegen.graph.repositories import GraphRepositories

        def build(target):
            builder = SnapshotBuilder(target, now=NOW)
            builder.import_provider(
                CuratedJSONProvider(f"{SEEDS}/animals.curated.json", now=NOW)
            )
            builder.attach_embeddings(DevHashEmbeddingProvider(now=NOW))
            builder.activate()
            return builder.seal("2026-09-26").content_hash

        first = build(repos)
        second = build(GraphRepositories(InMemoryDocumentStore()))
        assert first == second


class TestFullBuild:
    def test_two_providers_share_one_graph(self, repos):
        builder = SnapshotBuilder(repos, now=NOW)
        meta, report = builder.build(
            "2026-09-26",
            providers=[
                (CuratedJSONProvider(f"{SEEDS}/animals.curated.json", now=NOW), {}),
                (
                    WordNetLexiconProvider(f"{SEEDS}/wordnet-mini.lexicon.json", now=NOW),
                    {"taxonomy": "wordnet", "entity_identity": "sense"},
                ),
            ],
            frequency=TableFrequencyProvider(
                {"tiger": 4.6, "raccoon": 3.8},
                name="wordfreq",
                version="3.1",
                retrieved_at=NOW,
            ),
            embeddings=DevHashEmbeddingProvider(now=NOW),
        )
        assert meta.sealed
        assert len(report.provider_counts) == 2
        assert len(list(repos.sources.iter_all())) == 2

    def test_the_two_taxonomies_stay_separate(self, repos):
        builder = SnapshotBuilder(repos, now=NOW)
        builder.build(
            "2026-09-26",
            providers=[
                (CuratedJSONProvider(f"{SEEDS}/animals.curated.json", now=NOW), {}),
                (
                    WordNetLexiconProvider(f"{SEEDS}/wordnet-mini.lexicon.json", now=NOW),
                    {"taxonomy": "wordnet", "entity_identity": "sense"},
                ),
            ],
        )
        assert {c.taxonomy for c in repos.categories.iter_all()} == {
            "default",
            "wordnet",
        }

    def test_forked_ancestry_survives_into_the_store(self, repos):
        builder = SnapshotBuilder(repos, now=NOW)
        builder.build(
            "2026-09-26",
            providers=[
                (
                    WordNetLexiconProvider(f"{SEEDS}/wordnet-mini.lexicon.json", now=NOW),
                    {"taxonomy": "wordnet", "entity_identity": "sense"},
                )
            ],
        )
        ailurid = repos.categories.by_name("ailurid")[0]
        assert len(ailurid.parent_ids) == 2
        ancestors = repos.categories.ancestors(ailurid.id)
        names = [c.canonical_name for c in ancestors]
        # Reached by both paths, returned once.
        assert names.count("carnivore") == 1
        assert set(names) >= {"entity", "organism", "carnivore"}

    def test_shared_ancestors_of_siblings(self, repos):
        builder = SnapshotBuilder(repos, now=NOW)
        builder.build(
            "2026-09-26",
            providers=[
                (CuratedJSONProvider(f"{SEEDS}/animals.curated.json", now=NOW), {})
            ],
        )
        feline = repos.categories.by_name("feline")[0]
        canine = repos.categories.by_name("canine")[0]
        shared = repos.categories.shared_ancestors([feline.id, canine.id])
        assert [c.canonical_name for c in shared] == ["animal", "mammal"]

    def test_a_category_cannot_be_written_before_its_parents(self, repos):
        from puzzlegen.core.errors import ConflictError
        from puzzlegen.graph.models import Category

        orphan = Category.build(
            canonical_name="feline",
            created_at=NOW,
            status=ReviewStatus.CANDIDATE,
        ).model_copy(
            update={
                "parent_ids": ("category:animal",),
                "depth": 1,
                "min_depth": 1,
            }
        )
        with pytest.raises(ConflictError):
            repos.categories.put(orphan)
