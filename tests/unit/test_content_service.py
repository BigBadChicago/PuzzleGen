from __future__ import annotations

import datetime as dt
import json

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
from puzzlegen.content.query import ContentQuery, Operation
from puzzlegen.content.service import ContentService
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


class TestCategoriesThatMergeByName:
    """Two source nodes with one name are one category, parents included.

    Open English WordNet has two distinct `galley` synsets, a ship's kitchen
    and a rowed ship, and both are hypernyms of `monoreme`. Category identity
    is the canonical name, so both parents resolve to one id. The model
    rejected the duplicate and a depth 6 export could not be imported at all.
    """

    def galley_bundle(self, *, child_parents: tuple[str, ...]) -> ProviderBundle:
        return ProviderBundle(
            descriptor=descriptor(),
            categories=(
                RawCategory(key="c.kitchen", name="galley", gloss="a ship's kitchen"),
                RawCategory(key="c.ship", name="galley", gloss="a ship propelled by oars"),
                RawCategory(key="c.monoreme", name="monoreme", parent_keys=child_parents),
            ),
            entities=(
                RawEntity(key="e.trireme", name="trireme", category_keys=("c.monoreme",)),
            ),
        )

    def test_two_parents_that_merge_become_one_parent(self):
        result = Normalizer(now=NOW).normalize(
            self.galley_bundle(child_parents=("c.kitchen", "c.ship"))
        )
        child = next(c for c in result.categories if c.canonical_name == "monoreme")
        assert len(child.parent_ids) == 1

    def test_the_surviving_parent_is_the_merged_category(self):
        result = Normalizer(now=NOW).normalize(
            self.galley_bundle(child_parents=("c.kitchen", "c.ship"))
        )
        child = next(c for c in result.categories if c.canonical_name == "monoreme")
        galley = next(c for c in result.categories if c.canonical_name == "galley")
        assert child.parent_ids == (galley.id,)

    def test_the_two_source_nodes_produce_one_category(self):
        result = Normalizer(now=NOW).normalize(
            self.galley_bundle(child_parents=("c.kitchen", "c.ship"))
        )
        assert sum(1 for c in result.categories if c.canonical_name == "galley") == 1

    def test_first_seen_order_decides_which_gloss_survives(self):
        """Deduplication keeps the first, so the order the provider emitted
        is the order the merge respects, not an arbitrary one."""
        result = Normalizer(now=NOW).normalize(
            self.galley_bundle(child_parents=("c.kitchen", "c.ship"))
        )
        galley = next(c for c in result.categories if c.canonical_name == "galley")
        assert galley.gloss == "a ship's kitchen"

    def test_distinct_parents_are_all_kept(self):
        bundle = ProviderBundle(
            descriptor=descriptor(),
            categories=(
                RawCategory(key="c.vessel", name="vessel"),
                RawCategory(key="c.kitchen", name="kitchen"),
                RawCategory(
                    key="c.galley", name="galley", parent_keys=("c.vessel", "c.kitchen")
                ),
            ),
            entities=(RawEntity(key="e.x", name="x", category_keys=("c.galley",)),),
        )
        result = Normalizer(now=NOW).normalize(bundle)
        child = next(c for c in result.categories if c.canonical_name == "galley")
        assert len(child.parent_ids) == 2

    def test_a_single_parent_is_unaffected(self):
        result = Normalizer(now=NOW).normalize(
            self.galley_bundle(child_parents=("c.ship",))
        )
        child = next(c for c in result.categories if c.canonical_name == "monoreme")
        assert len(child.parent_ids) == 1

    def test_three_parents_collapsing_to_one_still_import(self):
        bundle = ProviderBundle(
            descriptor=descriptor(),
            categories=(
                RawCategory(key="c.a", name="galley"),
                RawCategory(key="c.b", name="galley"),
                RawCategory(key="c.c", name="galley"),
                RawCategory(
                    key="c.child", name="monoreme", parent_keys=("c.a", "c.b", "c.c")
                ),
            ),
            entities=(RawEntity(key="e.x", name="x", category_keys=("c.child",)),),
        )
        result = Normalizer(now=NOW).normalize(bundle)
        child = next(c for c in result.categories if c.canonical_name == "monoreme")
        assert len(child.parent_ids) == 1

    def test_the_child_is_still_reachable_from_the_merged_parent(self):
        """The edge survives the merge; only its duplicate is dropped."""
        result = Normalizer(now=NOW).normalize(
            self.galley_bundle(child_parents=("c.kitchen", "c.ship"))
        )
        galley = next(c for c in result.categories if c.canonical_name == "galley")
        child = next(c for c in result.categories if c.canonical_name == "monoreme")
        assert galley.id in child.parent_ids
        assert child.depth == galley.depth + 1


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


class TestGroupEnumerationScales:
    """Groups are found category by category, not by sifting the whole pool.

    Measured on the real 14,720 entity snapshot: one overlay query took 215
    seconds and returned nothing, because 480 million combinations of five
    contain 3,780 that share a category and a 20,000 budget reaches none of
    them. After this change the same query examines five combinations.
    """

    def seeded(
        self, repos, tmp_path, *, categories: int, per: int
    ) -> ContentService:
        """Many small categories in one pool, written through the real import
        path rather than hand-assembled records."""
        document = {
            "curated_schema": 1,
            "version": "1",
            "updated": "2026-09-28",
            "categories": [
                {"key": f"c{index}", "name": f"cat{index}"}
                for index in range(categories)
            ],
            "entities": [
                {
                    "key": f"e{index}_{n}",
                    "name": f"e{index}_{n}",
                    "categories": [f"c{index}"],
                    "confidence": 0.95,
                }
                for index in range(categories)
                for n in range(per)
            ],
        }
        path = tmp_path / "seed.curated.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        builder = SnapshotBuilder(repos, now=NOW)
        builder.import_provider(CuratedJSONProvider(path, now=NOW))
        builder.activate(ActivationPolicy())
        return ContentService(repos)

    def test_a_sparse_pool_still_finds_its_groups(self, repos, tmp_path):
        """The case that returned nothing: many small categories in a big pool."""
        service = self.seeded(repos, tmp_path, categories=12, per=6)
        result = service.execute(
            ContentQuery(operation=Operation.FIND_GROUPS, group_size=5, limit=5)
        )
        assert len(result.groups) == 5

    def test_it_examines_far_fewer_combinations_than_the_pool_has(
        self, repos, tmp_path
    ):
        service = self.seeded(repos, tmp_path, categories=12, per=6)
        result = service.execute(
            ContentQuery(operation=Operation.FIND_GROUPS, group_size=5, limit=5)
        )
        assert result.examined <= 10

    def test_members_of_one_group_share_a_category(self, repos, tmp_path):
        service = self.seeded(repos, tmp_path, categories=4, per=6)
        result = service.execute(
            ContentQuery(operation=Operation.FIND_GROUPS, group_size=5, limit=3)
        )
        assert result.groups
        for group in result.groups:
            assert group.shared_category_id

    def test_a_category_too_small_to_fill_a_group_is_skipped(self, repos, tmp_path):
        service = self.seeded(repos, tmp_path, categories=3, per=4)
        result = service.execute(
            ContentQuery(operation=Operation.FIND_GROUPS, group_size=5, limit=3)
        )
        assert result.groups == ()

    def test_memberships_are_read_once_for_the_whole_pool(self, repos, tmp_path):
        """One indexed lookup per entity is nothing until the pool is 14,720
        and the same query asks the same question 14,720 times."""
        service = self.seeded(repos, tmp_path, categories=4, per=6)
        first = service._all_memberships()
        assert first is service._all_memberships()
        assert len(first) == 24

    def test_a_group_with_no_shared_category_is_named_as_such(self):
        """Distinct from a distance rejection: the two need opposite fixes."""
        from puzzlegen.core.errors import RejectionReason

        assert RejectionReason.NO_SHARED_CATEGORY.value == "NO_SHARED_CATEGORY"
        assert (
            RejectionReason.NO_SHARED_CATEGORY
            is not RejectionReason.SEMANTIC_DISTANCE_TOO_HIGH
        )


class TestIntersectingTaxonomies:
    """Groups required to carry a second meaning.

    Measured on the real snapshot: an overlay of 144 entities inside a 14,720
    entity graph is touched by 157 lexical categories out of 6,511, so groups
    sampled from the lexicon almost never contain overlay members. The first
    real day covered its best hidden group one member in five.
    """

    def seeded(self, repos, tmp_path) -> ContentService:
        """Six lexical groups; two of their members also carry an overlay
        meaning."""
        lexical = {
            "curated_schema": 1,
            "version": "1",
            "updated": "2026-09-28",
            "categories": [{"key": f"c{i}", "name": f"cat{i}"} for i in range(3)],
            "entities": [
                {
                    "key": f"e{i}_{n}",
                    "name": f"e{i}_{n}",
                    "categories": [f"c{i}"],
                    "confidence": 0.95,
                }
                for i in range(3)
                for n in range(6)
            ],
        }
        overlay = {
            "curated_schema": 1,
            "version": "1",
            "updated": "2026-09-28",
            "categories": [{"key": "o0", "name": "second meaning"}],
            "entities": [
                {"key": f"e0_{n}", "name": f"e0_{n}", "categories": ["o0"],
                 "confidence": 0.95}
                for n in range(2)
            ],
        }
        lex_path = tmp_path / "lex.curated.json"
        ov_path = tmp_path / "ov.curated.json"
        lex_path.write_text(json.dumps(lexical), encoding="utf-8")
        ov_path.write_text(json.dumps(overlay), encoding="utf-8")

        builder = SnapshotBuilder(repos, now=NOW)
        builder.import_provider(CuratedJSONProvider(lex_path, name="lex", now=NOW))
        builder.import_provider(
            CuratedJSONProvider(ov_path, name="ov", now=NOW),
            taxonomy="overlay",
            entity_identity="lemma",
        )
        builder.activate(ActivationPolicy())
        return ContentService(repos)

    def query(self, **changes) -> ContentQuery:
        base = {
            "operation": Operation.FIND_GROUPS,
            "group_size": 5,
            "limit": 10,
        }
        base.update(changes)
        return ContentQuery(**base)

    def test_without_the_constraint_groups_come_from_every_category(
        self, repos, tmp_path
    ):
        """Six members give six combinations of five per category, so the
        limit fills before the categories run out."""
        service = self.seeded(repos, tmp_path)
        groups = service.execute(self.query()).groups
        assert {g.shared_category for g in groups} == {"cat0", "cat1", "cat2"}

    def test_with_it_only_groups_carrying_the_second_meaning_are(
        self, repos, tmp_path
    ):
        service = self.seeded(repos, tmp_path)
        result = service.execute(self.query(intersects_taxonomy="overlay"))
        assert result.groups
        assert {g.shared_category for g in result.groups} == {"cat0"}

    def test_the_rejection_is_named(self, repos, tmp_path):
        service = self.seeded(repos, tmp_path)
        result = service.execute(self.query(intersects_taxonomy="overlay"))
        assert result.rejected.get("NOT_IN_REQUIRED_TAXONOMY")

    def test_one_member_is_enough_by_default(self, repos, tmp_path):
        """Which is what a board needs: each visible group gives up one tile
        to the hidden group, not all five."""
        service = self.seeded(repos, tmp_path)
        assert self.query().minimum_intersecting_members == 1
        assert service.execute(self.query(intersects_taxonomy="overlay")).groups

    def test_demanding_more_than_exist_finds_nothing(self, repos, tmp_path):
        """Requiring every member is a much stronger claim: no lexical
        category in the real snapshot contains even three overlay words."""
        service = self.seeded(repos, tmp_path)
        result = service.execute(
            self.query(intersects_taxonomy="overlay", minimum_intersecting_members=5)
        )
        assert result.groups == ()

    def test_an_unknown_taxonomy_excludes_everything(self, repos, tmp_path):
        service = self.seeded(repos, tmp_path)
        result = service.execute(self.query(intersects_taxonomy="nowhere"))
        assert result.groups == ()
