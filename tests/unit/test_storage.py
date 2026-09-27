"""Storage conformance.

Every test here runs against both backends via the parametrized ``store``
fixture. A behaviour that holds in memory but not in SQLite is a bug in the
backend, not a difference to be tolerated.
"""

from __future__ import annotations

import datetime as dt

import pytest

from puzzlegen.core import ids
from puzzlegen.core.errors import ConflictError, NotFoundError, SchemaVersionError, StorageError
from puzzlegen.core.types import (
    DependencyRefKind,
    FreshnessClass,
    FrequencyBand,
    ReviewStatus,
)
from puzzlegen.graph.repositories import GraphRepositories
from puzzlegen.graph.store import Query

from ..conftest import LATER, NOW, make_category, make_entity, make_fact, make_relationship


class TestQueryValidation:
    def test_negative_limit_is_refused(self):
        with pytest.raises(ValueError):
            Query(limit=-1)

    def test_negative_offset_is_refused(self):
        with pytest.raises(ValueError):
            Query(offset=-1)

    def test_empty_any_of_is_refused(self):
        with pytest.raises(ValueError):
            Query(any_of={"status": []})

    def test_is_empty_detects_an_unfiltered_query(self):
        assert Query().is_empty()
        assert not Query(equals={"status": "ACTIVE"}).is_empty()


class TestDocumentStoreContract:
    def test_undeclared_collection_is_refused(self, store):
        with pytest.raises(StorageError):
            store.get("nope", "x")

    def test_redeclaring_with_different_indexes_is_refused(self, store):
        store.declare_collection("things", ["a"])
        store.declare_collection("things", ["a"])
        with pytest.raises(StorageError):
            store.declare_collection("things", ["a", "b"])

    def test_undeclared_index_field_on_write_is_refused(self, store):
        store.declare_collection("things", ["a"])
        with pytest.raises(StorageError):
            store.put("things", "1", {"id": "1"}, {"b": ["x"]})

    def test_undeclared_index_field_on_query_is_refused(self, store):
        store.declare_collection("things", ["a"])
        with pytest.raises(StorageError):
            store.query("things", Query(equals={"b": "x"}))

    def test_absent_document_is_none(self, store):
        store.declare_collection("things", ["a"])
        assert store.get("things", "missing") is None

    def test_results_are_ordered_by_id(self, store):
        store.declare_collection("things", ["a"])
        for doc_id in ("c", "a", "b"):
            store.put("things", doc_id, {"id": doc_id}, {"a": ["x"]})
        found = store.query("things", Query(equals={"a": "x"}))
        assert [d["id"] for d in found] == ["a", "b", "c"]

    def test_iteration_is_ordered_by_id(self, store):
        store.declare_collection("things", [])
        for doc_id in ("c", "a", "b"):
            store.put("things", doc_id, {"id": doc_id}, {})
        assert [d["id"] for d in store.iter_all("things")] == ["a", "b", "c"]

    def test_put_replaces_and_reindexes(self, store):
        store.declare_collection("things", ["a"])
        store.put("things", "1", {"id": "1", "a": "x"}, {"a": ["x"]})
        store.put("things", "1", {"id": "1", "a": "y"}, {"a": ["y"]})
        assert store.query("things", Query(equals={"a": "x"})) == []
        assert len(store.query("things", Query(equals={"a": "y"}))) == 1

    def test_delete_reports_existence_and_clears_indexes(self, store):
        store.declare_collection("things", ["a"])
        store.put("things", "1", {"id": "1"}, {"a": ["x"]})
        assert store.delete("things", "1") is True
        assert store.delete("things", "1") is False
        assert store.query("things", Query(equals={"a": "x"})) == []

    def test_multi_valued_index_matches_any_value(self, store):
        store.declare_collection("things", ["name"])
        store.put("things", "1", {"id": "1"}, {"name": ["tiger", "panthera tigris"]})
        assert len(store.query("things", Query(equals={"name": "panthera tigris"}))) == 1

    def test_filters_are_conjunctive(self, store):
        store.declare_collection("things", ["a", "b"])
        store.put("things", "1", {"id": "1"}, {"a": ["x"], "b": ["p"]})
        store.put("things", "2", {"id": "2"}, {"a": ["x"], "b": ["q"]})
        found = store.query("things", Query(equals={"a": "x", "b": "q"}))
        assert [d["id"] for d in found] == ["2"]

    def test_any_of_is_disjunctive_within_a_field(self, store):
        store.declare_collection("things", ["a"])
        for doc_id, value in (("1", "x"), ("2", "y"), ("3", "z")):
            store.put("things", doc_id, {"id": doc_id}, {"a": [value]})
        found = store.query("things", Query(any_of={"a": ["x", "z"]}))
        assert [d["id"] for d in found] == ["1", "3"]

    def test_limit_and_offset_page_deterministically(self, store):
        store.declare_collection("things", ["a"])
        for i in range(10):
            store.put("things", f"{i:02d}", {"id": f"{i:02d}"}, {"a": ["x"]})
        page = store.query("things", Query(equals={"a": "x"}, limit=3, offset=3))
        assert [d["id"] for d in page] == ["03", "04", "05"]

    def test_offset_without_limit_is_honoured(self, store):
        store.declare_collection("things", ["a"])
        for i in range(5):
            store.put("things", f"{i}", {"id": f"{i}"}, {"a": ["x"]})
        page = store.query("things", Query(equals={"a": "x"}, offset=3))
        assert [d["id"] for d in page] == ["3", "4"]

    def test_get_many_skips_missing_ids(self, store):
        store.declare_collection("things", [])
        store.put("things", "1", {"id": "1"}, {})
        assert set(store.get_many("things", ["1", "absent"])) == {"1"}

    def test_get_many_with_no_ids_returns_empty(self, store):
        store.declare_collection("things", [])
        assert store.get_many("things", []) == {}

    def test_count_respects_filters(self, store):
        store.declare_collection("things", ["a"])
        store.put("things", "1", {"id": "1"}, {"a": ["x"]})
        store.put("things", "2", {"id": "2"}, {"a": ["y"]})
        assert store.count("things") == 2
        assert store.count("things", Query(equals={"a": "x"})) == 1

    def test_returned_documents_are_copies(self, store):
        store.declare_collection("things", [])
        store.put("things", "1", {"id": "1", "nested": {"k": "v"}}, {})
        fetched = store.get("things", "1")
        fetched["nested"]["k"] = "mutated"
        assert store.get("things", "1")["nested"]["k"] == "v"


class TestTransactions:
    def test_failed_transaction_rolls_back(self, store):
        store.declare_collection("things", ["a"])
        store.put("things", "keep", {"id": "keep"}, {"a": ["x"]})
        with pytest.raises(RuntimeError):
            with store.transaction():
                store.put("things", "discard", {"id": "discard"}, {"a": ["x"]})
                raise RuntimeError("boom")
        assert store.get("things", "discard") is None
        assert store.get("things", "keep") is not None

    def test_rollback_also_restores_indexes(self, store):
        store.declare_collection("things", ["a"])
        with pytest.raises(RuntimeError):
            with store.transaction():
                store.put("things", "1", {"id": "1"}, {"a": ["x"]})
                raise RuntimeError("boom")
        assert store.query("things", Query(equals={"a": "x"})) == []

    def test_successful_transaction_commits(self, store):
        store.declare_collection("things", ["a"])
        with store.transaction():
            store.put("things", "1", {"id": "1"}, {"a": ["x"]})
        assert store.get("things", "1") is not None

    def test_nested_transaction_joins_the_outer_scope(self, store):
        store.declare_collection("things", ["a"])
        with pytest.raises(RuntimeError):
            with store.transaction():
                with store.transaction():
                    store.put("things", "1", {"id": "1"}, {"a": ["x"]})
                raise RuntimeError("boom")
        assert store.get("things", "1") is None


class TestRepositories:
    def test_round_trips_a_record(self, repos, curated_source):
        repos.sources.put(curated_source)
        assert repos.sources.require(curated_source.id) == curated_source

    def test_require_raises_on_absence(self, repos):
        with pytest.raises(NotFoundError):
            repos.entities.require("entity:absent")

    def test_insert_refuses_to_overwrite(self, repos, curated_source):
        repos.sources.insert(curated_source)
        with pytest.raises(ConflictError):
            repos.sources.insert(curated_source)

    def test_put_many_is_atomic(self, repos, curated_source):
        repos.sources.put(curated_source)
        good = make_entity("tiger", curated_source)
        with pytest.raises(StorageError):
            with repos.transaction():
                repos.entities.put(good)
                raise StorageError("boom")
        assert repos.entities.get(good.id) is None

    def test_schema_version_mismatch_is_detected(self, repos, store, curated_source):
        repos.sources.put(curated_source)
        document = repos.sources.get(curated_source.id).model_dump(mode="json")
        document["schema_version"] = 99
        store.put("sources", curated_source.id, document, {"kind": ["LEXICAL"], "name": ["x"], "deprecated": ["false"]})
        with pytest.raises(SchemaVersionError):
            repos.sources.get(curated_source.id)

    def test_entity_lookup_by_alias(self, repos, curated_source):
        repos.sources.put(curated_source)
        tiger = make_entity("tiger", curated_source, aliases=("Panthera tigris",))
        repos.entities.put(tiger)
        assert repos.entities.by_name("panthera tigris") == [tiger]

    def test_entity_frequency_band_filter_excludes_inactive(self, repos, curated_source):
        repos.sources.put(curated_source)
        active = make_entity("tiger", curated_source, band=FrequencyBand.COMMON)
        candidate = make_entity(
            "quoll",
            curated_source,
            status=ReviewStatus.CANDIDATE,
            band=FrequencyBand.COMMON,
        )
        repos.entities.put_many([active, candidate])
        found = repos.entities.by_frequency_band(FrequencyBand.COMMON)
        assert [e.id for e in found] == [active.id]

    def test_active_and_by_status_agree(self, repos, curated_source):
        repos.sources.put(curated_source)
        repos.entities.put(make_entity("tiger", curated_source))
        assert repos.entities.active() == repos.entities.by_status(ReviewStatus.ACTIVE)

    def test_due_for_review_finds_overdue_records(self, repos, curated_source):
        repos.sources.put(curated_source)
        periodic = make_entity("tiger", curated_source, freshness=FreshnessClass.PERIODIC)
        repos.entities.put(periodic)
        assert repos.entities.due_for_review(NOW) == []
        assert repos.entities.due_for_review(LATER + dt.timedelta(days=1)) == [periodic]

    def test_fact_lookup_by_subject_and_predicate(self, repos, curated_source):
        repos.sources.put(curated_source)
        tiger = make_entity("tiger", curated_source)
        repos.entities.put(tiger)
        status = make_fact(tiger, "conservation_status", "endangered", curated_source)
        habitat = make_fact(tiger, "habitat", "grassland", curated_source)
        repos.facts.put_many([status, habitat])
        assert repos.facts.by_subject_predicate(tiger.id, "habitat") == [habitat]
        assert len(repos.facts.by_subject(tiger.id)) == 2

    def test_facts_are_findable_by_shared_value(self, repos, curated_source):
        repos.sources.put(curated_source)
        tiger = make_entity("tiger", curated_source)
        leopard = make_entity("leopard", curated_source)
        repos.entities.put_many([tiger, leopard])
        facts = [
            make_fact(tiger, "conservation_status", "endangered", curated_source),
            make_fact(leopard, "conservation_status", "endangered", curated_source),
        ]
        repos.facts.put_many(facts)
        found = repos.facts.subjects_with_value("conservation_status", "STRING:endangered")
        assert {f.subject_id for f in found} == {tiger.id, leopard.id}

    def test_relationship_endpoint_index_is_undirected(self, repos, curated_source):
        repos.sources.put(curated_source)
        tiger = make_entity("tiger", curated_source)
        feline = make_category("feline", curated_source)
        repos.entities.put(tiger)
        repos.categories.put(feline)
        rel = make_relationship(tiger, "is_a", feline, curated_source)
        repos.relationships.put(rel)
        assert repos.relationships.touching(feline.id) == [rel]
        assert repos.relationships.by_subject(tiger.id, "is_a") == [rel]
        assert repos.relationships.by_object(feline.id, "is_a") == [rel]

    def test_category_children_and_roots(self, repos, curated_source):
        repos.sources.put(curated_source)
        animal = make_category("animal", curated_source)
        feline = make_category("feline", curated_source, parent=animal)
        repos.categories.put_many([animal, feline])
        assert repos.categories.roots() == [animal]
        assert repos.categories.children(animal.id) == [feline]
        assert repos.categories.at_depth(1) == [feline]

    def test_category_ancestors_are_root_first(self, repos, curated_source):
        repos.sources.put(curated_source)
        animal = make_category("animal", curated_source)
        mammal = make_category("mammal", curated_source, parent=animal)
        feline = make_category("feline", curated_source, parent=mammal)
        repos.categories.put_many([animal, mammal, feline])
        assert [c.canonical_name for c in repos.categories.ancestors(feline.id)] == [
            "animal",
            "mammal",
        ]

    def test_record_counts_cover_every_collection(self, repos, curated_source):
        repos.sources.put(curated_source)
        counts = repos.record_counts()
        assert counts["sources"] == 1
        assert set(counts) >= {"entities", "facts", "relationships", "dependencies"}


class TestDependencyTracking:
    def test_blast_radius_aggregates_dependents(self, repos, curated_source):
        repos.sources.put(curated_source)
        tiger = make_entity("tiger", curated_source)
        repos.entities.put(tiger)
        for day, game in (("2026-09-26", "grouping"), ("2026-09-27", "chain")):
            puzzle_id = ids.for_puzzle(day, game, "hash")
            manifest_id = ids.for_manifest(puzzle_id)
            repos.dependencies.record_refs(
                manifest_id=manifest_id,
                puzzle_id=puzzle_id,
                game_id=game,
                day_key=day,
                refs=[(DependencyRefKind.ENTITY, tiger.id)],
                created_at=NOW,
            )
        radius = repos.dependencies.blast_radius(tiger.id)
        assert radius["puzzle_count"] == 2
        assert radius["game_ids"] == ["chain", "grouping"]
        assert radius["day_keys"] == ["2026-09-26", "2026-09-27"]

    def test_dependencies_of_a_manifest_are_listed(self, repos, curated_source):
        repos.sources.put(curated_source)
        tiger = make_entity("tiger", curated_source)
        fact = make_fact(tiger, "habitat", "grassland", curated_source)
        puzzle_id = ids.for_puzzle("2026-09-26", "grouping", "hash")
        manifest_id = ids.for_manifest(puzzle_id)
        repos.dependencies.record_refs(
            manifest_id=manifest_id,
            puzzle_id=puzzle_id,
            game_id="grouping",
            day_key="2026-09-26",
            refs=[
                (DependencyRefKind.ENTITY, tiger.id),
                (DependencyRefKind.FACT, fact.id),
            ],
            created_at=NOW,
        )
        edges = repos.dependencies.dependencies_of(manifest_id)
        assert {e.ref_kind for e in edges} == {
            DependencyRefKind.ENTITY,
            DependencyRefKind.FACT,
        }

    def test_unreferenced_record_has_an_empty_radius(self, repos):
        radius = repos.dependencies.blast_radius("entity:unused")
        assert radius["puzzle_count"] == 0 and radius["game_ids"] == []
