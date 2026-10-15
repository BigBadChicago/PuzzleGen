"""Cross-board information flow, as a quantity.

The figures below are hand computed, which is the point of testing a
measurement rather than a boolean: a boolean is right or wrong, and a
quantity that is subtly wrong in the middle of its range would pass a
boolean's tests.
"""

from __future__ import annotations

import pytest

from puzzlegen.engine.certifiers import digit_lock, odd_number_out, shape_trio
from puzzlegen.engine.coupling import (
    CouplingTarget,
    DailySetDeclaration,
    PairIntent,
    Window,
    EnumeratedSpace,
    Fact,
    check_day,
    default_spaces,
    independent_targets,
    measure_day,
    measure_leak,
    read_declaration,
    spaces_for_day,
    write_declaration,
)

from tests.unit.test_certify import (
    ATTRIBUTES,
    FOUR_TILES,
    digit_board,
    narrowing_clues,
    numbers_board,
    reading,
    trio_board,
)


def as_document_of(reports, not_measured=()):
    """The committed artifact's own serializer, imported where it lives.

    The tool is not a package, so it is loaded by path rather than imported:
    testing a reimplementation of the serializer would test the wrong thing,
    since the point is the shape of the file CI compares.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    try:
        import measure_coupling
    finally:
        sys.path.pop(0)
    return measure_coupling.as_document(reports, not_measured)


#: The predicate the arithmetic tests use. One predicate, so every space
#: below reads every other's facts and the figures are about the arithmetic
#: rather than about predicate matching, which has its own tests.
P = "test/is_answer"


def fact(ref, value=True, predicate=P):
    return Fact(predicate=predicate, ref=ref, value=value)


def tile_space(answers, revealed=(), predicates=frozenset({P})):
    """A space over single-tile answers, for the arithmetic tests."""
    return EnumeratedSpace(
        answers=tuple(answers),
        admits=lambda answer, f: (
            (f.ref == answer) if f.value else (f.ref != answer)
        ),
        revealed=tuple(revealed),
        predicates=frozenset(predicates),
    )


class TestTheArithmetic:
    def test_a_board_coupled_to_itself_measures_one(self):
        """The upper bound, and the sanity check on the definition."""
        space = tile_space(
            ["a", "b", "c", "d"],
            revealed=[fact("a"), fact("b", False), fact("c", False)],
        )
        measured = measure_leak(
            space, space, source_game_id="x", target_game_id="y"
        )
        assert measured.space_before == 4
        assert measured.space_after == 1
        assert measured.leak == pytest.approx(0.75)
        # Narrowing four answers to one is 0.75, not 1.0: the target still
        # has an answer. A leak of exactly 1.0 means nothing survives.
        assert not measured.gives_it_away

    def test_a_source_that_rules_out_every_answer_gives_the_board_away(self):
        source = tile_space(["z"], revealed=[fact("a", False), fact("b", False)])
        measured = measure_leak(
            source, tile_space(["a", "b"]), source_game_id="x", target_game_id="y"
        )
        assert measured.leak == 1.0
        assert measured.gives_it_away

    def test_two_boards_sharing_no_entity_measure_zero(self):
        """The source rules out a ref the target's space never held."""
        source = tile_space(["p"], revealed=[fact("p", False)])
        target = tile_space(["a", "b", "c"])
        measured = measure_leak(
            source, target, source_game_id="x", target_game_id="y"
        )
        assert measured.leak == 0.0
        assert measured.independent
        assert measured.space_before == measured.space_after == 3

    def test_a_hand_computed_partial_leak(self):
        """Six answers, two ruled out, so a third of the space goes."""
        source = tile_space(
            ["z"], revealed=[fact("b", False), fact("e", False)]
        )
        target = tile_space(["a", "b", "c", "d", "e", "f"])
        measured = measure_leak(
            source, target, source_game_id="x", target_game_id="y"
        )
        assert measured.space_before == 6
        assert measured.space_after == 4
        assert measured.leak == pytest.approx(1 / 3)
        assert not measured.independent
        assert not measured.gives_it_away

    def test_the_measurement_is_directed(self):
        """A can constrain B while B leaves A untouched."""
        constrains = tile_space(["a"], revealed=[fact("a")])
        constrained = tile_space(["a", "b"])
        forward = measure_leak(
            constrains, constrained, source_game_id="a", target_game_id="b"
        )
        backward = measure_leak(
            constrained, constrains, source_game_id="b", target_game_id="a"
        )
        assert forward.leak == pytest.approx(0.5)
        assert backward.leak == 0.0

    def test_the_original_space_is_never_mutated(self):
        target = tile_space(["a", "b", "c"])
        measure_leak(
            tile_space(["z"], revealed=[fact("a", False)]),
            target,
            source_game_id="x",
            target_game_id="y",
        )
        assert len(list(target.enumerate())) == 3


class TestUnknownIsNotSafe:
    def test_a_non_enumerable_game_reports_none(self):
        measured = measure_leak(
            None, tile_space(["a"]), source_game_id="x", target_game_id="y"
        )
        assert measured.leak is None
        assert not measured.known

    def test_an_unknown_measurement_is_not_independent(self):
        """The one place the rule has to be written in code.

        A safety gate reads ``independent``, and an unknown that reported
        independence is how a leak ships.
        """
        measured = measure_leak(
            None, tile_space(["a"]), source_game_id="x", target_game_id="y"
        )
        assert measured.independent is False

    def test_an_unknown_satisfies_no_declaration_including_the_default(self):
        measured = measure_leak(
            None, tile_space(["a"]), source_game_id="x", target_game_id="y"
        )
        assert not CouplingTarget("x", "y").admits(measured)
        assert not CouplingTarget("x", "y", minimum=0.0, maximum=1.0).admits(
            measured
        )

    def test_an_empty_target_space_is_unknown_not_independent(self):
        """Dividing by zero would report 0.0, which reads as independence."""
        measured = measure_leak(
            tile_space(["z"], revealed=[fact("a")]),
            tile_space([]),
            source_game_id="x",
            target_game_id="y",
        )
        assert measured.leak is None
        assert measured.space_before == 0

    def test_a_fact_the_target_cannot_read_is_not_applied(self):
        """The bug a measurement over thirty real boards caught.

        Without the predicate, two games sharing entities and no rules read
        each other's "not in my answer" as their own and measured a leak of
        1.0. The target now reads only the predicates it defines.
        """
        source = tile_space(
            ["a"], revealed=[fact("a", False, predicate="other/claim")]
        )
        measured = measure_leak(
            source, tile_space(["a", "b"]), source_game_id="x", target_game_id="y"
        )
        assert measured.leak == 0.0
        assert measured.facts_applied == 0
        assert "reads none of the" in measured.detail


class TestDeclarations:
    def test_the_independent_default_admits_only_a_measured_zero(self):
        target = CouplingTarget("x", "y")
        assert target.admits(
            measure_leak(
                tile_space(["p"], revealed=[fact("p", False)]),
                tile_space(["a", "b"]),
                source_game_id="x",
                target_game_id="y",
            )
        )
        assert not target.admits(
            measure_leak(
                tile_space(["a"], revealed=[fact("a", False)]),
                tile_space(["a", "b"]),
                source_game_id="x",
                target_game_id="y",
            )
        )

    def test_a_declared_window_admits_a_partial_leak(self):
        """The mechanic, which a boolean could not express."""
        measured = measure_leak(
            tile_space(["z"], revealed=[fact("a", False)]),
            tile_space(["a", "b", "c", "d"]),
            source_game_id="x",
            target_game_id="y",
        )
        assert measured.leak == pytest.approx(0.25)
        assert CouplingTarget("x", "y", minimum=0.1, maximum=0.4).admits(measured)
        assert not CouplingTarget("x", "y", minimum=0.3, maximum=0.5).admits(
            measured
        )

    def test_an_impossible_window_is_refused_at_construction(self):
        with pytest.raises(ValueError, match="cannot exceed its maximum"):
            CouplingTarget("x", "y", minimum=0.5, maximum=0.2)
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            CouplingTarget("x", "y", maximum=1.5)

    def test_independent_targets_covers_every_ordered_pair(self):
        targets = independent_targets(["a", "b", "c"])
        assert len(targets) == 6
        assert all(t.maximum == 0.0 for t in targets)
        assert ("a", "b") in {(t.source_game_id, t.target_game_id) for t in targets}
        assert ("b", "a") in {(t.source_game_id, t.target_game_id) for t in targets}


class TestADaysReport:
    def spaces(self):
        # alpha rules out one of beta's two answers and none of gamma's, so
        # the day has exactly one leaking ordered pair.
        return {
            "alpha": tile_space(["a"], revealed=[fact("b", False)]),
            "beta": tile_space(["a", "b"]),
            "gamma": tile_space(["p", "q"]),
        }

    def test_every_ordered_pair_is_measured(self):
        measurements = measure_day(self.spaces())
        assert len(measurements) == 6

    def test_with_no_declarations_it_reports_and_gates_nothing(self):
        """The honest state while nobody has said what coupling should be."""
        report = check_day("2026-10-01", self.spaces())
        assert report.passes
        assert report.violations == ()
        assert len(report.leaking) == 1
        assert report.leaking[0].source_game_id == "alpha"

    def test_with_declarations_the_same_call_is_the_gate(self):
        report = check_day(
            "2026-10-01", self.spaces(), independent_targets(self.spaces())
        )
        assert not report.passes
        offenders = {
            (t.source_game_id, t.target_game_id) for t, _ in report.violations
        }
        assert ("alpha", "beta") in offenders
        assert any("VIOLATION" in line for line in report.describe())

    def test_a_game_that_cannot_report_is_refused_for_coupling(self):
        spaces = {**self.spaces(), "delta": None}
        report = check_day("2026-10-01", spaces, independent_targets(spaces))
        refused = {
            (t.source_game_id, t.target_game_id) for t, _ in report.violations
        }
        assert ("delta", "beta") in refused
        assert ("beta", "delta") in refused
        assert report.unknown

    def test_a_declaration_about_a_pair_not_on_the_board_is_reported(self):
        report = check_day(
            "2026-10-01", self.spaces(), [CouplingTarget("alpha", "absent")]
        )
        assert not report.passes
        assert "no such pair" in report.violations[0][1].detail


class TestTheSymbolicSpaces:
    """The three games' own spaces, built from their real board shapes."""

    def test_every_shipped_game_has_a_space_builder(self):
        assert sorted(default_spaces()) == [
            "digit_lock",
            "odd_number_out",
            "shape_trio",
        ]

    def test_a_trio_boards_space_is_its_valid_trios(self):
        payload, solution = trio_board(FOUR_TILES, ["a", "b", "c"])
        space = shape_trio.space(payload, solution)
        assert [sorted(answer) for answer in space.enumerate()] == [
            ["a", "b", "c"]
        ]

    def test_a_trio_boards_facts_cover_every_tile_in_and_out(self):
        """A board's answer tells you about the tiles it excludes too."""
        payload, solution = trio_board(FOUR_TILES, ["a", "b", "c"])
        revealed = shape_trio.space(payload, solution).facts()
        assert {f.ref: f.value for f in revealed} == {
            "a": True,
            "b": True,
            "c": True,
            "d": False,
        }
        assert {f.predicate for f in revealed} == {shape_trio.IN_TRIO}

    def test_a_target_space_built_without_a_solution_reveals_nothing(self):
        payload, _ = trio_board(FOUR_TILES, ["a", "b", "c"])
        assert tuple(shape_trio.space(payload).facts()) == ()

    def test_two_trio_boards_over_the_same_tiles_couple_completely(self):
        payload, solution = trio_board(FOUR_TILES, ["a", "b", "c"])
        measured = measure_leak(
            shape_trio.space(payload, solution),
            shape_trio.space(payload),
            source_game_id="shape_trio",
            target_game_id="shape_trio_2",
        )
        # One answer before and one after: knowing it changes nothing a
        # solver did not already have, because the board has one trio.
        assert measured.leak == 0.0

    def test_a_hand_built_pair_with_a_known_shared_constraint(self):
        """Two odd_number_out boards over the same tiles.

        The target admits two tiles; the source's answer rules one of them
        out, so exactly half the space goes. Same game, so the predicate is
        shared and the figure is defined.
        """
        source_payload, source_solution = numbers_board(
            answer="c",
            memberships={
                "a": ["even", "small"],
                "b": ["even", "small"],
                "c": ["even"],
                "d": ["even", "small"],
            },
        )
        target_payload, target_solution = numbers_board(
            answer="d",
            memberships={
                "a": ["even", "small"],
                "b": ["even", "small"],
                "c": ["even"],
                "d": ["small"],
            },
        )
        target = odd_number_out.space(target_payload, target_solution)
        assert sorted(target.enumerate()) == ["c", "d"]
        measured = measure_leak(
            odd_number_out.space(source_payload, source_solution),
            target,
            source_game_id="odd_number_out",
            target_game_id="odd_number_out_2",
        )
        assert measured.space_before == 2
        assert measured.space_after == 1
        assert measured.leak == pytest.approx(0.5)

    def test_two_games_that_share_tiles_but_no_predicate_measure_zero(self):
        """The real finding from the thirty-day run.

        A trio board and a numbers board can draw the same entities and still
        tell each other nothing: "not in my trio" is not "not the odd one
        out". The figure is a measured zero, not an unknown and not a 1.0.
        """
        trio_payload, trio_solution = trio_board(FOUR_TILES, ["a", "b", "c"])
        numbers_payload, numbers_solution = numbers_board(
            answer="d",
            memberships={
                "a": ["even", "small"],
                "b": ["even", "small"],
                "c": ["even"],
                "d": ["small"],
            },
        )
        measured = measure_leak(
            shape_trio.space(trio_payload, trio_solution),
            odd_number_out.space(numbers_payload, numbers_solution),
            source_game_id="shape_trio",
            target_game_id="odd_number_out",
        )
        assert measured.leak == 0.0
        assert measured.facts_applied == 0

    def test_an_odd_one_out_space_is_the_admissible_tiles_not_every_tile(self):
        """A leak measured against the wrong denominator is the wrong number."""
        payload, solution = numbers_board()
        space = odd_number_out.space(payload, solution)
        assert list(space.enumerate()) == ["t4"]
        assert len(payload["tiles"]) == 4

    def test_a_board_whose_memberships_are_missing_has_no_space(self):
        payload, solution = numbers_board()
        del solution["memberships"]
        assert odd_number_out.space(payload, solution) is None

    def test_a_digit_lock_space_is_the_surviving_codes(self):
        payload, solution = digit_board(clues=narrowing_clues())
        space = digit_lock.space(payload, solution)
        assert list(space.enumerate()) == [(1, 2, 3)]

    def test_each_game_defines_its_own_predicate(self):
        """Three predicates, no overlap, so no game reads another's facts."""
        predicates = [
            digit_lock.DIGIT_AT,
            shape_trio.IN_TRIO,
            odd_number_out.IS_ODD_ONE_OUT,
        ]
        assert len(set(predicates)) == 3
        assert all(predicate.count("/") == 1 for predicate in predicates)

    def test_a_digit_lock_board_tells_a_trio_board_nothing(self):
        payload, solution = digit_board(clues=narrowing_clues())
        trio_payload, _ = trio_board(FOUR_TILES, ["a", "b", "c"])
        measured = measure_leak(
            digit_lock.space(payload, solution),
            shape_trio.space(trio_payload),
            source_game_id="digit_lock",
            target_game_id="shape_trio",
        )
        assert measured.leak == 0.0
        assert measured.facts_applied == 0

    def test_a_malformed_board_has_no_space_rather_than_an_empty_one(self):
        for builder in default_spaces().values():
            assert builder({"tiles": "nonsense"}, {}) is None

    def test_spaces_for_day_maps_an_unknown_game_to_none(self):
        payload, solution = trio_board(FOUR_TILES, ["a", "b", "c"])
        built = spaces_for_day(
            {"shape_trio": (payload, solution), "mystery": ({}, {})}
        )
        assert built["shape_trio"] is not None
        assert built["mystery"] is None


class TestTheReportArtifact:
    """The tool's document carries what it did not measure, not only what it
    did."""

    def test_the_document_names_the_games_it_could_not_activate(self):
        document = as_document_of(
            [check_day("2026-10-01", {})], ("left_out",)
        )
        assert document["record"] == "coupling_report"
        assert document["not_measured"] == ["left_out"]
        assert document["days"][0]["day"] == "2026-10-01"

    def test_a_day_with_one_board_measures_no_pairs(self):
        """A coupling figure needs two boards, and reports none rather than
        zero when there is only one."""
        report = check_day("2026-10-01", {"alpha": tile_space(["a"])})
        assert report.measurements == ()
        assert report.passes

    def test_a_day_records_what_it_served_even_with_nothing_to_measure(self):
        """The board list cannot be recovered from the measurements.

        One board produces no pairs, so a report that derived its board list
        from its measurements described a day that served nothing -- which is
        exactly the day a reader would want to look into.
        """
        report = check_day("2026-10-01", {"alpha": tile_space(["a"])})
        assert report.boards == ("alpha",)

        document = as_document_of([report])
        assert document["days"][0]["boards"] == ["alpha"]
        assert document["days"][0]["measurements"] == []


class TestTheWindow:
    def test_a_window_admits_its_endpoints(self):
        window = Window(minimum=0.2, maximum=0.5)
        assert window.admits(0.2) and window.admits(0.5)
        assert not window.admits(0.19) and not window.admits(0.51)

    def test_an_unknown_never_lands_in_a_window(self):
        """Including the zero window, which is the independent default.

        An unmeasurable pair fails its declaration rather than passing as
        unexamined. Treating an unknown as a zero is how a leak ships.
        """
        assert not Window().admits(None)
        assert not Window(minimum=0.0, maximum=1.0 - 1e-9).admits(None)

    def test_a_window_outside_the_unit_interval_is_refused(self):
        with pytest.raises(ValueError):
            Window(minimum=-0.1, maximum=0.5)
        with pytest.raises(ValueError):
            Window(minimum=0.0, maximum=1.5)

    def test_an_inverted_window_is_refused(self):
        with pytest.raises(ValueError):
            Window(minimum=0.6, maximum=0.4)

    def test_a_window_survives_a_round_trip(self):
        window = Window(minimum=0.1, maximum=0.4)
        assert Window.from_document(window.as_document()) == window


class TestPairIntent:
    def test_the_default_pair_is_independent_and_uncoupled(self):
        pair = PairIntent(source_game_id="a", target_game_id="b")
        assert pair.leak.is_zero and pair.advantage.is_zero
        assert not pair.is_coupled

    def test_a_pair_declaring_an_advantage_is_coupled(self):
        pair = PairIntent(
            source_game_id="a",
            target_game_id="b",
            advantage=Window(minimum=0.2, maximum=0.6),
        )
        assert pair.is_coupled

    def test_a_board_is_not_coupled_to_itself(self):
        with pytest.raises(ValueError):
            PairIntent(source_game_id="a", target_game_id="a")

    def test_a_declaration_may_not_permit_a_leak_of_one(self):
        """That is one board printing another's answer."""
        with pytest.raises(ValueError, match="leak of 1.0"):
            PairIntent(
                source_game_id="a",
                target_game_id="b",
                leak=Window(minimum=0.0, maximum=1.0),
            )

    def test_a_declaration_may_not_permit_an_advantage_of_one(self):
        """A shortcut that finishes the board has done the puzzle."""
        with pytest.raises(ValueError, match="advantage of 1.0"):
            PairIntent(
                source_game_id="a",
                target_game_id="b",
                advantage=Window(minimum=0.5, maximum=1.0),
            )

    def test_a_pair_cannot_require_an_advantage_and_permit_a_leak(self):
        """The mechanic is a shortcut, so the answer may not also escape."""
        with pytest.raises(ValueError, match="load bearing"):
            PairIntent(
                source_game_id="a",
                target_game_id="b",
                leak=Window(minimum=0.0, maximum=0.3),
                advantage=Window(minimum=0.2, maximum=0.6),
            )

    def test_the_leak_half_is_the_target_check_day_already_reads(self):
        pair = PairIntent(
            source_game_id="a",
            target_game_id="b",
            leak=Window(minimum=0.0, maximum=0.2),
        )
        target = pair.as_target()
        assert isinstance(target, CouplingTarget)
        assert (target.minimum, target.maximum) == (0.0, 0.2)


class TestTheDailySetDeclaration:
    def test_the_independent_default_declares_every_ordered_pair(self):
        declaration = DailySetDeclaration.independent("set", ("a", "b", "c"))
        assert len(declaration.pairs) == 6
        assert declaration.coupled_pairs == ()

    def test_a_pair_left_undeclared_is_refused(self):
        """A pair nobody declared is a pair nobody checked.

        This is the failure the gate exists to prevent: a game added to the
        set and a pair that quietly fell off the list.
        """
        declaration = DailySetDeclaration.independent("set", ("a", "b"))
        with pytest.raises(ValueError, match="not"):
            DailySetDeclaration(
                name="set",
                game_ids=("a", "b", "c"),
                pairs=declaration.pairs,
            )

    def test_a_pair_naming_a_game_the_set_does_not_serve_is_refused(self):
        with pytest.raises(ValueError, match="does not serve"):
            DailySetDeclaration(
                name="set",
                game_ids=("a", "b"),
                pairs=(
                    PairIntent(source_game_id="a", target_game_id="b"),
                    PairIntent(source_game_id="b", target_game_id="a"),
                    PairIntent(source_game_id="a", target_game_id="z"),
                ),
            )

    def test_a_pair_declared_twice_is_refused(self):
        with pytest.raises(ValueError, match="twice"):
            DailySetDeclaration(
                name="set",
                game_ids=("a", "b"),
                pairs=(
                    PairIntent(source_game_id="a", target_game_id="b"),
                    PairIntent(source_game_id="a", target_game_id="b"),
                    PairIntent(source_game_id="b", target_game_id="a"),
                ),
            )

    def test_a_set_of_one_board_has_nothing_to_declare(self):
        with pytest.raises(ValueError, match="fewer than two"):
            DailySetDeclaration(name="set", game_ids=("a",), pairs=())

    def test_an_unnamed_set_is_refused(self):
        with pytest.raises(ValueError, match="named"):
            DailySetDeclaration.independent("", ("a", "b"))

    def test_a_board_the_declaration_says_nothing_about_is_reported(self):
        declaration = DailySetDeclaration.independent("set", ("a", "b"))
        assert declaration.covers(("a", "b")) == ()
        assert declaration.covers(("a", "b", "z")) == ("z",)

    def test_the_declaration_survives_a_round_trip(self):
        declaration = DailySetDeclaration(
            name="set",
            game_ids=("a", "b"),
            pairs=(
                PairIntent(
                    source_game_id="a",
                    target_game_id="b",
                    advantage=Window(minimum=0.2, maximum=0.6),
                    note="a licenses a shortcut through b",
                ),
                PairIntent(source_game_id="b", target_game_id="a"),
            ),
        )
        assert (
            DailySetDeclaration.from_document(declaration.as_document())
            == declaration
        )

    def test_reading_a_missing_declaration_is_not_an_error(self, tmp_path):
        """Nobody having declared a set is a state, not a failure."""
        assert read_declaration(tmp_path / "nothing.json") is None

    def test_a_written_declaration_reads_back(self, tmp_path):
        declaration = DailySetDeclaration.independent("set", ("a", "b"))
        path = tmp_path / "sets" / "set.json"
        write_declaration(path, declaration)
        assert read_declaration(path) == declaration

    def test_the_committed_symbolic_set_declares_the_three_games(self):
        """The artifact CI gates against, read rather than described."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[2]
        declaration = read_declaration(root / "content/daily-sets/symbolic.json")
        assert declaration is not None
        assert declaration.game_ids == (
            "digit_lock",
            "odd_number_out",
            "shape_trio",
        )
        assert len(declaration.pairs) == 6
        assert declaration.coupled_pairs == ()
        assert all(pair.note for pair in declaration.pairs)

    def test_the_committed_set_gates_the_games_it_declares(self):
        """Its leak windows are the targets a day is measured against."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[2]
        declaration = read_declaration(root / "content/daily-sets/symbolic.json")
        targets = declaration.targets()
        assert len(targets) == 6
        assert all(target.maximum == 0.0 for target in targets)
