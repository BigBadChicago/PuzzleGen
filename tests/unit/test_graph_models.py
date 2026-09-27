from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from puzzlegen.core import ids
from puzzlegen.core.hashing import stable_hash
from puzzlegen.core.types import (
    DependencyRefKind,
    FreshnessClass,
    ProvenanceClass,
    ReviewStatus,
    SourceKind,
    ValueKind,
)
from puzzlegen.graph.models import (
    Category,
    DependencyRecord,
    EmbeddingRecord,
    Entity,
    Fact,
    FactValue,
    FrequencyRecord,
    Provenance,
    Relationship,
    SnapshotMeta,
    Source,
)

from ..conftest import LATER, NOW, make_category, make_entity, make_fact, sourced_provenance


class TestSource:
    def test_replacement_requires_deprecation(self, curated_source):
        with pytest.raises(ValidationError):
            Source(
                id=ids.for_source("old", "1"),
                name="old",
                kind=SourceKind.LEXICAL,
                version="1",
                retrieved_at=NOW,
                replacement_source_id=curated_source.id,
            )

    def test_deprecated_source_may_name_a_replacement(self, curated_source):
        source = Source(
            id=ids.for_source("old", "1"),
            name="old",
            kind=SourceKind.LEXICAL,
            version="1",
            retrieved_at=NOW,
            deprecated=True,
            replacement_source_id=curated_source.id,
        )
        assert source.replacement_source_id == curated_source.id

    def test_naive_timestamps_are_refused(self):
        with pytest.raises(ValidationError):
            Source(
                id=ids.for_source("x", "1"),
                name="x",
                kind=SourceKind.LEXICAL,
                version="1",
                retrieved_at=dt.datetime(2026, 9, 26, 12, 0),
            )


class TestProvenance:
    def test_computed_must_record_its_inputs(self, curated_source):
        with pytest.raises(ValidationError):
            Provenance(
                source_id=curated_source.id,
                provenance_class=ProvenanceClass.COMPUTED,
                retrieval_date=NOW,
            )

    def test_computed_with_inputs_is_accepted(self, curated_source):
        record = Provenance(
            source_id=curated_source.id,
            provenance_class=ProvenanceClass.COMPUTED,
            retrieval_date=NOW,
            derived_from=(ids.for_relationship("entity:a", "is_a", "entity:b"),),
        )
        assert record.derived_from

    def test_judged_must_name_a_reviewer(self, curated_source):
        with pytest.raises(ValidationError):
            Provenance(
                source_id=curated_source.id,
                provenance_class=ProvenanceClass.JUDGED,
                retrieval_date=NOW,
            )

    def test_confidence_is_bounded(self, curated_source):
        with pytest.raises(ValidationError):
            Provenance(
                source_id=curated_source.id,
                provenance_class=ProvenanceClass.SOURCED,
                retrieval_date=NOW,
                confidence=1.5,
            )


class TestGovernanceInvariants:
    def test_active_record_requires_provenance(self):
        with pytest.raises(ValidationError):
            Entity(
                id=ids.for_entity("tiger", "en"),
                canonical_name="tiger",
                status=ReviewStatus.ACTIVE,
                created_at=NOW,
                updated_at=NOW,
            )

    def test_candidate_record_may_lack_provenance(self):
        entity = Entity(
            id=ids.for_entity("tiger", "en"),
            canonical_name="tiger",
            status=ReviewStatus.CANDIDATE,
            created_at=NOW,
            updated_at=NOW,
        )
        assert not entity.is_usable()

    def test_active_volatile_record_requires_a_review_date(self, curated_source):
        with pytest.raises(ValidationError):
            Entity(
                id=ids.for_entity("tiger", "en"),
                canonical_name="tiger",
                status=ReviewStatus.ACTIVE,
                freshness_class=FreshnessClass.VOLATILE,
                provenance=(sourced_provenance(curated_source),),
                created_at=NOW,
                updated_at=NOW,
            )

    def test_active_judged_record_requires_a_reviewer(self, curated_source):
        judged = Provenance(
            source_id=curated_source.id,
            provenance_class=ProvenanceClass.JUDGED,
            retrieval_date=NOW,
            reviewer="curator:a",
            confidence=0.8,
        )
        entity = Entity(
            id=ids.for_entity("tiger", "en"),
            canonical_name="tiger",
            status=ReviewStatus.ACTIVE,
            confidence=0.8,
            provenance=(judged,),
            created_at=NOW,
            updated_at=NOW,
        )
        assert entity.is_usable()

    def test_confidence_cannot_exceed_its_provenance(self, curated_source):
        with pytest.raises(ValidationError):
            Entity(
                id=ids.for_entity("tiger", "en"),
                canonical_name="tiger",
                status=ReviewStatus.ACTIVE,
                confidence=0.99,
                provenance=(sourced_provenance(curated_source, confidence=0.5),),
                created_at=NOW,
                updated_at=NOW,
            )

    def test_is_due_for_review_uses_the_review_date(self, curated_source):
        entity = make_entity(
            "tiger", curated_source, freshness=FreshnessClass.PERIODIC
        )
        assert not entity.is_due_for_review(NOW)
        assert entity.is_due_for_review(LATER + dt.timedelta(days=1))

    def test_provenance_classes_are_reported(self, curated_source):
        entity = make_entity("tiger", curated_source)
        assert entity.provenance_classes() == frozenset({ProvenanceClass.SOURCED})


class TestEntity:
    def test_aliases_must_be_distinct(self, curated_source):
        with pytest.raises(ValidationError):
            make_entity("tiger", curated_source, aliases=("Panthera tigris", "Panthera tigris"))

    def test_canonical_name_may_not_repeat_in_aliases(self, curated_source):
        with pytest.raises(ValidationError):
            make_entity("tiger", curated_source, aliases=("tiger",))

    def test_names_include_aliases(self, curated_source):
        entity = make_entity("tiger", curated_source, aliases=("Panthera tigris",))
        assert entity.names() == ("tiger", "Panthera tigris")

    def test_entity_is_frozen(self, curated_source):
        entity = make_entity("tiger", curated_source)
        with pytest.raises(ValidationError):
            entity.canonical_name = "lion"  # type: ignore[misc]

    def test_bad_language_tag_is_refused(self, curated_source):
        with pytest.raises(ValidationError):
            Entity(
                id=ids.for_entity("tiger", "en"),
                canonical_name="tiger",
                lang="english",
                created_at=NOW,
                updated_at=NOW,
            )


class TestCategory:
    def test_root_has_depth_zero(self, curated_source):
        root = make_category("animal", curated_source)
        assert root.depth == 0 and root.min_depth == 0 and root.is_root

    def test_child_depth_increments(self, curated_source):
        root = make_category("animal", curated_source)
        child = make_category("feline", curated_source, parent=root)
        assert child.depth == 1 and child.min_depth == 1
        assert child.parent_ids == (root.id,)

    def test_forked_ancestry_takes_longest_and_shortest_paths(self, curated_source):
        root = make_category("thing", curated_source)
        shallow = make_category("carnivore", curated_source, parent=root)
        mid = make_category("mammal", curated_source, parent=root)
        deep = make_category("feliform", curated_source, parent=mid)
        forked = make_category(
            "red panda group", curated_source, parents=(shallow, deep)
        )
        assert forked.depth == 3
        assert forked.min_depth == 2
        assert forked.has_forked_ancestry

    def test_single_parent_category_is_not_forked(self, curated_source):
        root = make_category("animal", curated_source)
        child = make_category("feline", curated_source, parent=root)
        assert not child.has_forked_ancestry

    def test_parents_must_share_the_taxonomy(self, curated_source):
        root = Category.build(
            canonical_name="animal",
            created_at=NOW,
            taxonomy="wordnet",
            status=ReviewStatus.CANDIDATE,
        )
        with pytest.raises(ValueError):
            Category.build(
                canonical_name="feline",
                parents=(root,),
                created_at=NOW,
                taxonomy="default",
                status=ReviewStatus.CANDIDATE,
            )

    def test_a_retired_category_cannot_be_a_parent(self, curated_source):
        root = Category.build(
            canonical_name="animal",
            created_at=NOW,
            retired=True,
            status=ReviewStatus.RETIRED,
        )
        with pytest.raises(ValueError):
            Category.build(
                canonical_name="feline",
                parents=(root,),
                created_at=NOW,
                status=ReviewStatus.CANDIDATE,
            )

    def test_depth_override_is_honoured(self, curated_source):
        parent = Category.build(
            canonical_name="animal", created_at=NOW, status=ReviewStatus.CANDIDATE
        )
        imported = Category.build(
            canonical_name="feline",
            parents=(parent,),
            created_at=NOW,
            depth_override=(4, 7),
            status=ReviewStatus.CANDIDATE,
        )
        assert (imported.min_depth, imported.depth) == (4, 7)

    def test_parentless_category_must_be_root(self):
        with pytest.raises(ValidationError):
            Category(
                id=ids.for_category("feline"),
                canonical_name="feline",
                depth=3,
                min_depth=3,
                created_at=NOW,
                updated_at=NOW,
            )

    def test_parented_category_must_have_positive_depth(self):
        with pytest.raises(ValidationError):
            Category(
                id=ids.for_category("feline"),
                canonical_name="feline",
                parent_ids=(ids.for_category("animal"),),
                depth=0,
                min_depth=0,
                created_at=NOW,
                updated_at=NOW,
            )

    def test_min_depth_cannot_exceed_depth(self):
        with pytest.raises(ValidationError):
            Category(
                id=ids.for_category("feline"),
                canonical_name="feline",
                parent_ids=(ids.for_category("animal"),),
                depth=2,
                min_depth=3,
                created_at=NOW,
                updated_at=NOW,
            )

    def test_duplicate_parents_are_refused(self):
        parent = ids.for_category("animal")
        with pytest.raises(ValidationError):
            Category(
                id=ids.for_category("feline"),
                canonical_name="feline",
                parent_ids=(parent, parent),
                depth=1,
                min_depth=1,
                created_at=NOW,
                updated_at=NOW,
            )

    def test_category_cannot_parent_itself(self):
        with pytest.raises(ValidationError):
            Category(
                id=ids.for_category("feline"),
                canonical_name="feline",
                parent_ids=(ids.for_category("feline"),),
                depth=1,
                min_depth=1,
                created_at=NOW,
                updated_at=NOW,
            )

    def test_replacement_requires_retirement(self):
        with pytest.raises(ValidationError):
            Category(
                id=ids.for_category("feline"),
                canonical_name="feline",
                depth=0,
                min_depth=0,
                replacement_category_id=ids.for_category("feliform"),
                created_at=NOW,
                updated_at=NOW,
            )


class TestFactValue:
    def test_exactly_one_slot_is_populated(self):
        with pytest.raises(ValidationError):
            FactValue(kind=ValueKind.STRING, text="a", integer=1)

    def test_missing_slot_is_refused(self):
        with pytest.raises(ValidationError):
            FactValue(kind=ValueKind.STRING)

    @pytest.mark.parametrize(
        "value", ["endangered", 42, 3.5, True, dt.date(2026, 9, 26)]
    )
    def test_round_trips_supported_types(self, value):
        assert FactValue.of(value).as_python() == value

    def test_booleans_are_not_mistaken_for_integers(self):
        assert FactValue.of(True).kind is ValueKind.BOOLEAN

    def test_unit_only_applies_to_numbers(self):
        with pytest.raises(ValidationError):
            FactValue(kind=ValueKind.STRING, text="a", unit="kg")

    def test_unit_is_part_of_the_repr_key(self):
        assert FactValue.of(5, unit="kg").repr_key() != FactValue.of(5).repr_key()

    def test_unsupported_type_is_refused(self):
        with pytest.raises(TypeError):
            FactValue.of({"a": 1})


class TestFact:
    def test_id_is_derived_from_its_content(self, curated_source):
        entity = make_entity("tiger", curated_source)
        fact = make_fact(entity, "conservation_status", "endangered", curated_source)
        assert fact.id == ids.for_fact(entity.id, "conservation_status", fact.value.repr_key())

    def test_mismatched_id_is_refused(self, curated_source):
        entity = make_entity("tiger", curated_source)
        with pytest.raises(ValidationError):
            Fact(
                id=ids.mint(ids.FACT, "handmade"),
                subject_id=entity.id,
                predicate="conservation_status",
                value=FactValue.of("endangered"),
                created_at=NOW,
                updated_at=NOW,
            )

    def test_changing_the_value_changes_the_id(self, curated_source):
        entity = make_entity("tiger", curated_source)
        a = make_fact(entity, "conservation_status", "endangered", curated_source)
        b = make_fact(entity, "conservation_status", "vulnerable", curated_source)
        assert a.id != b.id

    def test_predicate_format_is_enforced(self, curated_source):
        entity = make_entity("tiger", curated_source)
        with pytest.raises(ValidationError):
            Fact.build(
                subject_id=entity.id,
                predicate="Conservation Status",
                value=FactValue.of("endangered"),
                created_at=NOW,
            )


class TestRelationship:
    def test_id_is_derived_from_its_endpoints(self, curated_source):
        tiger = make_entity("tiger", curated_source)
        feline = make_category("feline", curated_source)
        rel = Relationship.build(
            subject_id=tiger.id,
            predicate="is_a",
            object_id=feline.id,
            created_at=NOW,
        )
        assert rel.id == ids.for_relationship(tiger.id, "is_a", feline.id)

    def test_self_loops_are_refused(self, curated_source):
        tiger = make_entity("tiger", curated_source)
        with pytest.raises(ValidationError):
            Relationship.build(
                subject_id=tiger.id,
                predicate="is_a",
                object_id=tiger.id,
                created_at=NOW,
            )

    def test_endpoints_must_be_entities_or_categories(self, curated_source):
        tiger = make_entity("tiger", curated_source)
        with pytest.raises(ValidationError):
            Relationship.build(
                subject_id=tiger.id,
                predicate="is_a",
                object_id=ids.for_source("curated", "1"),
                created_at=NOW,
            )


class TestAuxiliaryRecords:
    def test_embedding_length_must_match_dimensions(self):
        with pytest.raises(ValidationError):
            EmbeddingRecord(
                entity_id=ids.for_entity("tiger", "en"),
                model_name="m",
                model_version="1",
                dimensions=4,
                vector=(0.1, 0.2),
                computed_at=NOW,
            )

    def test_embedding_must_be_finite(self):
        with pytest.raises(ValidationError):
            EmbeddingRecord(
                entity_id=ids.for_entity("tiger", "en"),
                model_name="m",
                model_version="1",
                dimensions=2,
                vector=(0.1, float("inf")),
                computed_at=NOW,
            )

    def test_frequency_record_requires_an_entity_id(self):
        with pytest.raises(ValidationError):
            FrequencyRecord(
                entity_id="category:animal",
                zipf=4.0,
                band="COMMON",
                source_name="wordfreq",
                source_version="3.1",
                retrieved_at=NOW,
            )

    def test_sealed_snapshot_requires_a_hash(self):
        with pytest.raises(ValidationError):
            SnapshotMeta(
                id=ids.for_snapshot("2026-09-26"),
                label="2026-09-26",
                created_at=NOW,
                sealed=True,
            )

    def test_dependency_ref_kind_must_match_the_ref_id(self):
        with pytest.raises(ValidationError):
            DependencyRecord.build(
                manifest_id=ids.for_manifest("puzzle:x"),
                puzzle_id=ids.mint(ids.PUZZLE, "x"),
                game_id="grouping",
                day_key="2026-09-26",
                ref_kind=DependencyRefKind.FACT,
                ref_id=ids.for_entity("tiger", "en"),
                created_at=NOW,
            )


class TestModelHashing:
    def test_equal_records_hash_equally(self, curated_source):
        a = make_entity("tiger", curated_source)
        b = make_entity("tiger", curated_source)
        assert stable_hash(a.model_dump(mode="json")) == stable_hash(b.model_dump(mode="json"))

    def test_differing_records_hash_differently(self, curated_source):
        a = make_entity("tiger", curated_source)
        b = make_entity("lion", curated_source)
        assert stable_hash(a.model_dump(mode="json")) != stable_hash(b.model_dump(mode="json"))
