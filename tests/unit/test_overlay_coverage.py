"""Board feasibility measured against the generator's real precondition.

The point of these tests is the gap between the two measures. The handoff's
table counted lexical categories that are individually usable; the generator
needs four of them to work *together* with one overlay category. A world is
built below where the old count is comfortably positive and no board exists,
because that is the failure the measurement had to stop hiding.

``tools/`` is not a package, so the modules are loaded by path exactly the way
``test_overlay_tools.py`` loads the proposer.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from conftest import NOW, make_entity, sourced_provenance

from puzzlegen.core import ids
from puzzlegen.core.types import FreshnessClass, ReviewStatus
from puzzlegen.graph import (
    Category,
    GraphRepositories,
    Relationship,
    SqliteDocumentStore,
)

TOOLS = Path(__file__).resolve().parents[2] / "tools"

LEXICAL = "wordnet"
OVERLAY = "overlay"
IS_A = "is_a"


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(f"tool_{name}", TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(TOOLS))
overlay_coverage = load_tool("overlay_coverage")
measure_coverage = load_tool("measure_coverage")
propose_overlay = load_tool("propose_overlay")


# -- world building -----------------------------------------------------------


def category(name: str, source, *, taxonomy: str, parents=()) -> Category:
    return Category.build(
        canonical_name=name,
        parents=tuple(parents),
        created_at=NOW,
        taxonomy=taxonomy,
        status=ReviewStatus.ACTIVE,
        freshness_class=FreshnessClass.STATIC,
        confidence=0.95,
        provenance=(sourced_provenance(source),),
    )


def membership(entity_id: str, category_id: str, source) -> Relationship:
    return Relationship.build(
        subject_id=entity_id,
        predicate=IS_A,
        object_id=category_id,
        created_at=NOW,
        status=ReviewStatus.ACTIVE,
        freshness_class=FreshnessClass.STATIC,
        provenance=(sourced_provenance(source),),
    )


class World:
    """A graph builder that speaks in names rather than ids."""

    def __init__(self, repos: GraphRepositories, source):
        self.repos = repos
        self.source = source
        repos.sources.put(source)
        self.categories: dict[str, Category] = {}

    def entity(self, name: str) -> str:
        entity = make_entity(name, self.source)
        self.repos.entities.put(entity)
        return entity.id

    def category(self, name: str, *, taxonomy: str, parent: str | None = None) -> str:
        parents = (self.categories[parent],) if parent else ()
        record = category(name, self.source, taxonomy=taxonomy, parents=parents)
        self.repos.categories.put(record)
        self.categories[name] = record
        return record.id

    def member(self, entity_name: str, category_name: str) -> None:
        self.repos.relationships.put(
            membership(
                ids.for_entity(entity_name, "en"),
                self.categories[category_name].id,
                self.source,
            )
        )

    def lexical_group(self, name: str, members: list[str], *, parent=None) -> None:
        self.category(name, taxonomy=LEXICAL, parent=parent)
        for member in members:
            self.entity(member)
            self.member(member, name)

    def overlay_group(self, name: str, members: list[str]) -> None:
        self.category(name, taxonomy=OVERLAY)
        for member in members:
            self.member(member, name)

    def id_of(self, category_name: str) -> str:
        return self.categories[category_name].id


def workable(world: World) -> None:
    """Four lexical groups of five, and a hidden group touching all four.

    Five hidden members across four groups means one group gives up two,
    which is what ``borrowings_for`` allows and what a five-tile hidden group
    on a four-group board forces.
    """
    world.lexical_group("cats", ["lion", "tiger", "puma", "lynx", "ocelot"])
    world.lexical_group("birds", ["robin", "crane", "swift", "finch", "heron"])
    world.lexical_group("tools", ["hammer", "chisel", "plane", "file", "awl"])
    world.lexical_group("boats", ["ketch", "punt", "yawl", "dinghy", "canoe"])
    world.overlay_group("also_a_verb", ["crane", "swift", "file", "punt", "lynx"])


@pytest.fixture
def world(repos, curated_source) -> World:
    return World(repos, curated_source)


def snapshot_of(world: World):
    return overlay_coverage.load(
        world.repos, lexical_taxonomy=LEXICAL, overlay_taxonomy=OVERLAY
    )


# -- loading ------------------------------------------------------------------


class TestLoading:
    def test_members_are_direct_not_inherited(self, world):
        """A group is drawn from direct membership, as the service does.

        Counting inherited members would make a parent category look large
        enough to supply a group it cannot actually fill.
        """
        world.lexical_group("tools", ["hammer", "chisel", "plane", "file", "awl"])
        world.lexical_group("axes", ["hatchet", "adze"], parent="tools")
        snapshot = snapshot_of(world)

        assert len(snapshot.lexical_members[world.id_of("tools")]) == 5
        assert len(snapshot.lexical_members[world.id_of("axes")]) == 2

    def test_types_carry_ancestry(self, world):
        """The cross-membership gate reads ancestry, so the loader must too."""
        world.lexical_group("tools", ["hammer"])
        world.lexical_group("axes", ["hatchet"], parent="tools")
        snapshot = snapshot_of(world)

        hatchet = ids.for_entity("hatchet", "en")
        assert world.id_of("tools") in snapshot.types_of[hatchet]
        assert world.id_of("axes") in snapshot.types_of[hatchet]

    def test_pending_membership_is_not_loaded(self, world, curated_source):
        """Only ACTIVE counts. A proposed word is not yet content."""
        workable(world)
        world.entity("salmon")
        world.repos.relationships.put(
            Relationship.build(
                subject_id=ids.for_entity("salmon", "en"),
                predicate=IS_A,
                object_id=world.id_of("also_a_verb"),
                created_at=NOW,
                status=ReviewStatus.PENDING_REVIEW,
                freshness_class=FreshnessClass.STATIC,
                provenance=(sourced_provenance(curated_source),),
            )
        )
        snapshot = snapshot_of(world)

        assert len(snapshot.overlay_members[world.id_of("also_a_verb")]) == 5


# -- the precondition ---------------------------------------------------------


class TestAssess:
    def test_a_workable_world_is_feasible(self, world):
        workable(world)
        snapshot = snapshot_of(world)

        finding = overlay_coverage.assess(snapshot, world.id_of("also_a_verb"), 5)

        assert finding.feasible
        assert finding.exhausted
        assert len(finding.quadruple) == 4

    def test_too_few_hidden_members(self, world):
        workable(world)
        snapshot = snapshot_of(world)

        finding = overlay_coverage.assess(snapshot, world.id_of("also_a_verb"), 6)

        assert not finding.feasible
        assert finding.reason == overlay_coverage.TOO_FEW_HIDDEN_MEMBERS

    def test_a_member_with_no_usable_home(self, world):
        """A hidden word sitting in a near-leaf category is unplaceable.

        This is the measured cause of the phase 7 gap in one sentence: the
        word exists, it is in the overlay, and there is no visible group it
        can be borrowed from.
        """
        world.lexical_group("cats", ["lion", "tiger", "puma", "lynx", "ocelot"])
        world.lexical_group("birds", ["robin", "crane", "swift", "finch", "heron"])
        world.lexical_group("tools", ["hammer", "chisel", "plane", "file", "awl"])
        world.lexical_group("boats", ["ketch", "punt", "yawl", "dinghy", "canoe"])
        world.lexical_group("heathers", ["heather"])
        world.overlay_group(
            "also_a_name", ["heather", "crane", "file", "punt", "lynx"]
        )
        snapshot = snapshot_of(world)

        finding = overlay_coverage.assess(snapshot, world.id_of("also_a_name"), 5)

        assert not finding.feasible
        assert finding.reason == overlay_coverage.MEMBERS_WITHOUT_HOME
        assert finding.homeless == (ids.for_entity("heather", "en"),)

    def test_members_reaching_only_three_categories(self, world):
        world.lexical_group("cats", ["lion", "tiger", "puma", "lynx", "ocelot"])
        world.lexical_group("birds", ["robin", "crane", "swift", "finch", "heron"])
        world.lexical_group("tools", ["hammer", "chisel", "plane", "file", "awl"])
        world.lexical_group("boats", ["ketch", "punt", "yawl", "dinghy", "canoe"])
        world.overlay_group(
            "also_a_verb", ["crane", "swift", "file", "lynx", "puma"]
        )
        snapshot = snapshot_of(world)

        finding = overlay_coverage.assess(snapshot, world.id_of("also_a_verb"), 5)

        assert not finding.feasible
        assert finding.reason == overlay_coverage.TOO_FEW_DISTINCT_HOMES
        assert len(finding.homes) == 3

    def test_cross_membership_through_ancestry_blocks_a_board(self, world):
        """A chosen group whose ancestor is another chosen group is unusable.

        Every tile in the child carries the parent's category, so a player
        putting it in the parent's pile is not wrong, which is exactly what
        ``has_no_cross_membership`` refuses.
        """
        world.lexical_group("tools", ["hammer", "chisel", "plane", "file", "awl"])
        world.lexical_group(
            "axes", ["hatchet", "adze", "broadaxe", "cleaver", "maul"], parent="tools"
        )
        world.lexical_group("birds", ["robin", "crane", "swift", "finch", "heron"])
        world.lexical_group("boats", ["ketch", "punt", "yawl", "dinghy", "canoe"])
        world.overlay_group("also_a_verb", ["file", "hatchet", "crane", "punt", "yawl"])
        snapshot = snapshot_of(world)

        finding = overlay_coverage.assess(snapshot, world.id_of("also_a_verb"), 5)

        assert not finding.feasible
        assert finding.reason == overlay_coverage.NO_VALID_QUADRUPLE

    def test_shared_tiles_are_counted_once(self, world):
        """Two categories overlapping cannot both fill from the same tiles.

        Five members each, four of them shared, is ten tiles on paper and six
        distinct in fact. A count of category sizes says this works; the
        matching says it does not.
        """
        shared = ["alpha", "beta", "gamma", "delta"]
        world.lexical_group("left", [*shared, "one"])
        world.category("right", taxonomy=LEXICAL)
        for name in [*shared, "two"]:
            if name == "two":
                world.entity(name)
            world.member(name, "right")
        world.lexical_group("birds", ["robin", "crane", "swift", "finch", "heron"])
        world.lexical_group("boats", ["ketch", "punt", "yawl", "dinghy", "canoe"])
        world.overlay_group("also_a_verb", ["one", "two", "crane", "punt", "yawl"])
        snapshot = snapshot_of(world)

        finding = overlay_coverage.assess(snapshot, world.id_of("also_a_verb"), 5)

        assert not finding.feasible

    def test_a_bounded_search_says_so(self, world):
        """An overrun reports itself rather than reporting a clean no.

        The engine's solvers never dress a partial search as an exact count,
        and a measurement that did would send a curator to grow content that
        was already sufficient.
        """
        workable(world)
        snapshot = snapshot_of(world)

        finding = overlay_coverage.assess(
            snapshot, world.id_of("also_a_verb"), 5, budget=0
        )

        assert not finding.feasible
        assert not finding.exhausted
        assert finding.reason == overlay_coverage.BUDGET_EXHAUSTED

    def test_survey_covers_every_size(self, world):
        workable(world)
        snapshot = snapshot_of(world)

        report = overlay_coverage.survey(snapshot, group_sizes=(5, 6))

        assert {f.group_size for f in report.findings} == {5, 6}
        assert report.any_feasible()
        assert len(report.feasible_at(5)) == 1
        assert report.feasible_at(6) == []


# -- the proxy the old table used --------------------------------------------


class TestTheProxyOverstates:
    def test_the_old_count_is_positive_where_no_board_exists(self, world):
        """The reason this module exists, as one assertion.

        Four lexical categories are individually usable and each holds an
        overlay word, so the handoff's table would report four. No board can
        be built from them, because the overlay words are spread across three
        categories rather than four.
        """
        world.lexical_group("cats", ["lion", "tiger", "puma", "lynx", "ocelot"])
        world.lexical_group("birds", ["robin", "crane", "swift", "finch", "heron"])
        world.lexical_group("tools", ["hammer", "chisel", "plane", "file", "awl"])
        world.lexical_group("boats", ["ketch", "punt", "yawl", "dinghy", "canoe"])
        world.overlay_group("also_a_verb", ["crane", "swift", "file", "lynx", "puma"])
        world.overlay_group("also_a_boat", ["punt", "yawl"])
        snapshot = snapshot_of(world)

        assert measure_coverage.proxy_count(snapshot, 5) == 4
        report = overlay_coverage.survey(snapshot, group_sizes=(5,))
        assert not report.any_feasible()


# -- what would fix it --------------------------------------------------------


class TestCoverageGain:
    @pytest.fixture
    def three_homes(self, world) -> World:
        world.lexical_group("cats", ["lion", "tiger", "puma", "lynx", "ocelot"])
        world.lexical_group("birds", ["robin", "crane", "swift", "finch", "heron"])
        world.lexical_group("tools", ["hammer", "chisel", "plane", "file", "awl"])
        world.lexical_group("boats", ["ketch", "punt", "yawl", "dinghy", "canoe"])
        world.overlay_group("also_a_verb", ["crane", "swift", "file", "lynx", "puma"])
        return world

    def test_cheap_gain_names_the_category_not_yet_reached(self, three_homes):
        snapshot = snapshot_of(three_homes)

        gain = overlay_coverage.gain_of(
            snapshot,
            ids.for_entity("punt", "en"),
            three_homes.id_of("also_a_verb"),
        )

        assert gain.new_homes == (three_homes.id_of("boats"),)
        assert not gain.checked
        assert gain.unblocks == ()

    def test_a_word_in_a_category_already_reached_buys_nothing(self, three_homes):
        snapshot = snapshot_of(three_homes)

        gain = overlay_coverage.gain_of(
            snapshot,
            ids.for_entity("robin", "en"),
            three_homes.id_of("also_a_verb"),
        )

        assert gain.new_homes == ()

    def test_the_exact_check_reports_which_sizes_flip(self, three_homes):
        snapshot = snapshot_of(three_homes)

        gain = overlay_coverage.gain_of(
            snapshot,
            ids.for_entity("punt", "en"),
            three_homes.id_of("also_a_verb"),
            group_sizes=(5,),
            exact=True,
        )

        assert gain.checked
        assert gain.unblocks == (5,)

    def test_an_exact_check_that_changes_nothing_is_not_silence(self, three_homes):
        """A checked empty result and an unchecked one are different facts."""
        snapshot = snapshot_of(three_homes)

        gain = overlay_coverage.gain_of(
            snapshot,
            ids.for_entity("robin", "en"),
            three_homes.id_of("also_a_verb"),
            group_sizes=(5,),
            exact=True,
        )

        assert gain.checked
        assert gain.unblocks == ()

    def test_with_membership_leaves_the_original_alone(self, three_homes):
        snapshot = snapshot_of(three_homes)
        before = snapshot.overlay_members[three_homes.id_of("also_a_verb")]

        snapshot.with_membership(
            ids.for_entity("punt", "en"), three_homes.id_of("also_a_verb")
        )

        assert snapshot.overlay_members[three_homes.id_of("also_a_verb")] == before


# -- queue order --------------------------------------------------------------


def candidate(propose_module, entity_name: str, category_id: str, confidence: float):
    return propose_module.Candidate(
        subject_ref=f"ref:{entity_name}",
        entity_id=ids.for_entity(entity_name, "en"),
        entity_name=entity_name,
        category_id=category_id,
        category_name="also_a_verb",
        confidence=confidence,
    )


class TestRanking:
    @pytest.fixture
    def three_homes(self, world) -> World:
        world.lexical_group("cats", ["lion", "tiger", "puma", "lynx", "ocelot"])
        world.lexical_group("birds", ["robin", "crane", "swift", "finch", "heron"])
        world.lexical_group("tools", ["hammer", "chisel", "plane", "file", "awl"])
        world.lexical_group("boats", ["ketch", "punt", "yawl", "dinghy", "canoe"])
        world.overlay_group("also_a_verb", ["crane", "swift", "file", "lynx", "puma"])
        return world

    def test_coverage_order_puts_the_unblocking_word_first(self, three_homes):
        """Even when a less useful word is more plausible.

        This is the whole change. Confidence answers "does this word belong";
        the board needs "does this word sit where a group can borrow it", and
        ten days of accepts spent on the first question is how the seed
        overlay ended up scattered.
        """
        snapshot = snapshot_of(three_homes)
        overlay_id = three_homes.id_of("also_a_verb")
        plausible = candidate(propose_overlay, "robin", overlay_id, 0.95)
        useful = candidate(propose_overlay, "punt", overlay_id, 0.61)

        ranked = propose_overlay.rank_candidates(
            [plausible, useful],
            snapshot,
            rank_by=propose_overlay.RANK_BY_COVERAGE,
        )

        assert [c.entity_name for c in ranked] == ["punt", "robin"]
        # Only size 5: a sixth overlay member does not make five-member
        # lexical categories large enough to supply a six-tile group.
        assert ranked[0].unblocks == [5]

    def test_confidence_order_is_still_available_and_still_annotates(
        self, three_homes
    ):
        snapshot = snapshot_of(three_homes)
        overlay_id = three_homes.id_of("also_a_verb")
        plausible = candidate(propose_overlay, "robin", overlay_id, 0.95)
        useful = candidate(propose_overlay, "punt", overlay_id, 0.61)

        ranked = propose_overlay.rank_candidates(
            [plausible, useful],
            snapshot,
            rank_by=propose_overlay.RANK_BY_CONFIDENCE,
        )

        assert [c.entity_name for c in ranked] == ["robin", "punt"]
        assert ranked[1].new_homes == [three_homes.id_of("boats")]

    def test_without_a_snapshot_nothing_is_annotated(self, three_homes):
        overlay_id = three_homes.id_of("also_a_verb")
        ranked = propose_overlay.rank_candidates(
            [
                candidate(propose_overlay, "punt", overlay_id, 0.61),
                candidate(propose_overlay, "robin", overlay_id, 0.95),
            ],
            None,
        )

        assert [c.entity_name for c in ranked] == ["robin", "punt"]
        assert all(not c.coverage_checked for c in ranked)

    def test_the_check_limit_bounds_the_exact_work(self, three_homes):
        snapshot = snapshot_of(three_homes)
        overlay_id = three_homes.id_of("also_a_verb")

        ranked = propose_overlay.rank_candidates(
            [
                candidate(propose_overlay, "punt", overlay_id, 0.61),
                candidate(propose_overlay, "robin", overlay_id, 0.95),
            ],
            snapshot,
            check_limit=1,
        )

        assert sum(1 for c in ranked if c.coverage_checked) == 1


# -- the command --------------------------------------------------------------


class TestMeasureCommand:
    def build(self, path: Path, curated_source, builder) -> None:
        repos = GraphRepositories(SqliteDocumentStore(path))
        try:
            builder(World(repos, curated_source))
        finally:
            repos.close()

    def test_a_generatable_graph_exits_zero(
        self, tmp_path, curated_source, capsys
    ):
        db = tmp_path / "graph.sqlite"
        self.build(db, curated_source, workable)

        code = measure_coverage.main(["--db", str(db), "--group-size", "5"])

        assert code == 0
        assert "generatable: yes" in capsys.readouterr().out

    def test_an_ungeneratable_graph_exits_one_and_names_the_gap(
        self, tmp_path, curated_source, capsys
    ):
        def three_homes(world: World) -> None:
            world.lexical_group("cats", ["lion", "tiger", "puma", "lynx", "ocelot"])
            world.lexical_group(
                "birds", ["robin", "crane", "swift", "finch", "heron"]
            )
            world.lexical_group("tools", ["hammer", "chisel", "plane", "file", "awl"])
            world.lexical_group("boats", ["ketch", "punt", "yawl", "dinghy", "canoe"])
            world.overlay_group(
                "also_a_verb", ["crane", "swift", "file", "lynx", "puma"]
            )

        db = tmp_path / "graph.sqlite"
        self.build(db, curated_source, three_homes)

        code = measure_coverage.main(["--db", str(db), "--group-size", "5"])

        assert code == 1
        out = capsys.readouterr().out
        assert "generatable: no" in out
        assert "propose words from: boats" in out

    def test_the_json_report_carries_both_measures(
        self, tmp_path, curated_source, capsys
    ):
        db = tmp_path / "graph.sqlite"
        self.build(db, curated_source, workable)
        out = tmp_path / "coverage.json"

        measure_coverage.main(
            ["--db", str(db), "--group-size", "5", "--json", str(out)]
        )
        capsys.readouterr()

        import json

        document = json.loads(out.read_text())
        size = document["summary"]["sizes"][0]
        assert size["feasible_hidden_groups"] == 1
        assert size["proxy_usable_categories"] == 4

    def test_a_missing_database_is_an_argument_error(self, tmp_path):
        with pytest.raises(SystemExit):
            measure_coverage.main(["--db", str(tmp_path / "absent.sqlite")])


class TestCandidateBatch:
    """The workflow this whole exercise was for: rank candidates before
    committing them to the seed file, rather than checking one at a time.
    """

    def three_homes(self, world: World) -> None:
        world.lexical_group("cats", ["lion", "tiger", "puma", "lynx", "ocelot"])
        world.lexical_group("birds", ["robin", "crane", "swift", "finch", "heron"])
        world.lexical_group("tools", ["hammer", "chisel", "plane", "file", "awl"])
        world.lexical_group("boats", ["ketch", "punt", "yawl", "dinghy", "canoe"])
        world.overlay_group("also_a_verb", ["crane", "swift", "file", "lynx", "puma"])

    def build(self, path: Path, curated_source) -> None:
        repos = GraphRepositories(SqliteDocumentStore(path))
        try:
            self.three_homes(World(repos, curated_source))
        finally:
            repos.close()

    def test_the_unblocking_candidate_ranks_first(
        self, tmp_path, curated_source, capsys
    ):
        db = tmp_path / "graph.sqlite"
        self.build(db, curated_source)
        candidates = tmp_path / "candidates.json"
        candidates.write_text(
            json.dumps(
                [
                    {"entity": "robin", "category": "also_a_verb"},
                    {"entity": "punt", "category": "also_a_verb"},
                ]
            )
        )

        code = measure_coverage.main(
            [
                "--db",
                str(db),
                "--group-size",
                "5",
                "--candidates",
                str(candidates),
                "--exact",
            ]
        )

        out = capsys.readouterr().out
        assert code == 0
        assert out.index("punt") < out.index("robin")
        assert "UNBLOCKS" in out

    def test_an_unknown_entity_is_reported_not_raised(
        self, tmp_path, curated_source, capsys
    ):
        db = tmp_path / "graph.sqlite"
        self.build(db, curated_source)
        candidates = tmp_path / "candidates.json"
        candidates.write_text(
            json.dumps([{"entity": "narwhal", "category": "also_a_verb"}])
        )

        code = measure_coverage.main(
            ["--db", str(db), "--group-size", "5", "--candidates", str(candidates)]
        )

        out = capsys.readouterr().out
        assert code == 1
        assert "unknown entity" in out

    def test_category_matches_by_name_not_just_id(
        self, tmp_path, curated_source, capsys
    ):
        db = tmp_path / "graph.sqlite"
        self.build(db, curated_source)
        candidates = tmp_path / "candidates.json"
        candidates.write_text(
            json.dumps([{"entity": "punt", "category": "Also_A_Verb"}])
        )

        code = measure_coverage.main(
            ["--db", str(db), "--group-size", "5", "--candidates", str(candidates)]
        )

        assert "unknown category" not in capsys.readouterr().out

    def test_without_exact_the_cheap_new_home_still_shows(
        self, tmp_path, curated_source, capsys
    ):
        db = tmp_path / "graph.sqlite"
        self.build(db, curated_source)
        candidates = tmp_path / "candidates.json"
        candidates.write_text(
            json.dumps([{"entity": "punt", "category": "also_a_verb"}])
        )

        measure_coverage.main(
            ["--db", str(db), "--group-size", "5", "--candidates", str(candidates)]
        )

        out = capsys.readouterr().out
        assert "new home" in out or "UNBLOCKS" in out
        assert "boats" in out

    def test_the_json_report_is_written(self, tmp_path, curated_source, capsys):
        db = tmp_path / "graph.sqlite"
        self.build(db, curated_source)
        candidates = tmp_path / "candidates.json"
        candidates.write_text(
            json.dumps([{"entity": "punt", "category": "also_a_verb"}])
        )
        out_path = tmp_path / "ranked.json"

        measure_coverage.main(
            [
                "--db",
                str(db),
                "--group-size",
                "5",
                "--candidates",
                str(candidates),
                "--json",
                str(out_path),
            ]
        )
        capsys.readouterr()

        document = json.loads(out_path.read_text())
        assert document[0]["entity"] == "punt"

    def test_a_malformed_candidate_file_fails_loudly(self, tmp_path, curated_source):
        db = tmp_path / "graph.sqlite"
        self.build(db, curated_source)
        candidates = tmp_path / "candidates.json"
        candidates.write_text(json.dumps([{"entity": "punt"}]))

        with pytest.raises(ValueError, match="entity.*category"):
            measure_coverage.load_candidates(candidates)
