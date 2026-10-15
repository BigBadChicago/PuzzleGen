"""The licensed shortcut: what one board's answer saves on another.

The arithmetic here is over a small toy pair rather than over two real games,
and deliberately so. This release's games namespace their predicates per
game, so no real pair shares one and every real advantage is zero; a suite
built on that would test the absence of the mechanic rather than the
mechanic. The toy pair shares a predicate, which is exactly the state a day
reaches once a board publishes the entity behind a tile.
"""

from __future__ import annotations

import pytest

from puzzlegen.engine.certify import AssuranceLevel
from puzzlegen.engine.coupling import (
    DailySetDeclaration,
    EnumeratedSpace,
    Fact,
    PairIntent,
    Window,
)
from puzzlegen.engine.crossref import (
    CrossReference,
    check_day,
    offer,
    witnesses_for,
)
from puzzlegen.engine.witness import build_witness, deduction_for

#: One predicate, shared. Both toy boards are about the same four marks, so
#: each reads the other's claims -- which no pair of this release's games does.
MARK = "toy/mark"
#: A predicate only the source speaks, for the pair that shares nothing.
PRIVATE = "toy/private"

MARKS = ("w", "x", "y", "z")

#: Every pair of distinct marks, in order. Twelve answers.
ANSWERS = tuple((a, b) for a in MARKS for b in MARKS if a != b)


def admits(answer, fact):
    if fact.predicate != MARK:
        return True
    if fact.value is False:
        return fact.ref not in answer
    return answer[int(fact.value)] == fact.ref


def space(answers=ANSWERS, predicates=(MARK,)):
    return EnumeratedSpace(
        answers=tuple(answers),
        admits=admits,
        predicates=frozenset(predicates),
    )


def mark(ref, value=0):
    return Fact(predicate=MARK, ref=ref, value=value)


def source_space(*facts, predicates=(MARK,)):
    """A source board whose answer reveals ``facts``."""
    return EnumeratedSpace(
        answers=(("w", "x"),),
        admits=admits,
        revealed=tuple(facts),
        predicates=frozenset(predicates),
    )


def target_witness(*statements):
    """A witness over the toy space, from (text, facts) pairs."""
    return build_witness(
        game_id="target",
        assurance=AssuranceLevel.UNIQUENESS_PROVED,
        space=space(),
        candidates=[
            deduction_for(text, facts, space()) for text, facts in statements
        ],
    )


def solved_target(answer=("w", "x")):
    """A target board whose own clues entail its own answer."""
    return space([answer])


class TestWhatTheMeasurementIs:
    def test_an_offer_that_settles_half_the_work_has_an_advantage_of_a_half(self):
        witness = target_witness(
            ("w is first", [mark("w", 0)]),
            ("x is second", [mark("x", 1)]),
        )
        reference = offer(
            source_space(mark("w", 0)),
            witness,
            solved_target(),
            source_game_id="source",
            target_game_id="target",
        )
        assert reference.steps_before == 2
        assert reference.steps_after == 1
        assert reference.advantage == pytest.approx(0.5)
        assert reference.is_shortcut

    def test_an_offer_the_target_cannot_read_saves_nothing(self):
        """The position every real pair is in today.

        The source's answer reveals a fact in a predicate the target's
        witness does not speak, so nothing crosses and the advantage is a
        measured zero rather than an unknown.
        """
        witness = target_witness(("w is first", [mark("w", 0)]))
        reference = offer(
            source_space(
                Fact(predicate=PRIVATE, ref="w", value=0),
                predicates=(PRIVATE,),
            ),
            witness,
            solved_target(),
            source_game_id="source",
            target_game_id="target",
        )
        assert reference.offered == ()
        assert reference.advantage == 0.0
        assert not reference.is_shortcut
        assert "reads none of the facts" in reference.detail

    def test_a_fact_that_contradicts_a_step_settles_nothing(self):
        """Sharing a predicate is not sharing a claim."""
        witness = target_witness(("w is first", [mark("w", 0)]))
        reference = offer(
            source_space(mark("w", 1)),
            witness,
            solved_target(),
            source_game_id="source",
            target_game_id="target",
        )
        assert reference.offered == (mark("w", 1),)
        assert reference.advantage == 0.0

    def test_a_board_with_no_work_left_has_no_advantage_to_report(self):
        reference = offer(
            source_space(mark("w", 0)),
            target_witness(),
            solved_target(),
            source_game_id="source",
            target_game_id="target",
        )
        assert reference.steps_before == 0
        assert reference.advantage is None
        assert "no work" in reference.detail

    def test_an_unmeasurable_board_is_unknown_and_not_zero(self):
        for source, witness, target in (
            (None, target_witness(), solved_target()),
            (source_space(mark("w", 0)), None, solved_target()),
            (source_space(mark("w", 0)), target_witness(), None),
        ):
            reference = offer(
                source,
                witness,
                target,
                source_game_id="source",
                target_game_id="target",
            )
            assert reference.advantage is None
            assert not reference.known

    def test_the_advantage_is_measured_against_the_work_actually_left(self):
        """A step the player already holds is not work a shortcut saves."""
        witness = target_witness(
            ("w is first", [mark("w", 0)]),
            ("x is second", [mark("x", 1)]),
        )
        reference = offer(
            source_space(mark("w", 0)),
            witness,
            solved_target(),
            source_game_id="source",
            target_game_id="target",
            known=[mark("w", 0)],
        )
        assert reference.steps_before == 1
        assert reference.advantage == 0.0


class TestTheInvariants:
    def test_a_target_that_cannot_stand_alone_is_refused(self):
        """The risk the review names, and the only one that matters.

        Most people play one game. A board needing another board's answer to
        be solvable punishes them, so a cross reference to such a board is
        inadmissible however small its advantage.
        """
        witness = target_witness(("w is first", [mark("w", 0)]))
        reference = offer(
            source_space(mark("w", 0)),
            witness,
            space([("w", "x"), ("w", "y")]),
            source_game_id="source",
            target_game_id="target",
        )
        assert not reference.solvable_alone
        assert not reference.admissible
        assert any("load bearing" in finding for finding in reference.findings())

    def test_a_target_whose_own_clues_entail_its_answer_stands_alone(self):
        """Two deductions left and the offer settles one, which is admissible.

        A single-step witness would be settled in full by this offer and
        refused as a reveal, so the board that demonstrates standing alone
        has to have work left over.
        """
        reference = offer(
            source_space(mark("w", 0)),
            target_witness(
                ("w is first", [mark("w", 0)]),
                ("x is second", [mark("x", 1)]),
            ),
            solved_target(),
            source_game_id="source",
            target_game_id="target",
        )
        assert reference.solvable_alone
        assert reference.admissible

    def test_an_offer_that_settles_every_deduction_is_refused(self):
        """It has not saved the player work, it has done the puzzle.

        Measured over the witness, not over the target's answers: a board
        that stands alone already has one answer, so a rule written over
        answers would fire on every admissible board.
        """
        witness = target_witness(("w is first", [mark("w", 0)]))
        reference = offer(
            source_space(mark("w", 0)),
            witness,
            solved_target(),
            source_game_id="source",
            target_game_id="target",
        )
        assert reference.advantage == 1.0
        assert reference.finishes_the_board
        assert not reference.admissible

    def test_an_offer_the_target_s_clues_rule_out_is_refused(self):
        """Two boards disagreeing about what they share is a content fault.

        The player it would reach holds two true-looking claims and no
        solvable board, so it fails loudly rather than reading as a leak.
        """
        witness = target_witness(
            ("w is first", [mark("w", 0)]),
            ("x is second", [mark("x", 1)]),
        )
        reference = offer(
            source_space(mark("y", 0)),
            witness,
            solved_target(),
            source_game_id="source",
            target_game_id="target",
        )
        assert reference.answers_after == 0
        assert reference.contradicts_the_target
        assert not reference.admissible

    def test_an_unmeasured_target_is_refused_rather_than_passed(self):
        reference = offer(
            source_space(mark("w", 0)),
            None,
            solved_target(),
            source_game_id="source",
            target_game_id="target",
        )
        assert not reference.admissible

    def test_a_reference_describes_itself_either_way(self):
        known = offer(
            source_space(mark("w", 0)),
            target_witness(("w is first", [mark("w", 0)])),
            solved_target(),
            source_game_id="source",
            target_game_id="target",
        )
        assert "source -> target" in known.describe()
        unknown = offer(
            None,
            None,
            None,
            source_game_id="source",
            target_game_id="target",
        )
        assert "unknown" in unknown.describe()


class TestCheckingADay:
    def spaces(self):
        return {
            "source": source_space(mark("w", 0)),
            "target": solved_target(),
        }

    def witnesses(self):
        return {
            "source": None,
            "target": target_witness(
                ("w is first", [mark("w", 0)]),
                ("x is second", [mark("x", 1)]),
            ),
        }

    def coupled(self, minimum=0.4, maximum=0.6):
        return DailySetDeclaration(
            name="toy",
            game_ids=("source", "target"),
            pairs=(
                PairIntent(
                    source_game_id="source",
                    target_game_id="target",
                    advantage=Window(minimum=minimum, maximum=maximum),
                    note="source licenses a shortcut through target",
                ),
                PairIntent(source_game_id="target", target_game_id="source"),
            ),
        )

    def test_a_measured_shortcut_inside_its_window_passes(self):
        report = check_day(
            "2026-10-01", self.spaces(), self.witnesses(), self.coupled()
        )
        assert report.passes
        assert len(report.shortcuts) == 1

    def test_a_shortcut_outside_its_window_is_a_violation(self):
        report = check_day(
            "2026-10-01",
            self.spaces(),
            self.witnesses(),
            self.coupled(minimum=0.8, maximum=0.9),
        )
        assert not report.passes
        assert any("declared advantage" in why for _, why in report.violations)

    def test_an_uncoupled_pair_that_turns_out_to_leak_work_is_a_violation(self):
        """The gate in the direction the reviews asked for.

        The pair is declared independent and the source's answer settles one
        of the target's deductions anyway, which is an unintended shortcut.
        """
        declaration = DailySetDeclaration.independent("toy", ("source", "target"))
        report = check_day(
            "2026-10-01", self.spaces(), self.witnesses(), declaration
        )
        assert not report.passes
        assert any("declared uncoupled" in why for _, why in report.violations)

    def test_an_uncoupled_pair_may_have_an_unmeasurable_target(self):
        """It is offering that board nothing, so it owes no witness.

        This is why the committed symbolic set passes with no witness builder
        wired in: every pair of it is declared uncoupled.
        """
        declaration = DailySetDeclaration.independent("toy", ("source", "target"))
        report = check_day(
            "2026-10-01",
            self.spaces(),
            {"source": None, "target": None},
            declaration,
        )
        assert report.passes

    def test_a_coupled_pair_with_an_unmeasurable_target_fails(self):
        report = check_day(
            "2026-10-01",
            self.spaces(),
            {"source": None, "target": None},
            self.coupled(),
        )
        assert not report.passes
        assert any("unmeasured" in why for _, why in report.violations)

    def test_a_declaration_naming_a_pair_not_on_the_day_is_reported(self):
        """A stale declaration, which beats ignoring it."""
        report = check_day(
            "2026-10-01",
            {"source": source_space(mark("w", 0))},
            self.witnesses(),
            self.coupled(),
        )
        assert not report.passes
        assert any("neither board" in why for _, why in report.violations)

    def test_a_board_the_declaration_does_not_cover_fails_the_day(self):
        """An undeclared board is an unchecked board."""
        spaces = {**self.spaces(), "stranger": solved_target()}
        report = check_day(
            "2026-10-01", spaces, self.witnesses(), self.coupled()
        )
        assert report.undeclared == ("stranger",)
        assert not report.passes
        assert any(
            line.startswith("UNDECLARED") for line in report.describe()
        )

    def test_the_report_describes_every_reference(self):
        report = check_day(
            "2026-10-01", self.spaces(), self.witnesses(), self.coupled()
        )
        assert len(report.describe()) >= len(report.references)
        assert report.set_name == "toy"


class TestBuildingWitnessesForADay:
    def test_a_game_with_no_builder_maps_to_unmeasured(self):
        """Absence is a refusal to couple, not a false zero."""
        built = witnesses_for({"source": ({}, {}), "target": ({}, {})})
        assert built == {"source": None, "target": None}

    def test_a_builder_is_called_with_the_board(self):
        seen = {}

        def builder(payload, solution):
            seen["payload"] = payload
            return target_witness(("w is first", [mark("w", 0)]))

        built = witnesses_for(
            {"target": ({"board": 1}, {"answer": 2})}, {"target": builder}
        )
        assert seen["payload"] == {"board": 1}
        assert built["target"] is not None


class TestTheTwoMeasurementsAreDifferent:
    def test_a_uniquely_solvable_board_has_no_leak_and_can_have_an_advantage(self):
        """The resolution the whole module rests on.

        The target's clues entail its answer, so there is nothing of its
        answer left for another board to give away: the leak is zero by
        construction. The work the target still demands is a different
        quantity, and it is one the source's answer measurably shortens.
        """
        from puzzlegen.engine.coupling import measure_leak

        leak = measure_leak(
            source_space(mark("w", 0)),
            solved_target(),
            source_game_id="source",
            target_game_id="target",
        )
        advantage = offer(
            source_space(mark("w", 0)),
            target_witness(
                ("w is first", [mark("w", 0)]),
                ("x is second", [mark("x", 1)]),
            ),
            solved_target(),
            source_game_id="source",
            target_game_id="target",
        )
        assert leak.leak == 0.0
        assert leak.independent
        assert advantage.advantage == pytest.approx(0.5)
        assert advantage.admissible
