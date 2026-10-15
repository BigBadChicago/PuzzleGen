"""Deduction witnesses, and the hint ladder derived from one.

The numbers below are hand computed over a small space, which is the point of
testing a measurement rather than a boolean: a cost that is subtly wrong in
the middle of its range would pass a boolean's tests. The space is six
three-letter answers so the arithmetic stays checkable by eye.
"""

from __future__ import annotations

import pytest

from puzzlegen.engine.certify import AssuranceLevel
from puzzlegen.engine.coupling import EnumeratedSpace, Fact
from puzzlegen.engine.witness import (
    Deduction,
    DeductionWitness,
    WitnessError,
    build_witness,
    deduction_for,
    entails,
    next_deduction,
    remaining,
)

HAS = "toy/has"
AT = "toy/at"

#: Every arrangement of three distinct letters drawn from four. Twenty-four
#: answers, small enough that every count below can be checked by counting.
ANSWERS = tuple(
    (a, b, c)
    for a in "abcd"
    for b in "abcd"
    for c in "abcd"
    if len({a, b, c}) == 3
)

ANSWER = ("a", "b", "c")


def admits(answer, fact):
    if fact.predicate == HAS:
        return (fact.ref in answer) is bool(fact.value)
    if fact.predicate == AT:
        return answer[int(fact.value)] == fact.ref
    return True


def space(answers=ANSWERS):
    return EnumeratedSpace(
        answers=tuple(answers),
        admits=admits,
        predicates=frozenset({HAS, AT}),
    )


def has(ref, value=True):
    return Fact(predicate=HAS, ref=ref, value=value)


def at(ref, position):
    return Fact(predicate=AT, ref=ref, value=position)


class TestTheArithmetic:
    def test_the_space_is_the_one_the_counts_assume(self):
        """Twenty-four arrangements, and 'd is absent' keeps a quarter of them.

        Stated as a test because every figure below is read against it: four
        letters taken three at a time is twenty-four, and the six that omit
        any one letter are the six arrangements of the other three.
        """
        assert len(ANSWERS) == 24
        assert len([a for a in ANSWERS if "d" not in a]) == 6

    def test_an_exclusion_costs_less_than_an_inclusion(self):
        """Over four letters the cheaper claim is the negative one.

        Eighteen of twenty-four arrangements use 'd', six do not, so saying
        'd is in it' removes a quarter of the space and saying it is not
        removes three quarters. The derived ladder therefore opens with an
        inclusion here and with an exclusion on a ten-digit board -- which is
        the whole reason the ordering is measured rather than written down.
        """
        inclusion = deduction_for("d is in it", [has("d")], space())
        exclusion = deduction_for("d is not in it", [has("d", False)], space())
        assert inclusion.space_after == 18
        assert exclusion.space_after == 6
        assert inclusion.information == pytest.approx(0.25)
        assert exclusion.information == pytest.approx(0.75)

    def test_a_position_costs_more_than_a_membership(self):
        membership = deduction_for("a is in it", [has("a")], space())
        position = deduction_for("a is first", [at("a", 0)], space())
        assert membership.space_after == 18
        assert position.space_after == 6
        assert position.information > membership.information

    def test_several_facts_narrow_together(self):
        step = deduction_for(
            "it uses a, b and c",
            [has("a"), has("b"), has("c")],
            space(),
        )
        assert step.space_after == 6

    def test_a_statement_that_pins_the_answer_is_a_reveal(self):
        step = deduction_for(
            "it is abc",
            [at("a", 0), at("b", 1), at("c", 2)],
            space(),
        )
        assert step.space_after == 1
        assert step.is_reveal

    def test_a_statement_that_removes_nothing_is_vacuous(self):
        step = deduction_for("it has three letters", [has("a")], space([ANSWER]))
        assert step.is_vacuous
        assert not step.is_reveal

    def test_a_deduction_over_a_single_answer_space_is_not_a_reveal(self):
        """A board already down to one answer reveals nothing by saying so.

        ``is_reveal`` asks whether the statement did the narrowing, so the
        space having one member to begin with is not it. Without the
        ``space_before > 1`` guard every statement on a solved board would
        read as a spoiler and the ladder would refuse to say anything.
        """
        step = deduction_for("a is first", [at("a", 0)], space([ANSWER]))
        assert step.space_after == 1
        assert not step.is_reveal


class TestWhatADeductionRefuses:
    def test_a_statement_with_no_facts_is_refused(self):
        with pytest.raises(WitnessError):
            Deduction(statement="trust me", establishes=(), space_before=24, space_after=6)

    def test_a_statement_that_leaves_no_answer_is_refused(self):
        """False about the board, not a hint about it."""
        with pytest.raises(WitnessError):
            Deduction(
                statement="it is efg",
                establishes=(has("e"),),
                space_before=24,
                space_after=0,
            )

    def test_a_statement_that_widens_the_space_is_refused(self):
        with pytest.raises(WitnessError):
            Deduction(
                statement="anything goes",
                establishes=(has("a"),),
                space_before=6,
                space_after=24,
            )

    def test_an_unstated_statement_is_refused(self):
        with pytest.raises(WitnessError):
            Deduction(
                statement="", establishes=(has("a"),), space_before=24, space_after=6
            )


class TestBuildingAWitness:
    def candidates(self):
        return [
            deduction_for("d is not in it", [has("d", False)], space()),
            deduction_for("a is in it", [has("a")], space()),
            deduction_for("a is first", [at("a", 0)], space()),
        ]

    def test_the_steps_come_out_cheapest_first(self):
        witness = build_witness(
            game_id="toy",
            assurance=AssuranceLevel.UNIQUENESS_PROVED,
            space=space(),
            candidates=self.candidates(),
        )
        assert [step.statement for step in witness.steps] == [
            "a is in it",
            "a is first",
            "d is not in it",
        ]

    def test_a_vacuous_candidate_is_dropped_rather_than_ordered(self):
        candidates = [
            *self.candidates(),
            deduction_for("it has letters", [has("a")], space([ANSWER])),
        ]
        # The vacuous one is counted over a one-answer space, which is also a
        # different space, so it is dropped before the space check runs.
        witness = build_witness(
            game_id="toy",
            assurance=AssuranceLevel.ANSWER_VALID,
            space=space(),
            candidates=candidates,
        )
        assert len(witness.steps) == 3

    def test_candidates_counted_over_another_space_are_refused(self):
        """Costs over different spaces are not comparable, so not orderable."""
        other = deduction_for("a is first", [at("a", 0)], space(ANSWERS[:12]))
        with pytest.raises(WitnessError, match="different space"):
            build_witness(
                game_id="toy",
                assurance=AssuranceLevel.ANSWER_VALID,
                space=space(),
                candidates=[other],
            )

    def test_ties_break_on_the_statement_so_a_witness_is_reproducible(self):
        """A hint is re-derived on replay, so two builds must agree.

        Three statements of identical cost, offered in two different orders,
        have to come out in one order: a tie broken by iteration order would
        make a regrade disagree with the session it is regrading.
        """
        same_cost = [
            deduction_for(f"{letter} is in it", [has(letter)], space())
            for letter in "abc"
        ]
        forward = build_witness(
            game_id="toy",
            assurance=AssuranceLevel.ANSWER_VALID,
            space=space(),
            candidates=same_cost,
        )
        backward = build_witness(
            game_id="toy",
            assurance=AssuranceLevel.ANSWER_VALID,
            space=space(),
            candidates=list(reversed(same_cost)),
        )
        assert [s.statement for s in forward.steps] == [
            s.statement for s in backward.steps
        ]

    def test_an_unordered_witness_is_refused(self):
        steps = (
            deduction_for("a is first", [at("a", 0)], space()),
            deduction_for("a is in it", [has("a")], space()),
        )
        with pytest.raises(WitnessError, match="cheapest first"):
            DeductionWitness(
                game_id="toy",
                assurance=AssuranceLevel.ANSWER_VALID,
                steps=steps,
                space=24,
            )

    def test_a_witness_must_name_its_game(self):
        with pytest.raises(WitnessError):
            DeductionWitness(
                game_id="",
                assurance=AssuranceLevel.ANSWER_VALID,
                steps=(),
                space=24,
            )


class TestAWitnessHoldingUp:
    def witness(self):
        return build_witness(
            game_id="toy",
            assurance=AssuranceLevel.UNIQUENESS_PROVED,
            space=space(),
            candidates=[
                deduction_for("d is not in it", [has("d", False)], space()),
                deduction_for("a is in it", [has("a")], space()),
            ],
        )

    def test_a_witness_rederived_against_its_own_space_holds_up(self):
        assert self.witness().holds_up(space()) == ()

    def test_a_doctored_cost_is_caught(self):
        witness = self.witness()
        wrong = witness.steps[0].__class__(
            statement=witness.steps[0].statement,
            establishes=witness.steps[0].establishes,
            space_before=24,
            space_after=17,
        )
        doctored = DeductionWitness(
            game_id="toy",
            assurance=witness.assurance,
            steps=(wrong, *witness.steps[1:]),
            space=24,
        )
        findings = doctored.holds_up(space())
        assert findings and "17" in findings[0]

    def test_a_witness_against_the_wrong_space_says_so_once(self):
        findings = self.witness().holds_up(space(ANSWERS[:12]))
        assert len(findings) == 1
        assert "24" in findings[0] and "12" in findings[0]

    def test_a_step_the_space_cannot_read_is_unchecked_rather_than_passed(self):
        """A cost no space can verify is a finding, not a silent pass."""
        foreign = EnumeratedSpace(
            answers=ANSWERS, admits=admits, predicates=frozenset({AT})
        )
        findings = self.witness().holds_up(foreign)
        assert findings
        assert all("unchecked" in finding for finding in findings)

    def test_the_facts_of_a_witness_are_every_step_s_facts(self):
        assert self.witness().facts() == frozenset({has("d", False), has("a")})


class TestEntailment:
    def test_a_statement_every_survivor_satisfies_is_entailed(self):
        survivors = space([ANSWER])
        assert entails(survivors, [has("a"), at("a", 0)])

    def test_a_statement_some_survivor_contradicts_is_not(self):
        survivors = space([ANSWER, ("a", "c", "b")])
        assert entails(survivors, [has("a")])
        assert not entails(survivors, [at("b", 1)])

    def test_entailment_is_what_separates_a_deduction_from_a_leak(self):
        """On a board with two survivors, only the shared claims are hints.

        This is the check that would catch a board whose uniqueness contract
        was not met: a statement true of the intended answer and false of the
        other survivor is one no player could have derived.
        """
        survivors = space([ANSWER, ("a", "c", "b")])
        offered = [
            deduction_for("a is in it", [has("a")], space()),
            deduction_for("b is second", [at("b", 1)], space()),
        ]
        deducible = [s for s in offered if entails(survivors, s.establishes)]
        assert [s.statement for s in deducible] == ["a is in it"]


class TestTheLadder:
    def ladder(self):
        return build_witness(
            game_id="toy",
            assurance=AssuranceLevel.UNIQUENESS_PROVED,
            space=space(),
            candidates=[
                deduction_for("a is in it", [has("a")], space()),
                deduction_for("b is in it", [has("b")], space()),
                deduction_for("a is first", [at("a", 0)], space()),
                deduction_for(
                    "it is abc", [at("a", 0), at("b", 1), at("c", 2)], space()
                ),
            ],
        )

    def test_the_first_offer_is_the_cheapest_statement(self):
        step = next_deduction(self.ladder())
        assert step is not None
        assert step.statement == "a is in it"

    def test_a_statement_the_player_holds_is_skipped(self):
        step = next_deduction(self.ladder(), [has("a")])
        assert step is not None
        assert step.statement == "b is in it"

    def test_holding_one_fact_of_several_does_not_count_as_holding_the_step(self):
        """A statement is held only when every fact in it is held."""
        witness = build_witness(
            game_id="toy",
            assurance=AssuranceLevel.ANSWER_VALID,
            space=space(),
            candidates=[
                deduction_for("it uses a and b", [has("a"), has("b")], space())
            ],
        )
        step = next_deduction(witness, [has("a")])
        assert step is not None

    def test_a_second_request_moves_up_the_ladder(self):
        assert next_deduction(self.ladder(), given=1).statement == "b is in it"
        assert next_deduction(self.ladder(), given=2).statement == "a is first"

    def test_the_ladder_stops_before_the_reveal(self):
        """Exhaustion is a refusal to spoil, not an absence of true claims.

        The fourth rung states the whole answer, and it is the only rung left
        once three are spent, so the service says there is nothing to offer
        rather than handing it over.
        """
        assert next_deduction(self.ladder(), given=3) is None

    def test_a_caller_that_asks_for_the_reveal_gets_it(self):
        step = next_deduction(self.ladder(), given=3, allow_reveal=True)
        assert step is not None and step.statement == "it is abc"

    def test_a_witness_below_the_caller_s_floor_is_refused(self):
        weak = build_witness(
            game_id="toy",
            assurance=AssuranceLevel.ANSWER_VALID,
            space=space(),
            candidates=[deduction_for("a is in it", [has("a")], space())],
        )
        with pytest.raises(WitnessError, match="below"):
            next_deduction(weak, floor=AssuranceLevel.UNIQUENESS_PROVED)

    def test_a_witness_at_the_floor_is_accepted(self):
        step = next_deduction(
            self.ladder(), floor=AssuranceLevel.SOLUTION_COUNT_KNOWN
        )
        assert step is not None

    def test_a_negative_hint_count_is_refused(self):
        with pytest.raises(WitnessError):
            next_deduction(self.ladder(), given=-1)

    def test_an_empty_witness_offers_nothing(self):
        empty = DeductionWitness(
            game_id="toy",
            assurance=AssuranceLevel.ANSWER_VALID,
            steps=(),
            space=24,
        )
        assert empty.exhausted
        assert next_deduction(empty) is None

    def test_remaining_is_what_a_cross_reference_is_measured_against(self):
        assert len(remaining(self.ladder())) == 4
        assert len(remaining(self.ladder(), [has("a")])) == 3
