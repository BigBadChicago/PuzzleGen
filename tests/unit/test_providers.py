from __future__ import annotations

import datetime as dt
import json

import pytest

from puzzlegen.core.types import FrequencyBand, ProvenanceClass, SourceKind
from puzzlegen.providers.base import (
    ProviderBundle,
    ProviderDescriptor,
    ProviderError,
    RawCategory,
    RawEntity,
    RawFact,
    RawRelationship,
    order_categories,
    validate_bundle,
)
from puzzlegen.providers.curated import CuratedJSONProvider
from puzzlegen.providers.embeddings import (
    DEV_MODEL_NAME,
    DevHashEmbeddingProvider,
    TableEmbeddingProvider,
    centroid,
    cosine,
    normalize,
)
from puzzlegen.providers.frequency import (
    NullFrequencyProvider,
    TableFrequencyProvider,
    band_distance,
    band_for_zipf,
    band_spread,
)
from puzzlegen.providers.wordnet import WordNetLexiconProvider

from ..conftest import NOW

SEEDS = "content/seeds"


def descriptor(name: str = "test") -> ProviderDescriptor:
    return ProviderDescriptor(
        name=name, kind=SourceKind.CURATED_INTERNAL, version="1", retrieved_at=NOW
    )


class TestBundleValidation:
    def test_a_clean_bundle_has_no_problems(self):
        bundle = ProviderBundle(
            descriptor=descriptor(),
            categories=(RawCategory(key="c", name="cat"),),
            entities=(RawEntity(key="e", name="tiger", category_keys=("c",)),),
            facts=(RawFact(subject_key="e", predicate="habitat", value="forest"),),
            relationships=(
                RawRelationship(
                    subject_key="e", predicate="is_a", object_key="c",
                    object_is_category=True,
                ),
            ),
        )
        assert validate_bundle(bundle) == []

    def test_unknown_parent_is_reported(self):
        bundle = ProviderBundle(
            descriptor=descriptor(),
            categories=(RawCategory(key="c", name="cat", parent_keys=("missing",)),),
        )
        assert any("unknown parent" in p for p in validate_bundle(bundle))

    def test_unknown_fact_subject_is_reported(self):
        bundle = ProviderBundle(
            descriptor=descriptor(),
            facts=(RawFact(subject_key="ghost", predicate="p", value=1),),
        )
        assert any("unknown subject" in p for p in validate_bundle(bundle))

    def test_computed_fact_without_derivation_is_reported(self):
        bundle = ProviderBundle(
            descriptor=descriptor(),
            entities=(RawEntity(key="e", name="tiger"),),
            facts=(
                RawFact(
                    subject_key="e",
                    predicate="p",
                    value=1,
                    provenance_class=ProvenanceClass.COMPUTED,
                ),
            ),
        )
        assert any("records no derivation" in p for p in validate_bundle(bundle))

    def test_judged_fact_without_reviewer_is_reported(self):
        bundle = ProviderBundle(
            descriptor=descriptor(),
            entities=(RawEntity(key="e", name="tiger"),),
            facts=(
                RawFact(
                    subject_key="e",
                    predicate="p",
                    value=1,
                    provenance_class=ProvenanceClass.JUDGED,
                ),
            ),
        )
        assert any("names no reviewer" in p for p in validate_bundle(bundle))

    def test_duplicate_keys_are_reported(self):
        bundle = ProviderBundle(
            descriptor=descriptor(),
            entities=(RawEntity(key="e", name="a"), RawEntity(key="e", name="b")),
        )
        assert "duplicate entity keys" in validate_bundle(bundle)

    def test_relationship_loop_is_reported(self):
        bundle = ProviderBundle(
            descriptor=descriptor(),
            entities=(RawEntity(key="e", name="tiger"),),
            relationships=(
                RawRelationship(subject_key="e", predicate="p", object_key="e"),
            ),
        )
        assert any("loops on" in p for p in validate_bundle(bundle))


class TestCategoryOrdering:
    def test_parents_come_before_children(self):
        categories = [
            RawCategory(key="c", name="c", parent_keys=("b",)),
            RawCategory(key="b", name="b", parent_keys=("a",)),
            RawCategory(key="a", name="a"),
        ]
        assert [c.key for c in order_categories(categories)] == ["a", "b", "c"]

    def test_forked_ancestry_is_ordered(self):
        categories = [
            RawCategory(key="d", name="d", parent_keys=("b", "c")),
            RawCategory(key="b", name="b", parent_keys=("a",)),
            RawCategory(key="c", name="c", parent_keys=("a",)),
            RawCategory(key="a", name="a"),
        ]
        ordered = [c.key for c in order_categories(categories)]
        assert ordered.index("d") > max(ordered.index("b"), ordered.index("c"))

    def test_ordering_is_deterministic(self):
        categories = [
            RawCategory(key=k, name=k) for k in ("z", "y", "x", "w")
        ]
        assert order_categories(categories) == order_categories(list(reversed(categories)))

    def test_a_cycle_is_refused(self):
        categories = [
            RawCategory(key="a", name="a", parent_keys=("b",)),
            RawCategory(key="b", name="b", parent_keys=("a",)),
        ]
        with pytest.raises(ProviderError):
            order_categories(categories)


class TestCuratedProvider:
    def test_loads_the_seed_file(self):
        provider = CuratedJSONProvider(f"{SEEDS}/animals.curated.json", now=NOW)
        bundle = provider.load()
        assert validate_bundle(bundle) == []
        assert bundle.counts()["entities"] == 23
        assert bundle.descriptor.kind is SourceKind.CURATED_INTERNAL

    def test_reports_freshness_from_its_declared_update_date(self):
        provider = CuratedJSONProvider(f"{SEEDS}/animals.curated.json", now=NOW)
        status = provider.freshness_status()
        assert status.healthy and status.age_days is not None

    def test_an_old_curated_file_is_reported_unhealthy(self, tmp_path):
        path = tmp_path / "old.json"
        path.write_text(json.dumps({"version": "1", "updated": "2020-01-01"}))
        assert not CuratedJSONProvider(path, now=NOW).freshness_status().healthy

    def test_fetch_entity_finds_a_key(self):
        provider = CuratedJSONProvider(f"{SEEDS}/animals.curated.json", now=NOW)
        assert provider.fetch_entity("e.tiger").name == "tiger"
        assert provider.fetch_entity("e.nothing") is None

    def test_missing_file_is_refused(self, tmp_path):
        with pytest.raises(ProviderError):
            CuratedJSONProvider(tmp_path / "absent.json").load()

    def test_malformed_json_is_refused(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        with pytest.raises(ProviderError):
            CuratedJSONProvider(path).load()

    def test_unsupported_schema_is_refused(self, tmp_path):
        path = tmp_path / "future.json"
        path.write_text(json.dumps({"curated_schema": 99}))
        with pytest.raises(ProviderError):
            CuratedJSONProvider(path).load()

    def test_missing_required_field_is_refused(self, tmp_path):
        path = tmp_path / "partial.json"
        path.write_text(json.dumps({"entities": [{"name": "tiger"}]}))
        with pytest.raises(ProviderError):
            CuratedJSONProvider(path).load()

    def test_judged_facts_carry_their_reviewer(self):
        bundle = CuratedJSONProvider(f"{SEEDS}/animals.curated.json", now=NOW).load()
        judged = [
            f for f in bundle.facts if f.provenance_class is ProvenanceClass.JUDGED
        ]
        assert judged and all(f.reviewer for f in judged)


class TestWordNetProvider:
    def test_loads_the_lexicon(self):
        provider = WordNetLexiconProvider(f"{SEEDS}/wordnet-mini.lexicon.json", now=NOW)
        bundle = provider.load()
        assert validate_bundle(bundle) == []
        assert bundle.descriptor.kind is SourceKind.LEXICAL

    def test_forked_ancestry_is_preserved(self):
        bundle = WordNetLexiconProvider(
            f"{SEEDS}/wordnet-mini.lexicon.json", now=NOW
        ).load()
        ailurid = next(c for c in bundle.categories if c.key == "s.ailurid")
        assert set(ailurid.parent_keys) == {"s.procyonid", "s.bear_family"}

    def test_depth_hints_are_carried_through(self):
        bundle = WordNetLexiconProvider(
            f"{SEEDS}/wordnet-mini.lexicon.json", now=NOW
        ).load()
        ailurid = next(c for c in bundle.categories if c.key == "s.ailurid")
        assert ailurid.depth_hint == (4, 4)

    def test_roots_carry_no_depth_hint(self):
        bundle = WordNetLexiconProvider(
            f"{SEEDS}/wordnet-mini.lexicon.json", now=NOW
        ).load()
        root = next(c for c in bundle.categories if c.key == "s.entity")
        assert root.parent_keys == () and root.depth_hint is None

    def test_hypernyms_outside_the_export_are_dropped(self):
        bundle = WordNetLexiconProvider(
            f"{SEEDS}/wordnet-mini.lexicon.json", now=NOW
        ).load()
        dangling = next(c for c in bundle.categories if c.key == "s.dangling")
        assert dangling.parent_keys == ()

    def test_senses_without_a_synset_are_dropped(self):
        bundle = WordNetLexiconProvider(
            f"{SEEDS}/wordnet-mini.lexicon.json", now=NOW
        ).load()
        assert all(e.key != "sense.orphan" for e in bundle.entities)

    def test_membership_becomes_a_relationship(self):
        bundle = WordNetLexiconProvider(
            f"{SEEDS}/wordnet-mini.lexicon.json", now=NOW
        ).load()
        rel = next(r for r in bundle.relationships if r.subject_key == "sense.red_panda")
        assert rel.object_key == "s.ailurid" and rel.object_is_category

    def test_missing_lexicon_is_refused(self, tmp_path):
        with pytest.raises(ProviderError):
            WordNetLexiconProvider(tmp_path / "absent.json").load()

    def test_incomplete_lexicon_is_refused(self, tmp_path):
        path = tmp_path / "partial.json"
        path.write_text(json.dumps({"lexicon": "x", "version": "1"}))
        with pytest.raises(ProviderError):
            WordNetLexiconProvider(path).load()


class TestFrequency:
    @pytest.mark.parametrize(
        "zipf,expected",
        [
            (7.0, FrequencyBand.VERY_COMMON),
            (5.0, FrequencyBand.VERY_COMMON),
            (4.4, FrequencyBand.COMMON),
            (3.1, FrequencyBand.UNCOMMON),
            (2.0, FrequencyBand.RARE),
            (0.4, FrequencyBand.OBSCURE),
        ],
    )
    def test_banding_boundaries(self, zipf, expected):
        assert band_for_zipf(zipf) is expected

    def test_band_distance_is_symmetric(self):
        a, b = FrequencyBand.VERY_COMMON, FrequencyBand.RARE
        assert band_distance(a, b) == band_distance(b, a) == 3

    def test_band_spread_of_a_uniform_group_is_zero(self):
        assert band_spread([FrequencyBand.COMMON] * 4) == 0

    def test_band_spread_finds_the_widest_gap(self):
        bands = [FrequencyBand.VERY_COMMON, FrequencyBand.COMMON, FrequencyBand.RARE]
        assert band_spread(bands) == 3

    def test_empty_spread_is_zero(self):
        assert band_spread([]) == 0

    def test_table_provider_scores_and_bands(self):
        provider = TableFrequencyProvider(
            {"tiger": 4.6}, name="wordfreq", version="3.1", retrieved_at=NOW
        )
        score = provider.score("Tiger")
        assert score.zipf == 4.6 and score.band is FrequencyBand.COMMON

    def test_unknown_term_scores_none(self):
        provider = TableFrequencyProvider(
            {"tiger": 4.6}, name="wordfreq", version="3.1", retrieved_at=NOW
        )
        assert provider.score("quokka") is None

    def test_multi_word_terms_take_their_rarest_component(self):
        provider = TableFrequencyProvider(
            {"red": 5.5, "panda": 3.4},
            name="wordfreq",
            version="3.1",
            retrieved_at=NOW,
        )
        assert provider.score("red panda").zipf == 3.4

    def test_multi_word_term_with_an_unknown_component_scores_none(self):
        provider = TableFrequencyProvider(
            {"red": 5.5}, name="wordfreq", version="3.1", retrieved_at=NOW
        )
        assert provider.score("red quokka") is None

    def test_other_languages_are_not_scored(self):
        provider = TableFrequencyProvider(
            {"tiger": 4.6}, name="wordfreq", version="3.1", retrieved_at=NOW
        )
        assert provider.score("tiger", lang="fr") is None

    def test_null_provider_scores_nothing_but_still_describes_itself(self):
        provider = NullFrequencyProvider(NOW)
        assert provider.score("tiger") is None
        assert provider.describe().name == "none"


class TestEmbeddings:
    def test_cosine_is_rescaled_onto_the_unit_interval(self):
        assert cosine((1.0, 0.0), (1.0, 0.0)) == pytest.approx(1.0)
        assert cosine((1.0, 0.0), (-1.0, 0.0)) == pytest.approx(0.0)
        assert cosine((1.0, 0.0), (0.0, 1.0)) == pytest.approx(0.5)

    def test_cosine_refuses_mismatched_dimensions(self):
        with pytest.raises(ValueError):
            cosine((1.0, 0.0), (1.0, 0.0, 0.0))

    def test_cosine_refuses_a_zero_vector(self):
        with pytest.raises(ValueError):
            cosine((0.0, 0.0), (1.0, 0.0))

    def test_centroid_averages(self):
        assert centroid([(0.0, 2.0), (2.0, 0.0)]) == (1.0, 1.0)

    def test_centroid_refuses_an_empty_group(self):
        with pytest.raises(ValueError):
            centroid([])

    def test_normalize_produces_unit_length(self):
        vector = normalize((3.0, 4.0))
        assert sum(x * x for x in vector) == pytest.approx(1.0)

    def test_dev_embeddings_are_deterministic(self):
        a = DevHashEmbeddingProvider(now=NOW).embed(["tiger"])
        b = DevHashEmbeddingProvider(now=NOW).embed(["tiger"])
        assert a == b

    def test_dev_embeddings_differ_by_text(self):
        provider = DevHashEmbeddingProvider(now=NOW)
        assert provider.embed(["tiger"]) != provider.embed(["lion"])

    def test_dev_embeddings_are_unit_length_and_correctly_sized(self):
        vector = DevHashEmbeddingProvider(dimensions=48, now=NOW).embed(["tiger"])[0]
        assert len(vector) == 48
        assert sum(x * x for x in vector) == pytest.approx(1.0)

    def test_dev_provider_labels_itself_as_development_only(self):
        descriptor = DevHashEmbeddingProvider(now=NOW).describe()
        assert descriptor.is_development_only
        assert descriptor.model_name == DEV_MODEL_NAME

    def test_table_provider_serves_and_normalizes(self):
        provider = TableEmbeddingProvider(
            {"tiger": [3.0, 4.0]},
            model_name="mini",
            model_version="1",
            computed_at=NOW,
        )
        vector = provider.embed(["tiger"])[0]
        assert sum(x * x for x in vector) == pytest.approx(1.0)
        assert not provider.describe().is_development_only

    def test_table_provider_refuses_an_unknown_text(self):
        provider = TableEmbeddingProvider(
            {"tiger": [1.0, 0.0]},
            model_name="mini",
            model_version="1",
            computed_at=NOW,
        )
        with pytest.raises(KeyError):
            provider.embed(["lion"])

    def test_table_provider_refuses_inconsistent_dimensions(self):
        with pytest.raises(ValueError):
            TableEmbeddingProvider(
                {"a": [1.0, 0.0], "b": [1.0]},
                model_name="mini",
                model_version="1",
                computed_at=NOW,
            )

    def test_table_provider_refuses_an_empty_table(self):
        with pytest.raises(ValueError):
            TableEmbeddingProvider(
                {}, model_name="mini", model_version="1", computed_at=NOW
            )


class TestDescriptorValidation:
    def test_provider_descriptor_requires_an_aware_timestamp(self):
        with pytest.raises(ValueError):
            ProviderDescriptor(
                name="x",
                kind=SourceKind.LEXICAL,
                version="1",
                retrieved_at=dt.datetime(2026, 9, 26),
            )
