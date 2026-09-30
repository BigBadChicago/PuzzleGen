"""Sibling grouping: groups of distinct children under one parent.

The rule these tests exist to pin down is the difference between two answers
to "these belong together". In a hierarchy imported from WordNet a category is
one meaning and its direct members are synonyms of it, so a group drawn from
one category is one thing under several names. A group drawn from the
children of one parent is several different things of one kind. The default
mode keeps the first behaviour, because a hand-authored taxonomy, where a
category simply holds everything of a kind, is exactly what it is right for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from puzzlegen.content.query import ContentQuery, GroupingMode, Operation
from puzzlegen.content.service import ContentService
from puzzlegen.content.snapshots import ActivationPolicy, SnapshotBuilder
from puzzlegen.core import ids
from puzzlegen.core.errors import RejectionReason
from puzzlegen.providers.curated import CuratedJSONProvider

from ..conftest import NOW

TAXONOMY = "lex"


def category(name: str, *parents: str) -> dict:
    row = {"key": f"c.{name}", "name": name}
    if parents:
        row["parents"] = [f"c.{p}" for p in parents]
    return row


def entity(name: str, *categories: str) -> dict:
    return {
        "key": f"e.{name}",
        "name": name,
        "categories": [f"c.{c}" for c in categories],
        "confidence": 0.95,
    }


def family(
    categories: list[dict],
    entities: list[dict],
    parent: str,
    kids: dict[str, list[str]],
) -> None:
    """Children of ``parent``, each with its own name plus any synonyms."""
    for child, synonyms in kids.items():
        categories.append(category(child, parent))
        for lemma in (child, *synonyms):
            entities.append(entity(lemma, child))


def write(path: Path, categories: list[dict], entities: list[dict]) -> Path:
    path.write_text(
        json.dumps(
            {
                "curated_schema": 1,
                "version": "1",
                "updated": "2026-09-28",
                "categories": categories,
                "entities": entities,
            }
        ),
        encoding="utf-8",
    )
    return path


def build(repos, tmp_path: Path, categories, entities, *, extra=None):
    builder = SnapshotBuilder(repos, now=NOW)
    builder.import_provider(
        CuratedJSONProvider(write(tmp_path / "lex.json", categories, entities), now=NOW),
        taxonomy=TAXONOMY,
        entity_identity="lemma",
    )
    if extra is not None:
        extra_categories, extra_entities = extra
        builder.import_provider(
            CuratedJSONProvider(
                write(tmp_path / "extra.json", extra_categories, extra_entities),
                now=NOW,
            ),
            taxonomy="extra",
            entity_identity="lemma",
        )
    builder.activate(ActivationPolicy())
    return ContentService(repos)


def world(tmp_path_factory=None):
    categories = [category("thing"), category("vessel", "thing"), category("vehicle", "thing")]
    # Every category in a real export has an entity carrying its own name (the
    # first lemma), which is what lets it stand as a child of its parent.
    # Checked against the committed lexicons: every synset in every file does.
    entities: list[dict] = [entity("vessel", "vessel"), entity("vehicle", "vehicle")]
    family(
        categories,
        entities,
        "vessel",
        {
            "canoe": ["dugout", "pirogue"],
            "kayak": ["baidarka"],
            "dory": [],
            "punt": [],
            "yacht": [],
            "tug": ["tugboat", "towboat"],
        },
    )
    family(
        categories,
        entities,
        "vehicle",
        {
            "car": ["auto", "automobile"],
            "bus": [],
            "truck": [],
            "baby buggy": [
                "baby carriage",
                "carriage",
                "go-cart",
                "perambulator",
                "pram",
                "pushchair",
                "pusher",
                "stroller",
            ],
        },
    )
    return categories, entities


@pytest.fixture
def service(repos, tmp_path):
    categories, entities = world()
    return build(repos, tmp_path, categories, entities)


def groups_query(size: int, mode: GroupingMode, **extra) -> ContentQuery:
    return ContentQuery(
        operation=Operation.FIND_GROUPS,
        taxonomy=TAXONOMY,
        group_size=size,
        grouping=mode,
        **extra,
    )


class TestTheDefaultIsTheOriginalRule:
    def test_a_query_defaults_to_shared_category(self):
        assert (
            ContentQuery(operation=Operation.FIND_GROUPS).grouping
            is GroupingMode.SHARED_CATEGORY
        )

    def test_it_groups_the_direct_members_of_one_category(self, service):
        """A hand-authored taxonomy's shape, and the reason the mode survives.

        Nine names for a pram is a group of nine under this rule, which is
        wrong for a hierarchy derived from WordNet and exactly right for a
        category someone filled by hand.
        """
        result = service.execute(groups_query(5, GroupingMode.SHARED_CATEGORY))

        assert result.groups
        assert {g.shared_category for g in result.groups} == {"baby buggy"}

    def test_its_members_are_all_one_category(self, service):
        result = service.execute(groups_query(5, GroupingMode.SHARED_CATEGORY))

        buggy = ids.for_category("baby buggy", "en", TAXONOMY)
        for group in result.groups:
            assert all(buggy in member.type_ids for member in group.members)

    def test_similarity_sees_one_category_repeated(self, service):
        result = service.execute(groups_query(5, GroupingMode.SHARED_CATEGORY))

        assert result.groups[0].minimum_similarity == pytest.approx(1.0)


class TestSiblingGroups:
    def test_groups_are_distinct_children_of_one_parent(self, service):
        result = service.execute(groups_query(5, GroupingMode.SIBLINGS, limit=10))

        assert result.groups
        assert {g.shared_category for g in result.groups} == {"vessel"}
        allowed = {"canoe", "kayak", "dory", "punt", "yacht", "tug"}
        for group in result.groups:
            assert {m.name for m in group.members} <= allowed

    def test_a_synonym_is_never_a_member(self, service):
        """The periwinkle problem: nine names for one thing are one tile."""
        result = service.execute(groups_query(5, GroupingMode.SIBLINGS, limit=10))

        names = {m.name for g in result.groups for m in g.members}
        assert names.isdisjoint({"dugout", "pirogue", "baidarka", "tugboat", "towboat"})

    def test_a_synonym_pile_is_not_a_group(self, service):
        result = service.execute(groups_query(5, GroupingMode.SIBLINGS, limit=10))

        assert "baby buggy" not in {g.shared_category for g in result.groups}
        names = {m.name for g in result.groups for m in g.members}
        assert names.isdisjoint({"pram", "stroller", "pushchair", "go-cart"})

    def test_every_member_stands_for_a_different_child(self, service):
        result = service.execute(groups_query(5, GroupingMode.SIBLINGS, limit=10))

        vessel = ids.for_category("vessel", "en", TAXONOMY)
        for group in result.groups:
            own = []
            for member in group.members:
                child = ids.for_category(member.name, "en", TAXONOMY)
                assert child in member.type_ids
                assert vessel in member.type_ids
                own.append(child)
            assert len(set(own)) == len(own) == 5

    def test_the_parent_is_a_dependency(self, service):
        """Retiring "vessel" has to invalidate every puzzle that grouped by it."""
        result = service.execute(groups_query(5, GroupingMode.SIBLINGS))

        vessel = ids.for_category("vessel", "en", TAXONOMY)
        assert all(vessel in g.dependency_ids() for g in result.groups)

    def test_the_gates_see_the_children_not_one_repeated_category(self, service):
        result = service.execute(groups_query(5, GroupingMode.SIBLINGS))

        assert all(g.minimum_similarity < 1.0 for g in result.groups)

    def test_a_similarity_floor_is_judged_between_the_children(self, service):
        result = service.execute(
            groups_query(5, GroupingMode.SIBLINGS, minimum_similarity=0.99)
        )

        assert not result.groups
        assert result.rejected[RejectionReason.SEMANTIC_DISTANCE_TOO_HIGH.value] > 0

    def test_results_are_deterministic(self, service):
        first = service.execute(groups_query(5, GroupingMode.SIBLINGS, limit=10))
        second = service.execute(groups_query(5, GroupingMode.SIBLINGS, limit=10))

        assert [[m.entity_id for m in g.members] for g in first.groups] == [
            [m.entity_id for m in g.members] for g in second.groups
        ]


class TestWhichParentsAreOffered:
    def test_a_parent_with_too_few_children_offers_nothing(self, service):
        """Vehicle has four children, one of them a synonym pile."""
        result = service.execute(groups_query(5, GroupingMode.SIBLINGS, limit=50))

        assert "vehicle" not in {g.shared_category for g in result.groups}

    def test_a_smaller_group_reaches_the_smaller_parent(self, service):
        result = service.execute(groups_query(4, GroupingMode.SIBLINGS, limit=50))

        assert {"vessel", "vehicle"} <= {g.shared_category for g in result.groups}

    def test_a_size_no_parent_can_fill_is_unsatisfied(self, service):
        result = service.execute(groups_query(7, GroupingMode.SIBLINGS))

        assert not result.groups
        assert not result.satisfied

    def test_a_child_with_no_entity_named_like_it_is_not_offered(
        self, repos, tmp_path
    ):
        """No representative means no tile, rather than a guessed one."""
        categories = [category("thing"), category("fleet", "thing")]
        entities: list[dict] = []
        family(
            categories,
            entities,
            "fleet",
            {"a": [], "b": [], "c": [], "d": []},
        )
        categories.append(category("e", "fleet"))
        entities.append(entity("only-an-alias", "e"))
        service = build(repos, tmp_path, categories, entities)

        result = service.execute(groups_query(5, GroupingMode.SIBLINGS))

        assert not result.groups

    def test_the_root_is_not_a_useful_parent(self, service):
        """Its children share nothing deeper than the root, so similarity is 0.

        The first query is the control: without a floor the root is offered,
        so its absence from the second is the similarity gate at work and not
        the parent never having been a candidate.
        """
        ungated = service.execute(groups_query(2, GroupingMode.SIBLINGS, limit=50))
        gated = service.execute(
            groups_query(2, GroupingMode.SIBLINGS, minimum_similarity=0.35, limit=50)
        )

        assert "thing" in {g.shared_category for g in ungated.groups}
        assert "thing" not in {g.shared_category for g in gated.groups}


class TestDistinctness:
    def test_a_child_that_descends_from_another_is_refused(self, repos, tmp_path):
        """Two of the five are then one concept and one of its own kinds."""
        categories = [category("thing"), category("fleet", "thing")]
        entities: list[dict] = []
        family(categories, entities, "fleet", {"a": [], "b": [], "c": [], "d": []})
        categories.append({"key": "c.e", "name": "e", "parents": ["c.fleet", "c.a"]})
        entities.append(entity("e", "e"))
        service = build(repos, tmp_path, categories, entities)

        result = service.execute(groups_query(5, GroupingMode.SIBLINGS))

        assert not result.groups
        assert result.rejected[RejectionReason.NOT_DISTINCT_SIBLINGS.value] == 1

    def test_the_assessor_rederives_the_children_itself(self, service):
        """A gate that runs even when the generator should have guaranteed it."""
        entities = {e.canonical_name: e for e in service._repos.entities.active()}
        members = [entities[n] for n in ("canoe", "kayak", "dory", "punt", "yacht")]
        memberships = {m.id: service._membership_ids(m.id) for m in members}
        index = service.taxonomy(TAXONOMY)
        views = {
            m.id: service._view(m, groups_query(5, GroupingMode.SIBLINGS), None, taxonomy=index)
            for m in members
        }

        wrong_parent = ids.for_category("vehicle", "en", TAXONOMY)
        right_parent = ids.for_category("vessel", "en", TAXONOMY)
        member_ids = [m.id for m in members]

        assert service._sibling_children(member_ids, memberships, index, wrong_parent, views) is None
        found = service._sibling_children(member_ids, memberships, index, right_parent, views)
        assert found is not None and len(set(found.values())) == 5


class TestASecondTaxonomyIsMetByConstruction:
    def test_children_carrying_it_are_tried_first(self, repos, tmp_path):
        """The requirement is satisfied by ordering, not by refusing most of
        what is offered.

        The carrying child is named to sort last by id. Enumerated in id
        order, the first combinations of five from eight would never include
        it, and a small limit would come back empty with every candidate
        refused. Ordered carrying-first, every early combination has it.
        """
        categories = [category("thing"), category("fleet", "thing")]
        entities: list[dict] = []
        family(
            categories,
            entities,
            "fleet",
            {f"a{i}": [] for i in range(7)} | {"zzz": []},
        )
        extra = (
            [{"key": "x.blue", "name": "blue things"}],
            [{"key": "e.zzz", "name": "zzz", "categories": ["x.blue"], "confidence": 0.95}],
        )
        service = build(repos, tmp_path, categories, entities, extra=extra)

        result = service.execute(
            groups_query(
                5,
                GroupingMode.SIBLINGS,
                intersects_taxonomy="extra",
                minimum_intersecting_members=1,
                limit=3,
            )
        )

        assert len(result.groups) == 3
        assert all("zzz" in {m.name for m in g.members} for g in result.groups)
        assert result.rejected.get(RejectionReason.NOT_IN_REQUIRED_TAXONOMY.value, 0) == 0

    def test_without_the_requirement_id_order_would_never_reach_it(
        self, repos, tmp_path
    ):
        """The contrast that shows the ordering is doing the work.

        Same world, same limit, no second taxonomy asked for: the first
        combinations come out in id order and the last-sorting child is
        absent from every one of them. So the carrying-first ordering above
        is what put it there, not the enumeration happening to reach it.
        """
        categories = [category("thing"), category("fleet", "thing")]
        entities: list[dict] = []
        family(
            categories,
            entities,
            "fleet",
            {f"a{i}": [] for i in range(7)} | {"zzz": []},
        )
        extra = (
            [{"key": "x.blue", "name": "blue things"}],
            [{"key": "e.zzz", "name": "zzz", "categories": ["x.blue"], "confidence": 0.95}],
        )
        service = build(repos, tmp_path, categories, entities, extra=extra)

        result = service.execute(groups_query(5, GroupingMode.SIBLINGS, limit=3))

        assert len(result.groups) == 3
        assert all("zzz" not in {m.name for m in g.members} for g in result.groups)


class TestTaxonomyIndexChildren:
    def test_children_are_one_edge_down(self, service):
        index = service.taxonomy(TAXONOMY)
        vessel = ids.for_category("vessel", "en", TAXONOMY)
        thing = ids.for_category("thing", "en", TAXONOMY)

        assert index.children(vessel) == {
            ids.for_category(n, "en", TAXONOMY)
            for n in ("canoe", "kayak", "dory", "punt", "yacht", "tug")
        }
        assert vessel in index.children(thing)
        assert ids.for_category("canoe", "en", TAXONOMY) not in index.children(thing)

    def test_a_leaf_has_no_children(self, service):
        index = service.taxonomy(TAXONOMY)

        assert index.children(ids.for_category("canoe", "en", TAXONOMY)) == frozenset()


class TestTheTaggedWordIsTheTile:
    """When a query needs a second taxonomy, the word tagged in it stands for
    its child, so a familiar synonym is a tile because the curator said so."""

    def strings(self, repos, tmp_path, tagged):
        categories = [category("thing"), category("strings", "thing")]
        entities: list[dict] = []
        family(
            categories,
            entities,
            "strings",
            {"violin": ["fiddle"], "viola": [], "cello": [], "bass": [], "harp": []},
        )
        extra = (
            [{"key": "x.s", "name": "has strings"}],
            [
                {"key": f"e.{w}", "name": w, "categories": ["x.s"], "confidence": 0.95}
                for w in tagged
            ],
        )
        return build(repos, tmp_path, categories, entities, extra=extra)

    def names(self, result) -> set[str]:
        return {m.name for g in result.groups for m in g.members}

    def test_a_tagged_synonym_is_the_tile(self, repos, tmp_path):
        service = self.strings(repos, tmp_path, ["fiddle", "harp"])

        result = service.execute(
            groups_query(5, GroupingMode.SIBLINGS, intersects_taxonomy="extra")
        )

        assert result.groups
        assert "fiddle" in self.names(result)
        assert "violin" not in self.names(result)

    def test_without_the_requirement_the_first_lemma_is_the_tile(self, repos, tmp_path):
        service = self.strings(repos, tmp_path, ["fiddle", "harp"])

        result = service.execute(groups_query(5, GroupingMode.SIBLINGS))

        assert "violin" in self.names(result)
        assert "fiddle" not in self.names(result)

    def test_when_both_are_tagged_the_name_bearing_word_wins(self, repos, tmp_path):
        service = self.strings(repos, tmp_path, ["fiddle", "violin", "harp"])

        result = service.execute(
            groups_query(5, GroupingMode.SIBLINGS, intersects_taxonomy="extra")
        )

        assert "violin" in self.names(result)
        assert "fiddle" not in self.names(result)

    def test_the_assessor_accepts_a_synonym_it_was_told_stands_for_its_child(
        self, repos, tmp_path
    ):
        """The name check that would have refused "fiddle" is gone. What is
        checked instead is that the tile is a member of the child it claims."""
        service = self.strings(repos, tmp_path, ["fiddle"])
        entities = {e.canonical_name: e for e in service._repos.entities.active()}
        members = [entities[n] for n in ("fiddle", "viola", "cello", "bass", "harp")]
        memberships = {m.id: service._membership_ids(m.id) for m in members}
        index = service.taxonomy(TAXONOMY)
        query = groups_query(5, GroupingMode.SIBLINGS)
        views = {m.id: service._view(m, query, None, taxonomy=index) for m in members}
        parent = ids.for_category("strings", "en", TAXONOMY)
        member_ids = [m.id for m in members]
        right = [
            ids.for_category(n, "en", TAXONOMY)
            for n in ("violin", "viola", "cello", "bass", "harp")
        ]
        wrong = [right[1], *right[1:]]

        found = service._sibling_children(member_ids, memberships, index, parent, views, right)

        assert found is not None and len(set(found.values())) == 5
        assert service._sibling_children(member_ids, memberships, index, parent, views, wrong) is None

    def test_a_word_filed_under_two_children_is_a_tile_once(self, repos, tmp_path):
        """``horn`` is a synonym under both cornet and French horn."""
        categories = [category("thing"), category("brass", "thing")]
        entities: list[dict] = []
        family(
            categories,
            entities,
            "brass",
            {"tuba": [], "bugle": [], "trombone": [], "sackbut": []},
        )
        categories += [category("cornet", "brass"), category("French horn", "brass")]
        entities += [
            entity("cornet", "cornet"),
            entity("French horn", "French horn"),
            entity("horn", "cornet", "French horn"),
        ]
        extra = (
            [{"key": "x.b", "name": "is blown"}],
            [{"key": "e.horn", "name": "horn", "categories": ["x.b"], "confidence": 0.95}],
        )
        service = build(repos, tmp_path, categories, entities, extra=extra)

        result = service.execute(
            groups_query(5, GroupingMode.SIBLINGS, intersects_taxonomy="extra", limit=20)
        )

        assert result.groups
        for group in result.groups:
            tiles = [m.entity_id for m in group.members]
            assert len(set(tiles)) == len(tiles) == 5
        names = self.names(result)
        assert "horn" in names and "cornet" in names
        assert "French horn" not in names
