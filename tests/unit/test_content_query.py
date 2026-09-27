from __future__ import annotations

import datetime as dt

import pytest

from puzzlegen.content.policy import ContentPolicy, PolicyService
from puzzlegen.content.port import ContentPort, PortBudget
from puzzlegen.content.query import (
    ContentQuery,
    ContentRequirement,
    Operation,
    resolve_queries,
)
from puzzlegen.content.service import ContentService
from puzzlegen.content.snapshots import SnapshotBuilder
from puzzlegen.core.errors import BoundaryViolationError
from puzzlegen.core.types import DifficultyBand, FrequencyBand, ReviewStatus
from puzzlegen.providers.curated import CuratedJSONProvider
from puzzlegen.providers.embeddings import DevHashEmbeddingProvider
from puzzlegen.providers.frequency import TableFrequencyProvider

from ..conftest import NOW

SEEDS = "content/seeds"

FREQUENCIES = {
    "tiger": 4.6, "lion": 4.9, "leopard": 4.2, "jaguar": 4.3, "cheetah": 4.1,
    "lynx": 3.4, "wolf": 4.8, "coyote": 3.9, "jackal": 3.2, "dingo": 3.1,
    "fox": 4.7, "dolphin": 4.4, "porpoise": 2.8, "narwhal": 2.6, "orca": 3.5,
    "eagle": 4.6, "falcon": 4.0, "hawk": 4.4, "osprey": 2.9, "albatross": 3.0,
    "puffin": 2.9, "gannet": 2.2, "petrel": 2.1,
}


@pytest.fixture
def service(repos):
    builder = SnapshotBuilder(repos, now=NOW)
    meta, _ = builder.build(
        "2026-09-26",
        providers=[(CuratedJSONProvider(f"{SEEDS}/animals.curated.json", now=NOW), {})],
        frequency=TableFrequencyProvider(
            FREQUENCIES, name="wordfreq", version="3.1", retrieved_at=NOW
        ),
        embeddings=DevHashEmbeddingProvider(now=NOW),
    )
    return ContentService(
        repos,
        policy=PolicyService(ContentPolicy(name="platform", minimum_confidence=0.7)),
        snapshot=meta,
        now=NOW,
    )


@pytest.fixture
def port(service):
    return ContentPort(service, game_id="grouping")


class TestQueryValidation:
    def test_limit_must_be_positive(self):
        with pytest.raises(ValueError):
            ContentQuery(operation=Operation.FIND_ENTITIES, limit=0)

    def test_limit_is_capped(self):
        with pytest.raises(ValueError):
            ContentQuery(operation=Operation.FIND_ENTITIES, limit=10_000)

    def test_similarity_bounds_are_checked(self):
        with pytest.raises(ValueError):
            ContentQuery(operation=Operation.FIND_GROUPS, minimum_similarity=1.5)

    def test_inverted_similarity_bounds_are_refused(self):
        with pytest.raises(ValueError):
            ContentQuery(
                operation=Operation.FIND_GROUPS,
                minimum_similarity=0.9,
                maximum_similarity=0.5,
            )

    def test_group_size_must_be_at_least_two(self):
        with pytest.raises(ValueError):
            ContentQuery(operation=Operation.FIND_GROUPS, group_size=1)

    def test_negative_taxonomy_depth_is_refused(self):
        with pytest.raises(ValueError):
            ContentQuery(operation=Operation.FIND_SIBLINGS, taxonomy_depth=-1)

    def test_duplicate_requirement_names_are_refused(self):
        query = ContentQuery(operation=Operation.FIND_ENTITIES)
        with pytest.raises(ValueError):
            resolve_queries(
                [
                    ContentRequirement(name="pool", query=query),
                    ContentRequirement(name="pool", query=query),
                ]
            )


class TestFindEntities:
    def test_returns_active_entities(self, service):
        result = service.execute(ContentQuery(operation=Operation.FIND_ENTITIES))
        assert len(result.entities) > 0
        assert all(v.entity_id.startswith("entity:") for v in result.entities)

    def test_results_are_deterministic(self, service):
        query = ContentQuery(operation=Operation.FIND_ENTITIES, limit=10)
        first = service.execute(query)
        second = service.execute(query)
        assert [e.entity_id for e in first.entities] == [
            e.entity_id for e in second.entities
        ]

    def test_scopes_to_a_category_subtree(self, service):
        result = service.execute(
            ContentQuery(operation=Operation.FIND_CATEGORY_MEMBERS, category="feline")
        )
        assert {v.name for v in result.entities} == {
            "tiger", "lion", "leopard", "jaguar", "cheetah", "lynx",
        }

    def test_a_parent_category_includes_descendants(self, service):
        result = service.execute(
            ContentQuery(operation=Operation.FIND_CATEGORY_MEMBERS, category="mammal")
        )
        names = {v.name for v in result.entities}
        assert {"tiger", "wolf", "dolphin"} <= names

    def test_an_unknown_category_returns_nothing(self, service):
        result = service.execute(
            ContentQuery(operation=Operation.FIND_ENTITIES, category="unicorn")
        )
        assert result.is_empty()

    def test_frequency_band_filters(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_ENTITIES_BY_FREQUENCY,
                frequency_band=FrequencyBand.COMMON,
            )
        )
        assert all(v.frequency_band is FrequencyBand.COMMON for v in result.entities)
        assert len(result.entities) > 0

    def test_difficulty_band_maps_onto_frequency(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_ENTITIES_BY_DIFFICULTY,
                difficulty_band=DifficultyBand.EXPERT,
            )
        )
        assert all(
            v.frequency_band in (FrequencyBand.RARE, FrequencyBand.OBSCURE)
            for v in result.entities
        )

    def test_excluded_ids_are_omitted(self, service):
        first = service.execute(
            ContentQuery(operation=Operation.FIND_ENTITIES, category="feline")
        )
        excluded = first.entities[0].entity_id
        second = service.execute(
            ContentQuery(
                operation=Operation.FIND_ENTITIES,
                category="feline",
                exclude_ids=frozenset({excluded}),
            )
        )
        assert excluded not in {v.entity_id for v in second.entities}

    def test_predicate_filters_to_asserting_entities(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_ENTITIES, predicate="top_speed_kph"
            )
        )
        assert {v.name for v in result.entities} == {
            "cheetah", "falcon", "lion", "wolf", "dolphin",
        }

    def test_predicate_value_narrows_further(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_ENTITIES,
                predicate="habitat",
                predicate_value="forest",
            )
        )
        assert {v.name for v in result.entities} == {"tiger", "wolf"}


class TestEntityViews:
    def test_a_view_carries_types_most_specific_first(self, service):
        result = service.execute(
            ContentQuery(operation=Operation.FIND_ENTITIES, category="feline")
        )
        tiger = next(v for v in result.entities if v.name == "tiger")
        assert tiger.types[0] == "feline"
        assert "animal" in tiger.types

    def test_a_view_flattens_facts_into_properties(self, service):
        result = service.execute(
            ContentQuery(operation=Operation.FIND_ENTITIES, category="feline")
        )
        tiger = next(v for v in result.entities if v.name == "tiger")
        assert tiger.properties["habitat"] == "forest"
        assert tiger.properties["conservation_status"] == "endangered"

    def test_a_view_exposes_no_provenance_or_source(self, service):
        result = service.execute(ContentQuery(operation=Operation.FIND_ENTITIES))
        view = result.entities[0]
        for forbidden in ("provenance", "source_id", "source_ref", "confidence"):
            assert not hasattr(view, forbidden)

    def test_judged_facts_do_not_reach_a_view(self, service):
        result = service.execute(
            ContentQuery(operation=Operation.FIND_ENTITIES, category="feline")
        )
        lynx = next(v for v in result.entities if v.name == "lynx")
        assert "cultural_prominence" not in lynx.properties

    def test_dependency_ids_cover_the_whole_view(self, service):
        result = service.execute(
            ContentQuery(operation=Operation.FIND_ENTITIES, category="feline")
        )
        tiger = next(v for v in result.entities if v.name == "tiger")
        ids = tiger.dependency_ids()
        assert tiger.entity_id in ids
        assert all(fid in ids for fid in tiger.fact_ids())
        assert all(cid in ids for cid in tiger.type_ids)


class TestFindSiblings:
    def test_returns_members_of_the_named_category(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_SIBLINGS, category="canine", taxonomy_depth=0
            )
        )
        assert {v.name for v in result.entities} == {
            "wolf", "coyote", "jackal", "dingo", "fox",
        }

    def test_a_similarity_floor_rejects_distant_members(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_SIBLINGS,
                category="mammal",
                taxonomy_depth=1,
                minimum_similarity=0.99,
            )
        )
        assert result.is_empty()
        assert any("SEMANTIC" in reason for reason in result.rejected)


class TestFindGroups:
    def test_forms_groups_from_one_category(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_GROUPS,
                category="feline",
                group_size=4,
                limit=5,
            )
        )
        assert len(result.groups) == 5
        assert all(len(g.members) == 4 for g in result.groups)
        assert all(g.shared_category == "feline" for g in result.groups)

    def test_group_statistics_are_populated(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_GROUPS, category="feline", group_size=4, limit=1
            )
        )
        group = result.groups[0]
        assert group.similarity_strategy == "wu_palmer"
        assert group.minimum_similarity == pytest.approx(1.0)

    def test_groups_are_deterministic(self, service):
        query = ContentQuery(
            operation=Operation.FIND_GROUPS, category="feline", group_size=4, limit=3
        )
        first = service.execute(query)
        second = service.execute(query)
        assert [
            tuple(m.entity_id for m in g.members) for g in first.groups
        ] == [tuple(m.entity_id for m in g.members) for g in second.groups]

    def test_a_frequency_spread_cap_rejects_mixed_groups(self, service):
        wide = service.execute(
            ContentQuery(
                operation=Operation.FIND_GROUPS,
                category="bird",
                group_size=4,
                limit=50,
            )
        )
        narrow = service.execute(
            ContentQuery(
                operation=Operation.FIND_GROUPS,
                category="bird",
                group_size=4,
                maximum_frequency_spread=0,
                limit=50,
            )
        )
        assert len(narrow.groups) < len(wide.groups)
        assert all(g.frequency_spread == 0 for g in narrow.groups)

    def test_a_cross_category_group_is_rejected_by_the_similarity_floor(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_GROUPS,
                category="animal",
                group_size=4,
                minimum_similarity=0.99,
                limit=10,
            )
        )
        assert all(
            len({m.types[0] for m in g.members}) == 1 for g in result.groups
        )

    def test_an_insufficient_pool_is_reported(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_GROUPS,
                category="cetacean",
                group_size=4,
                minimum_candidate_count=20,
            )
        )
        assert not result.satisfied
        assert "INSUFFICIENT_CANDIDATES" in result.rejected

    def test_find_pairs_produces_two_member_groups(self, service):
        result = service.execute(
            ContentQuery(operation=Operation.FIND_PAIRS, category="feline", limit=3)
        )
        assert all(len(g.members) == 2 for g in result.groups)

    def test_group_dependency_ids_include_the_shared_category(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_GROUPS, category="feline", group_size=4, limit=1
            )
        )
        group = result.groups[0]
        assert group.shared_category_id in group.dependency_ids()


class TestIntersectingGroups:
    def test_a_second_axis_is_attached(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_INTERSECTING_GROUPS,
                category="feline",
                group_size=2,
                secondary_predicate="conservation_status",
                limit=10,
            )
        )
        assert result.groups
        assert all(
            g.secondary_category.startswith("conservation_status=")
            for g in result.groups
        )

    def test_members_disagreeing_on_the_axis_are_rejected(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_INTERSECTING_GROUPS,
                category="feline",
                group_size=2,
                secondary_predicate="conservation_status",
                limit=50,
            )
        )
        for group in result.groups:
            values = {m.properties["conservation_status"] for m in group.members}
            assert len(values) == 1

    def test_a_missing_axis_is_counted(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_INTERSECTING_GROUPS,
                category="canine",
                group_size=2,
                secondary_predicate="conservation_status",
                limit=10,
            )
        )
        assert result.rejected.get("NO_SECOND_AXIS", 0) > 0


class TestRelationships:
    def test_related_entities_follow_an_edge(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_RELATED_ENTITIES,
                category="orca",
                relationship_type="preys_on",
            )
        )
        assert {v.name for v in result.entities} == {"porpoise"}
        assert result.relationships[0].predicate == "preys_on"

    def test_membership_edges_are_never_returned_as_relations(self, service):
        result = service.execute(
            ContentQuery(operation=Operation.FIND_RELATED_ENTITIES, category="tiger")
        )
        assert all(r.predicate != "is_a" for r in result.relationships)

    def test_relationship_paths_are_found(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_RELATIONSHIP_PATTERNS,
                category="tiger",
                limit=5,
            )
        )
        assert result.paths
        assert all(path.length >= 1 for path in result.paths)

    def test_paths_arrive_shortest_first(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_RELATIONSHIP_PATTERNS,
                category="wolf",
                limit=10,
            )
        )
        lengths = [p.length for p in result.paths]
        assert lengths == sorted(lengths)

    def test_paths_never_revisit_a_node(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_RELATIONSHIP_PATTERNS,
                category="wolf",
                limit=10,
            )
        )
        for path in result.paths:
            ids = [n.entity_id for n in path.nodes]
            assert len(ids) == len(set(ids))

    def test_path_dependency_ids_include_edges(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_RELATIONSHIP_PATTERNS,
                category="tiger",
                limit=1,
            )
        )
        path = result.paths[0]
        assert all(e.relationship_id in path.dependency_ids() for e in path.edges)


class TestGates:
    def test_inactive_entities_never_appear(self, service, repos):
        tiger = repos.entities.by_name("tiger")[0]
        repos.entities.put(
            tiger.model_copy(update={"status": ReviewStatus.QUARANTINED})
        )
        result = service.execute(
            ContentQuery(operation=Operation.FIND_ENTITIES, category="feline")
        )
        assert "tiger" not in {v.name for v in result.entities}

    def test_a_stale_fact_is_dropped_from_a_view(self, repos):
        builder = SnapshotBuilder(repos, now=NOW)
        meta, _ = builder.build(
            "2026-09-26",
            providers=[
                (CuratedJSONProvider(f"{SEEDS}/animals.curated.json", now=NOW), {})
            ],
        )
        much_later = NOW + dt.timedelta(days=4000)
        service = ContentService(repos, snapshot=meta, now=much_later)
        result = service.execute(
            ContentQuery(operation=Operation.FIND_ENTITIES, category="feline")
        )
        tiger = next(v for v in result.entities if v.name == "tiger")
        # conservation_status is PERIODIC and long overdue; habitat is STATIC.
        assert "conservation_status" not in tiger.properties
        assert "habitat" in tiger.properties

    def test_fresh_only_false_admits_a_stale_fact(self, repos):
        builder = SnapshotBuilder(repos, now=NOW)
        meta, _ = builder.build(
            "2026-09-26",
            providers=[
                (CuratedJSONProvider(f"{SEEDS}/animals.curated.json", now=NOW), {})
            ],
        )
        much_later = NOW + dt.timedelta(days=4000)
        service = ContentService(repos, snapshot=meta, now=much_later)
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_ENTITIES, category="feline", fresh_only=False
            )
        )
        tiger = next(v for v in result.entities if v.name == "tiger")
        assert "conservation_status" in tiger.properties

    def test_a_deprecated_source_blocks_its_records(self, service, repos):
        source = next(iter(repos.sources.iter_all()))
        repos.sources.put(source.model_copy(update={"deprecated": True}))
        result = service.execute(ContentQuery(operation=Operation.FIND_ENTITIES))
        assert result.is_empty()
        assert "DEPRECATED_SOURCE" in result.rejected

    def test_a_game_policy_narrows_the_pool(self, service):
        service.policy.register_game_policy(
            "tame", ContentPolicy(denied_terms=frozenset({"tiger"}))
        )
        broad = service.execute(
            ContentQuery(operation=Operation.FIND_ENTITIES, category="feline")
        )
        narrow = service.execute(
            ContentQuery(operation=Operation.FIND_ENTITIES, category="feline"),
            game_id="tame",
        )
        assert "tiger" in {v.name for v in broad.entities}
        assert "tiger" not in {v.name for v in narrow.entities}


class TestContentPort:
    def test_exposes_only_semantic_operations(self, port):
        public = {n for n in dir(port) if not n.startswith("_")}
        assert not {"repos", "store", "service", "execute", "sql"} & public

    def test_a_query_returns_views(self, port):
        result = port.find_entities(category="feline")
        assert len(result.entities) == 6

    def test_unknown_constraints_are_refused(self, port):
        with pytest.raises(BoundaryViolationError):
            port.find_entities(table="entities")

    def test_the_query_budget_is_enforced(self, service):
        port = ContentPort(
            service, game_id="greedy", budget=PortBudget(max_queries=2)
        )
        port.find_entities(category="feline")
        port.find_entities(category="canine")
        with pytest.raises(BoundaryViolationError):
            port.find_entities(category="bird")

    def test_an_oversized_limit_is_clamped_not_rejected(self, service):
        port = ContentPort(
            service, game_id="eager", budget=PortBudget(max_results_per_query=3)
        )
        result = port.find_entities(category="feline", limit=50)
        assert len(result.entities) == 3

    def test_the_total_result_budget_is_enforced(self, service):
        port = ContentPort(
            service, game_id="greedy", budget=PortBudget(max_total_results=2)
        )
        with pytest.raises(BoundaryViolationError):
            port.find_entities(category="feline")

    def test_usage_is_recorded(self, port):
        port.find_entities(category="feline")
        port.find_groups(category="canine", group_size=3, limit=2)
        assert port.usage.queries == 2
        assert port.usage.operations["find_entities"] == 1

    def test_issued_ids_accumulate(self, port):
        port.find_entities(category="feline")
        assert any(i.startswith("entity:") for i in port.issued_ids())

    def test_fabricated_references_are_detected(self, port):
        result = port.find_entities(category="feline")
        real = result.entities[0].entity_id
        assert port.verify_references([real]) == ()
        assert port.verify_references([real, "entity:invented"]) == ("entity:invented",)

    def test_requirements_are_satisfied_in_one_batch(self, port):
        requirements = [
            ContentRequirement(
                name="cats",
                query=ContentQuery(
                    operation=Operation.FIND_CATEGORY_MEMBERS, category="feline"
                ),
                minimum=4,
            ),
            ContentRequirement(
                name="dogs",
                query=ContentQuery(
                    operation=Operation.FIND_CATEGORY_MEMBERS, category="canine"
                ),
                minimum=4,
            ),
        ]
        results = port.satisfy(requirements)
        assert set(results) == {"cats", "dogs"}
        assert all(r.satisfied for r in results.values())

    def test_an_unmet_requirement_is_reported_not_raised(self, port):
        requirement = ContentRequirement(
            name="impossible",
            query=ContentQuery(
                operation=Operation.FIND_CATEGORY_MEMBERS, category="cetacean"
            ),
            minimum=99,
        )
        result = port.satisfy([requirement])["impossible"]
        assert not result.satisfied
        assert "needs 99" in result.detail

    def test_an_optional_requirement_tolerates_emptiness(self, port):
        requirement = ContentRequirement(
            name="maybe",
            query=ContentQuery(
                operation=Operation.FIND_CATEGORY_MEMBERS, category="unicorn"
            ),
            minimum=5,
            optional=True,
        )
        assert requirement.is_met(port.satisfy([requirement])["maybe"])

    def test_the_port_applies_the_game_policy(self, service):
        service.policy.register_game_policy(
            "tame", ContentPolicy(denied_terms=frozenset({"tiger"}))
        )
        port = ContentPort(service, game_id="tame")
        result = port.find_entities(category="feline")
        assert "tiger" not in {v.name for v in result.entities}
