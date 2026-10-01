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

    def entity(self, name: str, band="default") -> str:
        """``band`` is left at the shared fixture's COMMON unless given.

        ``None`` means unscored, and has to be asked for by name: it is the
        state a word is in when no frequency list has it, which is a third of
        the real snapshot, and a helper that quietly made every test word
        COMMON hid the case.
        """
        entity = make_entity(name, self.source)
        if band != "default":
            entity = entity.model_copy(update={"frequency_band": band})
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

    def domain(self) -> str:
        """A root every group shares by default.

        The game refuses a board whose four groups share no ancestry, so a
        fixture of four unrelated groups would model a board that can never
        be built. Tests that mean to model that pass ``rooted=False``.
        """
        if "domain" not in self.categories:
            self.category("domain", taxonomy=LEXICAL)
        return "domain"

    def lexical_group(
        self, name: str, members: list[str], *, parent=None, rooted: bool = True
    ) -> None:
        if parent is None and rooted:
            parent = self.domain()
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


def direct_membership(argv):
    """The command, told these fixtures are hand-authored direct-membership
    taxonomies. The default follows game 1, which groups by siblings, and the
    fixtures below model the other kind on purpose.
    """
    if "--grouping" in argv:
        return measure_coverage.main(argv)
    return measure_coverage.main(["--grouping", "shared_category", *argv])


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

        code = direct_membership(["--db", str(db), "--group-size", "5"])

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

        code = direct_membership(["--db", str(db), "--group-size", "5"])

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

        direct_membership(
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
            direct_membership(["--db", str(tmp_path / "absent.sqlite")])


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

        code = direct_membership(
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

        code = direct_membership(
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

        code = direct_membership(
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

        direct_membership(
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

        direct_membership(
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


# -- sibling grouping ---------------------------------------------------------


def sibling_group(
    world: World,
    parent: str,
    kids: list[str],
    *,
    unrooted: bool = False,
    bands: dict | None = None,
) -> None:
    """A parent whose children each carry an entity named like themselves.

    The shape a hierarchy imported from WordNet has: the parent holds no
    members of its own, each child is a category, and each child's own name is
    an entity sitting in it.
    """
    world.category(parent, taxonomy=LEXICAL, parent=None if unrooted else world.domain())
    for kid in kids:
        world.category(kid, taxonomy=LEXICAL, parent=parent)
        world.entity(kid, (bands or {}).get(kid, "default"))
        world.member(kid, kid)


def workable_siblings(world: World) -> None:
    sibling_group(world, "cats", ["lion", "tiger", "puma", "lynx", "ocelot"])
    sibling_group(world, "birds", ["robin", "crane", "swift", "finch", "heron"])
    sibling_group(world, "tools", ["hammer", "chisel", "plane", "file", "awl"])
    sibling_group(world, "boats", ["ketch", "punt", "yawl", "dinghy", "canoe"])
    world.overlay_group("also_a_verb", ["crane", "swift", "file", "punt", "lynx"])


class TestSiblingCoverage:
    def test_the_same_graph_is_feasible_by_siblings_and_not_by_direct_members(
        self, world
    ):
        """The point of the mode, in one graph.

        No parent holds a member of its own, so under the old rule there is
        no group anywhere. Under siblings each parent supplies its five
        children.
        """
        workable_siblings(world)

        siblings = overlay_coverage.load(
            world.repos,
            lexical_taxonomy=LEXICAL,
            overlay_taxonomy=OVERLAY,
            grouping=overlay_coverage.SIBLINGS,
        )
        direct = overlay_coverage.load(
            world.repos, lexical_taxonomy=LEXICAL, overlay_taxonomy=OVERLAY
        )

        overlay_id = world.id_of("also_a_verb")
        assert overlay_coverage.assess(siblings, overlay_id, 5).feasible
        assert not overlay_coverage.assess(direct, overlay_id, 5).feasible

    def test_a_parent_is_made_of_its_childrens_representatives(self, world):
        workable_siblings(world)
        snapshot = overlay_coverage.load(
            world.repos,
            lexical_taxonomy=LEXICAL,
            overlay_taxonomy=OVERLAY,
            grouping=overlay_coverage.SIBLINGS,
        )

        cats = snapshot.lexical_members[world.id_of("cats")]
        assert {snapshot.entity_names[e] for e in cats} == {
            "lion", "tiger", "puma", "lynx", "ocelot"
        }

    def test_a_child_with_no_entity_named_like_it_is_not_represented(self, world):
        sibling_group(world, "fleet", ["a", "b", "c", "d"])
        world.category("e", taxonomy=LEXICAL, parent="fleet")
        world.entity("only-an-alias")
        world.member("only-an-alias", "e")
        snapshot = overlay_coverage.load(
            world.repos,
            lexical_taxonomy=LEXICAL,
            overlay_taxonomy=OVERLAY,
            grouping=overlay_coverage.SIBLINGS,
        )

        assert len(snapshot.lexical_members[world.id_of("fleet")]) == 4

    def test_an_unknown_grouping_is_refused(self, world):
        with pytest.raises(ValueError, match="unknown grouping"):
            overlay_coverage.load(
                world.repos,
                lexical_taxonomy=LEXICAL,
                overlay_taxonomy=OVERLAY,
                grouping="nonsense",
            )

    def test_it_agrees_with_the_content_service(self, world):
        """Two implementations of one rule, kept from drifting by running both.

        The service decides which entity stands for each child when it builds
        groups; this module decides it when it predicts them. If they ever
        disagree, the coverage numbers describe a game that is not the one
        that runs.
        """
        from puzzlegen.content.query import ContentQuery, Operation
        from puzzlegen.content.service import ContentService

        workable_siblings(world)
        sibling_group(world, "fleet", ["a", "b", "c"])
        world.category("d", taxonomy=LEXICAL, parent="fleet")
        world.entity("d-alias")
        world.member("d-alias", "d")

        snapshot = overlay_coverage.load(
            world.repos,
            lexical_taxonomy=LEXICAL,
            overlay_taxonomy=OVERLAY,
            grouping=overlay_coverage.SIBLINGS,
        )
        service = ContentService(world.repos)
        query = ContentQuery(operation=Operation.FIND_GROUPS, taxonomy=LEXICAL)
        pool, memberships = service._pool(query, None, {})
        index = service.taxonomy(LEXICAL)
        by_child = service._representatives(pool, memberships, index)

        expected: dict[str, set[str]] = {}
        for child_id, entity in by_child.items():
            for parent_id in index.get(child_id).parent_ids:
                expected.setdefault(parent_id, set()).add(entity.id)

        assert {k: set(v) for k, v in snapshot.lexical_members.items()} == expected

    def test_the_command_defaults_to_the_rule_the_game_uses(
        self, tmp_path, curated_source, capsys
    ):
        db = tmp_path / "graph.sqlite"
        repos = GraphRepositories(SqliteDocumentStore(db))
        try:
            workable_siblings(World(repos, curated_source))
        finally:
            repos.close()

        code = measure_coverage.main(["--db", str(db), "--group-size", "5"])

        assert code == 0
        assert "generatable: yes" in capsys.readouterr().out

    def test_the_default_grouping_is_the_games_own(self):
        from puzzlegen.games.grouping.content import VISIBLE_GROUPING

        assert str(VISIBLE_GROUPING) == "siblings"


class TestGroupsMustShareADomain:
    """Four groups with no common ancestry are four unrelated piles.

    The game's temptation floor refuses them, and separate export roots share
    no ancestors, so this decides which hidden groups a set of exports can
    ever support: one whose members are spread across roots has no board,
    however many homes it reaches.
    """

    def separate_roots(self, world: World) -> None:
        world.lexical_group("cats", ["lion", "tiger", "puma", "lynx", "ocelot"], rooted=False)
        world.lexical_group("birds", ["robin", "crane", "swift", "finch", "heron"], rooted=False)
        world.lexical_group("tools", ["hammer", "chisel", "plane", "file", "awl"], rooted=False)
        world.lexical_group("boats", ["ketch", "punt", "yawl", "dinghy", "canoe"], rooted=False)
        world.overlay_group("also_a_verb", ["crane", "swift", "file", "punt", "lynx"])

    def test_groups_from_separate_roots_are_not_a_board(self, world):
        self.separate_roots(world)
        snapshot = snapshot_of(world)

        finding = overlay_coverage.assess(snapshot, world.id_of("also_a_verb"), 5)

        assert not finding.feasible
        assert finding.reason == overlay_coverage.NO_SHARED_DOMAIN

    def test_the_same_groups_under_one_root_are_a_board(self, world):
        workable(world)
        snapshot = snapshot_of(world)

        assert overlay_coverage.assess(
            snapshot, world.id_of("also_a_verb"), 5
        ).feasible

    def test_the_reason_is_not_the_general_one(self, world):
        """A curator told "no valid quadruple" would grow content that was
        never the problem. This names the actual cause."""
        self.separate_roots(world)
        finding = overlay_coverage.assess(
            snapshot_of(world), world.id_of("also_a_verb"), 5
        )

        assert finding.reason != overlay_coverage.NO_VALID_QUADRUPLE

    def test_the_same_holds_for_sibling_groups(self, world):
        sibling_group(world, "cats", ["lion", "tiger", "puma", "lynx", "ocelot"], unrooted=True)
        sibling_group(world, "birds", ["robin", "crane", "swift", "finch", "heron"], unrooted=True)
        sibling_group(world, "tools", ["hammer", "chisel", "plane", "file", "awl"], unrooted=True)
        sibling_group(world, "boats", ["ketch", "punt", "yawl", "dinghy", "canoe"], unrooted=True)
        world.overlay_group("also_a_verb", ["crane", "swift", "file", "punt", "lynx"])
        snapshot = overlay_coverage.load(
            world.repos,
            lexical_taxonomy=LEXICAL,
            overlay_taxonomy=OVERLAY,
            grouping=overlay_coverage.SIBLINGS,
        )

        finding = overlay_coverage.assess(snapshot, world.id_of("also_a_verb"), 5)

        assert finding.reason == overlay_coverage.NO_SHARED_DOMAIN

    def test_the_floor_is_the_games_own(self):
        from puzzlegen.games.grouping.generate import MINIMUM_TEMPTATION

        assert overlay_coverage.MINIMUM_TEMPTATION == MINIMUM_TEMPTATION


class TestOneResemblingPairIsEnough:
    """The floor is a sum over pairs of groups, not a rule for every pair.

    ``temptation_of`` adds up shared ancestry across every pair of the four
    groups and the game asks only that the total reach ``MINIMUM_TEMPTATION``.
    So two groups from one root and two from other roots is a board. Stating
    the rule as "all four must share a domain" overstates it, and would rule
    out hidden groups that span domains for no reason.
    """

    def test_two_rooted_and_two_unrooted_groups_are_a_board(self, world):
        world.lexical_group("cats", ["lion", "tiger", "puma", "lynx", "ocelot"])
        world.lexical_group("birds", ["robin", "crane", "swift", "finch", "heron"])
        world.lexical_group(
            "tools", ["hammer", "chisel", "plane", "file", "awl"], rooted=False
        )
        world.lexical_group(
            "boats", ["ketch", "punt", "yawl", "dinghy", "canoe"], rooted=False
        )
        world.overlay_group("also_a_verb", ["crane", "swift", "file", "punt", "lynx"])

        finding = overlay_coverage.assess(
            snapshot_of(world), world.id_of("also_a_verb"), 5
        )

        assert finding.feasible

    def test_one_rooted_group_alone_is_not_enough(self, world):
        """One group under a root has nobody to resemble."""
        world.lexical_group("cats", ["lion", "tiger", "puma", "lynx", "ocelot"])
        world.lexical_group(
            "birds", ["robin", "crane", "swift", "finch", "heron"], rooted=False
        )
        world.lexical_group(
            "tools", ["hammer", "chisel", "plane", "file", "awl"], rooted=False
        )
        world.lexical_group(
            "boats", ["ketch", "punt", "yawl", "dinghy", "canoe"], rooted=False
        )
        world.overlay_group("also_a_verb", ["crane", "swift", "file", "punt", "lynx"])

        finding = overlay_coverage.assess(
            snapshot_of(world), world.id_of("also_a_verb"), 5
        )

        assert finding.reason == overlay_coverage.NO_SHARED_DOMAIN


# -- why a near miss misses ---------------------------------------------------


def siblings_snapshot(world: World):
    return overlay_coverage.load(
        world.repos,
        lexical_taxonomy=LEXICAL,
        overlay_taxonomy=OVERLAY,
        grouping=overlay_coverage.SIBLINGS,
    )


def eid(name: str) -> str:
    return ids.for_entity(name, "en")


class TestWhyAWordHasNoHome:
    """Three causes, three different fixes, one sentence each."""

    def test_a_synonym_is_named_as_one(self, world):
        """Both are tagged, and the word bearing the category's name wins."""
        sibling_group(world, "strings", ["violin", "viola", "cello", "bass", "harp"])
        world.entity("fiddle")
        world.member("fiddle", "violin")
        world.overlay_group("has_strings", ["fiddle", "violin"])

        why = overlay_coverage.explain_homeless(siblings_snapshot(world), eid("fiddle"), 5)

        assert "synonym of violin" in why

    def test_the_word_that_stands_for_a_child_is_not_a_synonym(self, world):
        sibling_group(world, "strings", ["violin", "viola", "cello", "bass", "harp"])
        world.overlay_group("has_strings", ["violin"])
        snapshot = siblings_snapshot(world)

        assert eid("violin") in snapshot.lexical_members[world.id_of("strings")]

    def test_a_small_parent_says_how_small(self, world):
        sibling_group(world, "boats", ["ketch", "punt"])
        world.overlay_group("floats", ["ketch"])

        why = overlay_coverage.explain_homeless(siblings_snapshot(world), eid("ketch"), 5)

        assert "a kind of boats" in why
        assert "has 2 kinds" in why
        assert "needs 5" in why

    def test_a_word_the_lexicon_lacks_is_named_as_one(self, world):
        world.entity("garlic")
        world.overlay_group("kitchen", ["garlic"])

        why = overlay_coverage.explain_homeless(siblings_snapshot(world), eid("garlic"), 5)

        assert "not in the lexicon" in why

    def test_a_top_of_tree_word_says_so(self, world):
        world.category("island", taxonomy=LEXICAL)
        world.entity("island")
        world.member("island", "island")
        world.overlay_group("land", ["island"])

        why = overlay_coverage.explain_homeless(siblings_snapshot(world), eid("island"), 5)

        assert "no parent" in why

    def test_the_direct_membership_rule_gets_its_own_sentence(self, world):
        world.lexical_group("cats", ["lion", "tiger"])
        world.overlay_group("roars", ["lion"])
        snapshot = overlay_coverage.load(
            world.repos, lexical_taxonomy=LEXICAL, overlay_taxonomy=OVERLAY
        )

        why = overlay_coverage.explain_homeless(snapshot, eid("lion"), 5)

        assert "in cats" in why and "has 2 members" in why and "needs 5" in why


class TestWhyNoBoardWasFound:
    """A category can reach every home it needs and still have no board."""

    def nested(self, world: World) -> None:
        """Parent B is itself a child of parent A.

        Every tile under B has A in its ancestry, so a board naming both
        parents makes each B tile belong to A's group as well.
        """
        sibling_group(world, "A", ["k1", "k2", "k3", "k4"])
        world.category("B", taxonomy=LEXICAL, parent="A")
        world.entity("B")
        world.member("B", "B")
        for kid in ["b1", "b2", "b3", "b4", "b5"]:
            world.category(kid, taxonomy=LEXICAL, parent="B")
            world.entity(kid)
            world.member(kid, kid)
        sibling_group(world, "C", ["c1", "c2", "c3", "c4", "c5"])
        sibling_group(world, "D", ["d1", "d2", "d3", "d4", "d5"])
        world.overlay_group("axis", ["b1", "k1", "c1", "d1", "c2"])

    def test_nested_parents_are_named_as_the_cause(self, world):
        self.nested(world)

        finding = overlay_coverage.assess(
            siblings_snapshot(world), world.id_of("axis"), 5
        )

        assert not finding.feasible
        assert finding.reason == overlay_coverage.NO_VALID_QUADRUPLE
        assert dict(finding.failures) == {overlay_coverage.CROSS_MEMBERSHIP: 1}

    def test_a_hidden_member_left_off_the_board_is_named(self, world):
        """Five homes, one member each: any four leave a member behind."""
        for parent in "PQRST":
            sibling_group(
                world, parent, [f"{parent.lower()}{n}" for n in range(1, 6)]
            )
        world.overlay_group("axis", ["p1", "q1", "r1", "s1", "t1"])

        finding = overlay_coverage.assess(
            siblings_snapshot(world), world.id_of("axis"), 5
        )

        assert finding.reason == overlay_coverage.NO_VALID_QUADRUPLE
        assert dict(finding.failures) == {overlay_coverage.OFF_BOARD: 5}

    def test_causes_are_most_common_first(self, world):
        for parent in "PQRST":
            sibling_group(
                world, parent, [f"{parent.lower()}{n}" for n in range(1, 6)]
            )
        world.overlay_group("axis", ["p1", "q1", "r1", "s1", "t1"])

        finding = overlay_coverage.assess(
            siblings_snapshot(world), world.id_of("axis"), 5
        )

        counts = [count for _, count in finding.failures]
        assert counts == sorted(counts, reverse=True)

    def test_a_feasible_category_reports_no_failures(self, world):
        workable_siblings(world)

        finding = overlay_coverage.assess(
            siblings_snapshot(world), world.id_of("also_a_verb"), 5
        )

        assert finding.feasible
        assert finding.failures == ()

    def test_a_search_that_never_ran_reports_none(self, world):
        sibling_group(world, "boats", ["ketch", "punt"])
        world.overlay_group("floats", ["ketch"])

        finding = overlay_coverage.assess(
            siblings_snapshot(world), world.id_of("floats"), 5
        )

        assert finding.reason == overlay_coverage.TOO_FEW_HIDDEN_MEMBERS
        assert finding.failures == ()


class TestTheReportExplainsItself:
    def build(self, path: Path, curated_source, builder) -> None:
        repos = GraphRepositories(SqliteDocumentStore(path))
        try:
            builder(World(repos, curated_source))
        finally:
            repos.close()

    def test_the_cause_and_the_homes_are_printed(
        self, tmp_path, curated_source, capsys
    ):
        db = tmp_path / "graph.sqlite"
        self.build(db, curated_source, TestWhyNoBoardWasFound().nested)

        code = measure_coverage.main(["--db", str(db), "--group-size", "5"])

        out = capsys.readouterr().out
        assert code == 1
        assert "reaches 4 homes (needs 4)" in out
        assert "homes: A, B, C, D" in out
        assert "why no board: hidden_member_also_in_another_chosen_parent 1" in out

    def test_a_homeless_word_is_explained_in_the_report(
        self, tmp_path, curated_source, capsys
    ):
        def strings(world: World) -> None:
            sibling_group(
                world, "strings", ["violin", "viola", "cello", "bass", "harp"]
            )
            world.entity("fiddle")
            world.member("fiddle", "violin")
            world.overlay_group(
                "has_strings", ["fiddle", "harp", "cello", "viola", "violin"]
            )

        db = tmp_path / "graph.sqlite"
        self.build(db, curated_source, strings)

        measure_coverage.main(["--db", str(db), "--group-size", "5"])

        out = capsys.readouterr().out
        assert "no usable home: fiddle" in out
        assert "fiddle: a synonym of violin" in out

    def test_reasons_appear_once_not_once_per_size(
        self, tmp_path, curated_source, capsys
    ):
        def strings(world: World) -> None:
            sibling_group(
                world, "strings", ["violin", "viola", "cello", "bass", "harp"]
            )
            world.entity("fiddle")
            world.member("fiddle", "violin")
            world.overlay_group(
                "has_strings", ["fiddle", "harp", "cello", "viola", "violin"]
            )

        db = tmp_path / "graph.sqlite"
        self.build(db, curated_source, strings)

        measure_coverage.main(
            ["--db", str(db), "--group-size", "5", "--group-size", "6"]
        )

        assert capsys.readouterr().out.count("fiddle: a synonym of violin") == 1

    def test_the_json_carries_the_explanations(
        self, tmp_path, curated_source, capsys
    ):
        db = tmp_path / "graph.sqlite"
        self.build(db, curated_source, TestWhyNoBoardWasFound().nested)
        out_path = tmp_path / "coverage.json"

        measure_coverage.main(
            ["--db", str(db), "--group-size", "5", "--json", str(out_path)]
        )
        capsys.readouterr()

        row = json.loads(out_path.read_text())["targets"]["5"][0]
        assert row["home_names"] == ["A", "B", "C", "D"]
        assert row["why_no_quadruple"] == [
            {"cause": "hidden_member_also_in_another_chosen_parent", "count": 1}
        ]

    def test_the_size_only_note_is_printed(self, tmp_path, curated_source, capsys):
        db = tmp_path / "graph.sqlite"
        self.build(db, curated_source, TestWhyNoBoardWasFound().nested)

        measure_coverage.main(["--db", str(db), "--group-size", "5"])

        assert "chosen by size alone" in capsys.readouterr().out


class TestTheCauseComesWithAnExample:
    """A count says something failed. The word and the parents say what."""

    def piano(self, world: World) -> None:
        """One word filed under two instrument families, as WordNet does."""
        sibling_group(world, "keyboard", ["harpsichord", "organ", "celesta", "clavichord"])
        sibling_group(world, "percussion", ["drum", "gong", "bell", "cymbal"])
        record = category(
            "piano",
            world.source,
            taxonomy=LEXICAL,
            parents=(world.categories["keyboard"], world.categories["percussion"]),
        )
        world.repos.categories.put(record)
        world.categories["piano"] = record
        world.entity("piano")
        world.member("piano", "piano")
        sibling_group(world, "wind", ["flute", "oboe", "horn", "fife", "reed"])
        sibling_group(world, "brass", ["tuba", "cornet", "bugle", "trombone", "sackbut"])
        world.overlay_group("axis", ["piano", "organ", "drum", "flute", "tuba"])

    def test_a_word_in_two_families_is_named_with_both(self, world):
        self.piano(world)

        finding = overlay_coverage.assess(
            siblings_snapshot(world), world.id_of("axis"), 5
        )

        assert finding.failures == ((overlay_coverage.IN_TWO_GROUPS, 1),)
        assert dict(finding.examples) == {
            overlay_coverage.IN_TWO_GROUPS: "piano is a kind of both keyboard and percussion"
        }

    def test_a_nested_example_names_the_word_and_both_parents(self, world):
        TestWhyNoBoardWasFound().nested(world)

        finding = overlay_coverage.assess(
            siblings_snapshot(world), world.id_of("axis"), 5
        )

        assert dict(finding.examples) == {
            overlay_coverage.CROSS_MEMBERSHIP: "b1 is under A as well as B"
        }

    def test_choices_that_leave_a_word_out_are_not_counted_as_causes(self, world):
        """Five homes on offer, and only the one choice that holds every word
        is asked why it fails.

        One hidden word is filed under both D and E, so five parents are in
        play. Of the five ways to choose four, four leave a hidden word with no
        home among them, and the count used to be mostly those. The fifth holds
        every word and fails on the nesting, which is the only thing worth
        reporting.
        """
        TestWhyNoBoardWasFound().nested(world)
        sibling_group(world, "E", ["e1", "e2", "e3", "e4", "e5"])
        record = category(
            "z",
            world.source,
            taxonomy=LEXICAL,
            parents=(world.categories["D"], world.categories["E"]),
        )
        world.repos.categories.put(record)
        world.categories["z"] = record
        world.entity("z")
        world.member("z", "z")
        world.overlay_group("wide", ["b1", "k1", "c1", "d1", "z"])

        finding = overlay_coverage.assess(
            siblings_snapshot(world), world.id_of("wide"), 5
        )

        assert not finding.feasible
        assert finding.examined == 5
        assert finding.failures == ((overlay_coverage.CROSS_MEMBERSHIP, 1),)

    def test_nested_homes_are_listed(self, world):
        TestWhyNoBoardWasFound().nested(world)
        snapshot = siblings_snapshot(world)
        homes = {world.id_of(n) for n in "ABCD"}

        assert measure_coverage.nested_pairs(snapshot, homes) == ["B is under A"]

    def test_peers_are_not_listed_as_nested(self, world):
        workable_siblings(world)
        snapshot = siblings_snapshot(world)
        homes = {world.id_of(n) for n in ("cats", "birds", "tools", "boats")}

        assert measure_coverage.nested_pairs(snapshot, homes) == []

    def test_the_report_prints_the_example_and_the_nesting(
        self, tmp_path, curated_source, capsys
    ):
        db = tmp_path / "graph.sqlite"
        repos = GraphRepositories(SqliteDocumentStore(db))
        try:
            TestWhyNoBoardWasFound().nested(World(repos, curated_source))
        finally:
            repos.close()

        measure_coverage.main(["--db", str(db), "--group-size", "5"])

        out = capsys.readouterr().out
        assert "e.g. hidden_member_also_in_another_chosen_parent: b1 is under A as well as B" in out
        assert "nested homes: B is under A" in out


class TestTheVerdictDoesNotDependOnIterationOrder:
    """A set iterates in a different order every run, so a diagnostic that
    returns the first problem it meets reports a different one each time."""

    def snapshot(self, doubled: str):
        return overlay_coverage.Snapshot(
            lexical_members={
                "P": frozenset({doubled}),
                "Q": frozenset({doubled}),
                "R": frozenset(),
                "S": frozenset(),
            },
            lexical_names={c: c for c in "PQRS"},
            overlay_members={},
            overlay_names={},
            types_of={},
            entity_names={},
        )

    def test_a_word_left_out_wins_whichever_word_sorts_first(self):
        """One word has no home among the four and another has two.

        Tried with the left out word sorting first and then last, so a check
        that stops at the first word it looks at gives different answers.
        """
        for left_out, doubled in (("a_out", "z_two"), ("z_out", "a_two")):
            verdict, _ = overlay_coverage._quadruple_works(
                self.snapshot(doubled),
                ("P", "Q", "R", "S"),
                frozenset({left_out, doubled}),
                5,
                {c: frozenset() for c in "PQRS"},
            )

            assert verdict == overlay_coverage.OFF_BOARD

    def test_two_words_in_two_groups_report_the_first_by_name(self):
        snapshot = overlay_coverage.Snapshot(
            lexical_members={
                "P": frozenset({"b", "c"}),
                "Q": frozenset({"b", "c"}),
                "R": frozenset(),
                "S": frozenset(),
            },
            lexical_names={c: c for c in "PQRS"},
            overlay_members={},
            overlay_names={},
            types_of={},
            entity_names={"b": "bee", "c": "cat"},
        )

        verdict, detail = overlay_coverage._quadruple_works(
            snapshot, ("P", "Q", "R", "S"), frozenset({"b", "c"}), 5,
            {c: frozenset() for c in "PQRS"},
        )

        assert verdict == overlay_coverage.IN_TWO_GROUPS
        assert detail == "bee is a kind of both P and Q"


class TestTheTaggedWordStandsForItsChild:
    """Option R in the coverage model: the curator's spelling is the tile."""

    def strings(self, world: World, tagged: list[str]) -> None:
        sibling_group(world, "strings", ["violin", "viola", "cello", "bass", "harp"])
        world.entity("fiddle")
        world.member("fiddle", "violin")
        world.overlay_group("has_strings", tagged)

    def test_a_tagged_synonym_is_the_tile(self, world):
        self.strings(world, ["fiddle", "harp"])
        snapshot = siblings_snapshot(world)

        tiles = snapshot.lexical_members[world.id_of("strings")]
        assert eid("fiddle") in tiles
        assert eid("violin") not in tiles

    def test_an_untagged_child_keeps_its_own_name(self, world):
        self.strings(world, ["fiddle"])
        snapshot = siblings_snapshot(world)

        tiles = snapshot.lexical_members[world.id_of("strings")]
        assert {eid(n) for n in ("viola", "cello", "bass", "harp")} <= tiles

    def test_when_both_are_tagged_the_name_bearing_word_wins(self, world):
        self.strings(world, ["fiddle", "violin"])
        snapshot = siblings_snapshot(world)

        tiles = snapshot.lexical_members[world.id_of("strings")]
        assert eid("violin") in tiles and eid("fiddle") not in tiles

    def test_nothing_tagged_means_the_first_lemma(self, world):
        self.strings(world, ["harp"])
        snapshot = siblings_snapshot(world)

        tiles = snapshot.lexical_members[world.id_of("strings")]
        assert eid("violin") in tiles and eid("fiddle") not in tiles

    def test_a_what_if_rebuilds_the_groups(self, world):
        """Tagging a word can change which word stands for its child, so the
        exact check cannot reuse the lexical structure it started with."""
        self.strings(world, ["harp"])
        snapshot = siblings_snapshot(world)
        overlay_id = world.id_of("has_strings")

        after = snapshot.with_membership(eid("fiddle"), overlay_id)

        strings = world.id_of("strings")
        assert eid("violin") in snapshot.lexical_members[strings]
        assert eid("fiddle") in after.lexical_members[strings]
        assert eid("violin") not in after.lexical_members[strings]
        assert after.representative[world.id_of("violin")] == eid("fiddle")

    def test_it_agrees_with_the_content_service_when_words_are_tagged(self, world):
        from puzzlegen.content.query import ContentQuery, Operation
        from puzzlegen.content.service import ContentService

        self.strings(world, ["fiddle", "harp"])
        snapshot = siblings_snapshot(world)
        service = ContentService(world.repos)
        pool, memberships = service._pool(
            ContentQuery(operation=Operation.FIND_GROUPS, taxonomy=LEXICAL), None, {}
        )
        index = service.taxonomy(LEXICAL)
        carrying = frozenset(
            e.id
            for e in pool
            if any(c in service.taxonomy(OVERLAY) for c in memberships[e.id])
        )

        chosen = service._representatives(pool, memberships, index, carrying)

        assert {cid: e.id for cid, e in chosen.items()} == snapshot.representative


class TestTheReportNamesWhatCanBeBuilt:
    """A list of failures says nothing about the one thing that works."""

    def build(self, path: Path, curated_source, builder) -> None:
        repos = GraphRepositories(SqliteDocumentStore(path))
        try:
            builder(World(repos, curated_source))
        finally:
            repos.close()

    def test_a_buildable_category_is_named_with_its_visible_groups(
        self, tmp_path, curated_source, capsys
    ):
        db = tmp_path / "graph.sqlite"
        self.build(db, curated_source, workable_siblings)

        measure_coverage.main(["--db", str(db), "--group-size", "5"])

        line = next(
            l for l in capsys.readouterr().out.splitlines() if "can be built" in l
        )
        assert line.startswith("  size 5 can be built: also_a_verb")
        for parent in ("cats", "birds", "tools", "boats"):
            assert parent in line

    def test_nothing_is_claimed_when_nothing_can_be_built(
        self, tmp_path, curated_source, capsys
    ):
        db = tmp_path / "graph.sqlite"
        self.build(db, curated_source, TestWhyNoBoardWasFound().nested)

        measure_coverage.main(["--db", str(db), "--group-size", "5"])

        assert "can be built" not in capsys.readouterr().out

    def test_the_json_lists_the_buildable_boards(
        self, tmp_path, curated_source, capsys
    ):
        db = tmp_path / "graph.sqlite"
        self.build(db, curated_source, workable_siblings)
        out = tmp_path / "coverage.json"

        measure_coverage.main(
            ["--db", str(db), "--group-size", "5", "--json", str(out)]
        )
        capsys.readouterr()

        [size] = json.loads(out.read_text())["summary"]["sizes"]
        [board] = size["feasible_boards"]
        assert board["hidden_group"] == "also_a_verb"
        assert sorted(board["parents"]) == ["birds", "boats", "cats", "tools"]

    def chain(self, world: World) -> None:
        """Three homes in one line of descent: A, B under A, C under B."""
        sibling_group(world, "A", ["k1", "k2", "k3", "k4"])
        world.category("B", taxonomy=LEXICAL, parent="A")
        world.entity("B")
        world.member("B", "B")
        for kid in ["b1", "b2", "b3", "b4"]:
            world.category(kid, taxonomy=LEXICAL, parent="B")
            world.entity(kid)
            world.member(kid, kid)
        world.category("C", taxonomy=LEXICAL, parent="B")
        world.entity("C")
        world.member("C", "C")
        for kid in ["c1", "c2", "c3", "c4", "c5"]:
            world.category(kid, taxonomy=LEXICAL, parent="C")
            world.entity(kid)
            world.member(kid, kid)
        world.overlay_group("chain", ["k1", "b1", "c1", "c2", "c3"])

    def test_nesting_is_shown_when_the_reason_is_too_few_homes(
        self, tmp_path, curated_source, capsys
    ):
        """Three homes in a line are not three peers, whatever the count says.

        The reason here is the count, not the nesting, and the nesting is the
        part a curator needs: adding a fourth word in the same family cannot
        help, because the family is one line.
        """
        db = tmp_path / "graph.sqlite"
        self.build(db, curated_source, self.chain)

        measure_coverage.main(["--db", str(db), "--group-size", "5"])

        out = capsys.readouterr().out
        assert "[too_few_distinct_homes]" in out
        assert "nested homes: B is under A; C is under A; C is under B" in out


# -- nearest home only --------------------------------------------------------


class TestAWordServesItsNearestCategoryOnly:
    """``bass guitar`` belongs to ``guitar``, not to ``stringed instrument``.

    A word is a tile for its nearest category, so it can serve a group no
    larger than that category has kinds. Nothing pulls it up a level to make a
    bigger group, because then a board naming both ``guitar`` and
    ``stringed instrument`` would have one word in two groups.
    """

    def instruments(self, world: World) -> None:
        sibling_group(
            world,
            "stringed",
            ["guitar", "banjo", "harp", "lute", "sitar", "zither", "koto", "piano"],
        )
        world.category("bowed", taxonomy=LEXICAL, parent="stringed")
        world.entity("bowed")
        world.member("bowed", "bowed")
        sibling_group(world, "guitar_kinds", ["acoustic", "electric", "bass_guitar", "hawaiian"])
        sibling_group(world, "bowed_kinds", ["violin", "cello", "viol", "bass_fiddle"])

    def test_a_grandchild_is_not_a_tile_of_its_grandparent(self, world):
        sibling_group(world, "stringed", ["guitar", "banjo", "harp", "lute", "sitar"])
        world.category("bass_guitar", taxonomy=LEXICAL, parent="guitar")
        world.entity("bass_guitar")
        world.member("bass_guitar", "bass_guitar")
        world.overlay_group("axis", ["bass_guitar"])
        snapshot = siblings_snapshot(world)

        assert eid("bass_guitar") not in snapshot.lexical_members[world.id_of("stringed")]
        assert eid("guitar") in snapshot.lexical_members[world.id_of("stringed")]

    def test_a_words_ceiling_is_the_size_of_its_nearest_category(self, world):
        sibling_group(world, "big", [f"k{n}" for n in range(1, 9)])
        sibling_group(world, "small", ["s1", "s2", "s3", "s4"])
        world.entity("orphan")
        world.overlay_group("axis", ["k1", "s1", "orphan"])

        found = measure_coverage.ceilings(siblings_snapshot(world), world.id_of("axis"))

        assert found == {"k1": 8, "s1": 4, "orphan": 0}

    def test_a_synonym_that_is_not_the_tile_has_a_ceiling_of_zero(self, world):
        sibling_group(world, "strings", ["violin", "viola", "cello", "bass", "harp"])
        world.entity("fiddle")
        world.member("fiddle", "violin")
        world.overlay_group("axis", ["fiddle", "violin"])

        found = measure_coverage.ceilings(siblings_snapshot(world), world.id_of("axis"))

        assert found == {"fiddle": 0, "violin": 5}

    def test_a_word_in_two_homes_takes_the_larger(self, world):
        sibling_group(world, "one", ["a1", "a2", "a3", "a4", "a5", "a6", "a7"])
        sibling_group(world, "two", ["b1", "b2", "b3", "b4", "b5"])
        record = category(
            "both",
            world.source,
            taxonomy=LEXICAL,
            parents=(world.categories["one"], world.categories["two"]),
        )
        world.repos.categories.put(record)
        world.categories["both"] = record
        world.entity("both")
        world.member("both", "both")
        world.overlay_group("axis", ["both"])

        found = measure_coverage.ceilings(siblings_snapshot(world), world.id_of("axis"))

        assert found == {"both": 8}

    def test_the_report_prints_the_ceilings_once(self, tmp_path, curated_source, capsys):
        def build(world: World) -> None:
            sibling_group(world, "big", [f"k{n}" for n in range(1, 9)])
            sibling_group(world, "small", ["s1", "s2", "s3", "s4"])
            world.entity("orphan")
            world.overlay_group("axis", ["k1", "s1", "orphan"])

        db = tmp_path / "graph.sqlite"
        repos = GraphRepositories(SqliteDocumentStore(db))
        try:
            build(World(repos, curated_source))
        finally:
            repos.close()

        measure_coverage.main(
            ["--db", str(db), "--group-size", "5", "--group-size", "6"]
        )

        out = capsys.readouterr().out
        line = "largest group each word can be a tile in: k1 8, s1 4, orphan 0"
        assert out.count(line) == 1

    def test_the_json_carries_the_ceilings(self, tmp_path, curated_source, capsys):
        def build(world: World) -> None:
            sibling_group(world, "big", [f"k{n}" for n in range(1, 9)])
            world.overlay_group("axis", ["k1"])

        db = tmp_path / "graph.sqlite"
        repos = GraphRepositories(SqliteDocumentStore(db))
        try:
            build(World(repos, curated_source))
        finally:
            repos.close()
        out = tmp_path / "coverage.json"

        measure_coverage.main(["--db", str(db), "--group-size", "5", "--json", str(out)])
        capsys.readouterr()

        row = json.loads(out.read_text())["targets"]["5"][0]
        assert row["ceilings"] == {"k1": 8}

    def test_the_rule_is_stated_at_the_end_of_the_report(
        self, tmp_path, curated_source, capsys
    ):
        db = tmp_path / "graph.sqlite"
        repos = GraphRepositories(SqliteDocumentStore(db))
        try:
            TestWhyNoBoardWasFound().nested(World(repos, curated_source))
        finally:
            repos.close()

        measure_coverage.main(["--db", str(db), "--group-size", "5"])

        assert "a word is a tile only for its nearest category" in capsys.readouterr().out


class TestTheCapacityColumn:
    def test_the_report_counts_parents_able_to_supply_each_size(
        self, tmp_path, curated_source, capsys
    ):
        db = tmp_path / "graph.sqlite"
        repos = GraphRepositories(SqliteDocumentStore(db))
        try:
            workable_siblings(World(repos, curated_source))
        finally:
            repos.close()
        out = tmp_path / "coverage.json"

        measure_coverage.main(
            ["--db", str(db), "--group-size", "5", "--group-size", "6", "--json", str(out)]
        )
        capsys.readouterr()

        sizes = {s["group_size"]: s for s in json.loads(out.read_text())["summary"]["sizes"]}
        assert sizes[5]["parents_able"] == 4
        assert sizes[6]["parents_able"] == 0


# -- the game's frequency filter ---------------------------------------------

from puzzlegen.core.types import FrequencyBand  # noqa: E402

MEDIUM = frozenset({"COMMON", "UNCOMMON"})
COMMON = FrequencyBand.COMMON
RARE = FrequencyBand.RARE


def filtered(world: World, allowed=MEDIUM, difficulty="medium"):
    return overlay_coverage.load(
        world.repos,
        lexical_taxonomy=LEXICAL,
        overlay_taxonomy=OVERLAY,
        grouping=overlay_coverage.SIBLINGS,
        allowed_bands=allowed,
        difficulty=difficulty,
    )


class TestTheGamesFrequencyFilter:
    """The game drops every tile outside the difficulty's bands, and a coverage
    model that ignores that reports boards the game can never build.

    On the real snapshot the structural model said a size 5 board existed while
    all thirty days failed, because the kinds that would have made the groups
    were too rare for MEDIUM. Nothing was wrong with either tool: one answered
    a question the game never asks.
    """

    def kinds(self, n=5, rare=()):
        names = [f"k{i}" for i in range(n)]
        return names, {k: (RARE if k in rare else COMMON) for k in names}

    def test_no_filter_counts_every_word_even_unscored_ones(self, world):
        names, _ = self.kinds()
        sibling_group(world, "p", names, bands={k: None for k in names})

        assert len(siblings_snapshot(world).lexical_members[world.id_of("p")]) == 5

    def test_a_rare_kind_is_not_a_tile_at_medium(self, world):
        names, bands = self.kinds(5, rare=("k0",))
        sibling_group(world, "p", names, bands=bands)

        tiles = filtered(world).lexical_members[world.id_of("p")]

        assert eid("k0") not in tiles and len(tiles) == 4

    def test_so_a_parent_that_was_big_enough_no_longer_is(self, world):
        names, bands = self.kinds(5, rare=("k0",))
        sibling_group(world, "p", names, bands=bands)

        assert siblings_snapshot(world).usable_lexical(5) != []
        assert filtered(world).usable_lexical(5) == []

    def test_an_unscored_word_is_never_a_tile_under_a_filter(self, world):
        names, _ = self.kinds()
        sibling_group(world, "p", names, bands={k: None for k in names})

        assert filtered(world).lexical_members.get(world.id_of("p"), frozenset()) == frozenset()

    def test_a_common_parent_still_supplies_a_group(self, world):
        names, bands = self.kinds()
        sibling_group(world, "p", names, bands=bands)

        assert filtered(world).usable_lexical(5) == [world.id_of("p")]

    def test_overlay_membership_is_not_filtered(self, world):
        """The hidden words are found by noticing a second meaning, so the game
        does not filter them by frequency. Only tiles are."""
        names, bands = self.kinds(5, rare=("k0",))
        sibling_group(world, "p", names, bands=bands)
        world.overlay_group("axis", ["k0", "k1"])

        snapshot = filtered(world)

        assert eid("k0") in snapshot.overlay_members[world.id_of("axis")]
        assert eid("k0") not in snapshot.lexical_members[world.id_of("p")]

    def test_a_rare_tagged_synonym_does_not_take_the_tile(self, world):
        """Preferring the tagged word must not promote a word the filter removes."""
        sibling_group(
            world, "strings", ["violin", "viola", "cello", "bass", "harp"],
            bands={k: COMMON for k in ("violin", "viola", "cello", "bass", "harp")},
        )
        world.entity("fiddle", RARE)
        world.member("fiddle", "violin")
        world.overlay_group("axis", ["fiddle"])

        tiles = filtered(world).lexical_members[world.id_of("strings")]

        assert eid("violin") in tiles and eid("fiddle") not in tiles

    def test_a_rare_overlay_word_is_explained_as_too_uncommon(self, world):
        names, bands = self.kinds(5, rare=("k0",))
        sibling_group(world, "p", names, bands=bands)
        world.overlay_group("axis", ["k0"])

        why = overlay_coverage.explain_homeless(filtered(world), eid("k0"), 5)

        assert "too uncommon for medium" in why
        assert "RARE" in why and "COMMON, UNCOMMON" in why

    def test_an_unscored_word_says_so(self, world):
        names, _ = self.kinds()
        sibling_group(world, "p", names, bands={k: None for k in names})
        world.overlay_group("axis", ["k0"])

        why = overlay_coverage.explain_homeless(filtered(world), eid("k0"), 5)

        assert "frequency band is unscored" in why

    def test_a_word_the_lexicon_lacks_is_still_not_called_uncommon(self, world):
        world.entity("garlic")
        world.overlay_group("axis", ["garlic"])

        why = overlay_coverage.explain_homeless(filtered(world), eid("garlic"), 5)

        assert "not in the lexicon" in why

    def test_a_parent_that_shrank_says_how_much_was_the_filter(self, world):
        """Rarity's doing and WordNet's are fixed in different places."""
        names, bands = self.kinds(6, rare=("k5",))
        sibling_group(world, "p", names, bands=bands)
        world.overlay_group("axis", ["k0"])

        why = overlay_coverage.explain_homeless(filtered(world), eid("k0"), 6)

        assert "has 5 kinds (6 before the frequency filter) and needs 6" in why

    def test_a_parent_that_was_always_small_makes_no_such_claim(self, world):
        names, bands = self.kinds(4)
        sibling_group(world, "p", names, bands=bands)
        world.overlay_group("axis", ["k0"])

        why = overlay_coverage.explain_homeless(filtered(world), eid("k0"), 5)

        assert "before the frequency filter" not in why
        assert "has 4 kinds and needs 5" in why

    def test_a_what_if_keeps_the_filter(self, world):
        names, bands = self.kinds()
        sibling_group(world, "p", names, bands=bands)
        world.overlay_group("axis", ["k0"])
        snapshot = filtered(world)

        after = snapshot.with_membership(eid("k1"), world.id_of("axis"))

        assert after.allowed_bands == MEDIUM and after.difficulty == "medium"
        assert after.kinds_before_filter == snapshot.kinds_before_filter


class TestTheDifficultyFlag:
    def build(self, tmp_path, curated_source, rare_one=False):
        db = tmp_path / "graph.sqlite"
        repos = GraphRepositories(SqliteDocumentStore(db))
        try:
            world = World(repos, curated_source)
            names = [f"k{i}" for i in range(5)]
            bands = {k: (RARE if rare_one and k == "k0" else COMMON) for k in names}
            sibling_group(world, "p", names, bands=bands)
            for other in ("q", "r", "s"):
                sibling_group(world, other, [f"{other}{i}" for i in range(5)],
                              bands={f"{other}{i}": COMMON for i in range(5)})
            world.overlay_group("axis", ["k1", "q1", "r1", "s1", "k2"])
        finally:
            repos.close()
        return db

    def test_medium_builds_a_board_from_common_words(self, tmp_path, curated_source, capsys):
        db = self.build(tmp_path, curated_source)

        code = measure_coverage.main(
            ["--db", str(db), "--group-size", "5", "--difficulty", "medium"]
        )

        assert code == 0
        assert "generatable: yes" in capsys.readouterr().out

    def test_one_rare_kind_removes_the_board(self, tmp_path, curated_source, capsys):
        db = self.build(tmp_path, curated_source, rare_one=True)

        with_filter = measure_coverage.main(
            ["--db", str(db), "--group-size", "5", "--difficulty", "medium"]
        )
        capsys.readouterr()
        without = measure_coverage.main(
            ["--db", str(db), "--group-size", "5", "--difficulty", "any"]
        )

        assert with_filter == 1 and without == 0

    def test_the_header_states_what_is_being_filtered(self, tmp_path, curated_source, capsys):
        db = self.build(tmp_path, curated_source)

        measure_coverage.main(["--db", str(db), "--group-size", "5", "--difficulty", "medium"])

        out = capsys.readouterr().out
        assert "difficulty medium: only words in bands COMMON, UNCOMMON can be tiles" in out

    def test_any_says_it_is_an_upper_bound(self, tmp_path, curated_source, capsys):
        db = self.build(tmp_path, curated_source)

        measure_coverage.main(["--db", str(db), "--group-size", "5"])

        out = capsys.readouterr().out
        assert "difficulty any: no frequency filter" in out
        assert "upper bound" in out

    def test_the_json_records_the_filter(self, tmp_path, curated_source, capsys):
        db = self.build(tmp_path, curated_source)
        out = tmp_path / "c.json"

        measure_coverage.main(
            ["--db", str(db), "--group-size", "5", "--difficulty", "hard", "--json", str(out)]
        )
        capsys.readouterr()

        summary = json.loads(out.read_text())["summary"]
        assert summary["difficulty"] == "hard"
        assert summary["allowed_bands"] == ["RARE", "UNCOMMON"]
        assert summary["entities_usable_as_tiles"] == 0

    def test_an_unknown_difficulty_is_refused(self, tmp_path, curated_source):
        db = self.build(tmp_path, curated_source)

        with pytest.raises(SystemExit):
            measure_coverage.main(["--db", str(db), "--difficulty", "expert"])
