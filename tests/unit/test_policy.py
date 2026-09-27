from __future__ import annotations

import pytest

from puzzlegen.content.policy import (
    DEFAULT_PLATFORM_POLICY,
    ContentPolicy,
    PolicyOutcome,
    PolicyService,
)
from puzzlegen.content.similarity import TaxonomyIndex
from puzzlegen.core.types import (
    FreshnessClass,
    ProvenanceClass,
    ReviewStatus,
)
from puzzlegen.graph.models import Provenance

from ..conftest import NOW, make_category, make_entity, make_fact


@pytest.fixture
def taxonomy(curated_source):
    animal = make_category("animal", curated_source)
    mammal = make_category("mammal", curated_source, parent=animal)
    feline = make_category("feline", curated_source, parent=mammal)
    bird = make_category("bird", curated_source, parent=animal)
    return {
        "index": TaxonomyIndex([animal, mammal, feline, bird]),
        "animal": animal,
        "mammal": mammal,
        "feline": feline,
        "bird": bird,
    }


class TestPolicyNarrowing:
    def test_denials_union(self):
        a = ContentPolicy(name="a", denied_ids=frozenset({"entity:x"}))
        b = ContentPolicy(name="b", denied_ids=frozenset({"entity:y"}))
        assert a.narrow(b).denied_ids == frozenset({"entity:x", "entity:y"})

    def test_confidence_takes_the_higher_floor(self):
        a = ContentPolicy(minimum_confidence=0.7)
        b = ContentPolicy(minimum_confidence=0.9)
        assert a.narrow(b).minimum_confidence == 0.9
        assert b.narrow(a).minimum_confidence == 0.9

    def test_an_empty_allow_list_yields_to_a_populated_one(self):
        a = ContentPolicy()
        b = ContentPolicy(allowed_categories=frozenset({"category:animal"}))
        assert a.narrow(b).allowed_categories == frozenset({"category:animal"})

    def test_two_allow_lists_intersect(self):
        a = ContentPolicy(allowed_categories=frozenset({"category:a", "category:b"}))
        b = ContentPolicy(allowed_categories=frozenset({"category:b", "category:c"}))
        assert a.narrow(b).allowed_categories == frozenset({"category:b"})

    def test_a_game_cannot_unblock_platform_content(self, taxonomy, curated_source):
        service = PolicyService(
            ContentPolicy(name="platform", denied_terms=frozenset({"tiger"})),
            taxonomy["index"],
        )
        # A game policy that denies nothing cannot admit the blocked term.
        service.register_game_policy("permissive", ContentPolicy(name="permissive"))
        tiger = make_entity("tiger", curated_source)
        assert not service.is_eligible(tiger, game_id="permissive").allowed

    def test_a_game_can_block_what_the_platform_allows(self, taxonomy, curated_source):
        service = PolicyService(ContentPolicy(name="platform"), taxonomy["index"])
        service.register_game_policy(
            "strict", ContentPolicy(denied_terms=frozenset({"tiger"}))
        )
        tiger = make_entity("tiger", curated_source)
        assert service.is_eligible(tiger).allowed
        assert not service.is_eligible(tiger, game_id="strict").allowed

    def test_registering_returns_the_narrowed_policy(self, taxonomy):
        service = PolicyService(
            ContentPolicy(name="platform", minimum_confidence=0.8), taxonomy["index"]
        )
        combined = service.register_game_policy(
            "g", ContentPolicy(minimum_confidence=0.5)
        )
        assert combined.minimum_confidence == 0.8

    def test_an_unregistered_game_falls_back_to_the_platform_policy(self, taxonomy):
        service = PolicyService(ContentPolicy(name="platform"), taxonomy["index"])
        assert service.policy_for("unknown").name == "platform"


class TestEligibility:
    def test_an_active_confident_entity_is_allowed(self, taxonomy, curated_source):
        service = PolicyService(taxonomy=taxonomy["index"])
        assert service.is_eligible(make_entity("tiger", curated_source)).allowed

    def test_a_non_active_record_is_blocked(self, taxonomy, curated_source):
        service = PolicyService(taxonomy=taxonomy["index"])
        candidate = make_entity(
            "tiger", curated_source, status=ReviewStatus.CANDIDATE
        )
        verdict = service.is_eligible(candidate)
        assert verdict.outcome is PolicyOutcome.BLOCK
        assert "status" in verdict.reason

    def test_a_low_confidence_record_is_blocked(self, taxonomy, curated_source):
        service = PolicyService(
            ContentPolicy(minimum_confidence=0.99), taxonomy["index"]
        )
        verdict = service.is_eligible(make_entity("tiger", curated_source))
        assert "confidence" in verdict.reason

    def test_an_explicit_id_denial_is_blocked(self, taxonomy, curated_source):
        tiger = make_entity("tiger", curated_source)
        service = PolicyService(
            ContentPolicy(denied_ids=frozenset({tiger.id})), taxonomy["index"]
        )
        assert not service.is_eligible(tiger).allowed

    def test_a_denied_term_blocks_an_alias_too(self, taxonomy, curated_source):
        service = PolicyService(
            ContentPolicy(denied_terms=frozenset({"panthera"})), taxonomy["index"]
        )
        tiger = make_entity("tiger", curated_source, aliases=("Panthera tigris",))
        assert not service.is_eligible(tiger).allowed

    def test_a_denied_pattern_blocks(self, taxonomy, curated_source):
        service = PolicyService(
            ContentPolicy(denied_patterns=(r"^tig",)), taxonomy["index"]
        )
        assert not service.is_eligible(make_entity("tiger", curated_source)).allowed

    def test_a_denied_predicate_blocks_a_fact(self, taxonomy, curated_source):
        service = PolicyService(
            ContentPolicy(denied_predicates=frozenset({"habitat"})), taxonomy["index"]
        )
        tiger = make_entity("tiger", curated_source)
        fact = make_fact(tiger, "habitat", "forest", curated_source)
        assert not service.is_eligible(fact).allowed

    def test_a_denied_freshness_class_blocks(self, taxonomy, curated_source):
        service = PolicyService(
            ContentPolicy(
                denied_freshness_classes=frozenset({FreshnessClass.VOLATILE})
            ),
            taxonomy["index"],
        )
        tiger = make_entity("tiger", curated_source)
        volatile = make_fact(
            tiger, "population", 3900, curated_source, freshness=FreshnessClass.VOLATILE
        )
        assert not service.is_eligible(volatile).allowed

    def test_a_denied_provenance_class_blocks(self, taxonomy, curated_source):
        service = PolicyService(
            ContentPolicy(
                denied_provenance_classes=frozenset({ProvenanceClass.JUDGED})
            ),
            taxonomy["index"],
        )
        tiger = make_entity("tiger", curated_source)
        judged = tiger.model_copy(
            update={
                "provenance": (
                    Provenance(
                        source_id=curated_source.id,
                        provenance_class=ProvenanceClass.JUDGED,
                        retrieval_date=NOW,
                        reviewer="curator:a",
                        confidence=0.95,
                    ),
                )
            }
        )
        assert not service.is_eligible(judged).allowed

    def test_a_denied_category_blocks_its_whole_subtree(self, taxonomy, curated_source):
        service = PolicyService(
            ContentPolicy(denied_categories=frozenset({taxonomy["mammal"].id})),
            taxonomy["index"],
        )
        tiger = make_entity("tiger", curated_source)
        verdict = service.is_eligible(tiger, category_ids=[taxonomy["feline"].id])
        assert not verdict.allowed

    def test_a_sibling_subtree_is_unaffected(self, taxonomy, curated_source):
        service = PolicyService(
            ContentPolicy(denied_categories=frozenset({taxonomy["mammal"].id})),
            taxonomy["index"],
        )
        eagle = make_entity("eagle", curated_source)
        assert service.is_eligible(eagle, category_ids=[taxonomy["bird"].id]).allowed

    def test_an_allow_list_admits_only_its_subtree(self, taxonomy, curated_source):
        service = PolicyService(
            ContentPolicy(allowed_categories=frozenset({taxonomy["mammal"].id})),
            taxonomy["index"],
        )
        tiger = make_entity("tiger", curated_source)
        eagle = make_entity("eagle", curated_source)
        assert service.is_eligible(tiger, category_ids=[taxonomy["feline"].id]).allowed
        assert not service.is_eligible(
            eagle, category_ids=[taxonomy["bird"].id]
        ).allowed

    def test_an_allow_list_blocks_uncategorised_records(self, taxonomy, curated_source):
        service = PolicyService(
            ContentPolicy(allowed_categories=frozenset({taxonomy["mammal"].id})),
            taxonomy["index"],
        )
        verdict = service.is_eligible(make_entity("tiger", curated_source))
        assert not verdict.allowed
        assert "no category" in verdict.reason

    def test_a_taxonomy_restriction_blocks_a_foreign_category(
        self, taxonomy, curated_source
    ):
        service = PolicyService(
            ContentPolicy(allowed_taxonomies=frozenset({"wordnet"})),
            taxonomy["index"],
        )
        assert not service.is_eligible(taxonomy["feline"]).allowed

    def test_the_verdict_names_the_deciding_layer(self, taxonomy, curated_source):
        service = PolicyService(ContentPolicy(name="platform"), taxonomy["index"])
        service.register_game_policy(
            "strict", ContentPolicy(denied_terms=frozenset({"tiger"}))
        )
        verdict = service.is_eligible(
            make_entity("tiger", curated_source), game_id="strict"
        )
        assert verdict.source == "strict"


class TestBulkFiltering:
    def test_split_reports_reason_counts(self, taxonomy, curated_source):
        service = PolicyService(
            ContentPolicy(denied_terms=frozenset({"tiger", "lion"})),
            taxonomy["index"],
        )
        records = [
            make_entity(name, curated_source)
            for name in ("tiger", "lion", "eagle", "wolf")
        ]
        kept, reasons = service.filter_eligible(records)
        assert {e.canonical_name for e in kept} == {"eagle", "wolf"}
        assert sum(reasons.values()) == 2

    def test_categories_are_consulted_when_supplied(self, taxonomy, curated_source):
        service = PolicyService(
            ContentPolicy(denied_categories=frozenset({taxonomy["bird"].id})),
            taxonomy["index"],
        )
        tiger = make_entity("tiger", curated_source)
        eagle = make_entity("eagle", curated_source)
        mapping = {
            tiger.id: [taxonomy["feline"].id],
            eagle.id: [taxonomy["bird"].id],
        }
        kept, _ = service.filter_eligible(
            [tiger, eagle], categories_of=lambda r: mapping[r.id]
        )
        assert [e.canonical_name for e in kept] == ["tiger"]


class TestDefaultPolicy:
    def test_the_platform_floor_requires_active_status(self):
        assert DEFAULT_PLATFORM_POLICY.required_status == frozenset(
            {ReviewStatus.ACTIVE}
        )

    def test_the_platform_floor_sets_a_confidence_minimum(self):
        assert DEFAULT_PLATFORM_POLICY.minimum_confidence > 0
