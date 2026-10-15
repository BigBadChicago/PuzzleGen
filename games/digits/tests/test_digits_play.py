"""Playing the board: grading, hints, scoring, rendering, sharing.

``grade_move`` must be a pure function of the puzzle and the moves before it,
because the session layer replays the whole ledger on every submission and
raises if a regrade disagrees.

The decision worth testing hardest in this game is what a wrong submission is
told. It is graded with the same two counts every clue on the board carries,
which is not a leak in any new way: a solver who submits a guess has bought
the information a clue would have given them, at the cost of a try.
"""

from __future__ import annotations

import pytest

from puzzlegen.engine.plugin import SessionTelemetry
from puzzlegen_digits.descriptor import SHARE_TOKENS
from puzzlegen_digits.lock import CodeError, verdict
from puzzlegen_digits.witness import upcoming, witness_for
from puzzlegen_digits.play import (
    DIFFICULTY_BONUS,
    MAX_HINTS,
    MAX_MISTAKES,
    PENALTY_PER_HINT,
    PENALTY_PER_MISTAKE,
    POINTS_FIRST_TRY,
    POINTS_FOR_CODE,
    create_share_artifact,
    empty_state,
    get_hint,
    grade_move,
    render,
    score,
)

from boards import CODE, make_puzzle


@pytest.fixture
def puzzle():
    return make_puzzle(properties=("primes",))


WRONG = [2, 5, 8]


def telemetry(**kwargs) -> SessionTelemetry:
    defaults = dict(
        completed=True, attempts=1, elapsed_ms=10_000, mistakes=0, hints_used=0
    )
    return SessionTelemetry(**{**defaults, **kwargs})


class TestGrading:
    def test_the_code_opens_the_lock(self, puzzle):
        judgement = grade_move(puzzle, {"code": list(CODE)}, empty_state(puzzle))
        assert judgement.correct and judgement.complete and judgement.solved

    def test_the_order_matters(self, puzzle):
        """Unlike a trio, a code is a sequence: the same digits in another
        order is a different code and a wrong answer."""
        judgement = grade_move(
            puzzle, {"code": list(reversed(CODE))}, empty_state(puzzle)
        )
        assert not judgement.solved

    def test_a_wrong_code_costs_a_mistake_and_does_not_finish(self, puzzle):
        judgement = grade_move(puzzle, {"code": WRONG}, empty_state(puzzle))
        assert not judgement.correct and not judgement.complete
        assert judgement.state["mistakes"] == 1

    def test_a_wrong_code_is_recorded_with_its_verdict(self, puzzle):
        """On this board an attempt is a clue, so a client that could not show
        it would be asking the player to remember their own verdicts."""
        judgement = grade_move(puzzle, {"code": WRONG}, empty_state(puzzle))
        attempt = judgement.state["attempts"][0]
        exact, misplaced = verdict(tuple(WRONG), tuple(CODE))
        assert attempt == {
            "guess": WRONG, "exact": exact, "misplaced": misplaced
        }

    def test_the_note_gives_the_verdict_in_the_boards_own_language(self, puzzle):
        judgement = grade_move(puzzle, {"code": [4, 1, 7]}, empty_state(puzzle))
        assert "right place" in judgement.note or "wrong place" in judgement.note

    def test_a_guess_sharing_nothing_is_told_so(self, puzzle):
        judgement = grade_move(puzzle, {"code": [0, 3, 5]}, empty_state(puzzle))
        assert "None of those digits" in judgement.note

    def test_the_board_closes_after_the_last_mistake(self, puzzle):
        state = empty_state(puzzle)
        for _ in range(MAX_MISTAKES):
            state = grade_move(puzzle, {"code": WRONG}, state).state
        assert state["complete"] and not state["solved"]

    def test_the_code_is_revealed_only_once_the_board_closes(self, puzzle):
        state = empty_state(puzzle)
        for turn in range(MAX_MISTAKES):
            judgement = grade_move(puzzle, {"code": WRONG}, state)
            state = judgement.state
            if turn < MAX_MISTAKES - 1:
                assert state["revealed"] == []
                assert "".join(str(d) for d in CODE) not in judgement.note
        assert state["revealed"] == [list(CODE)]
        assert "".join(str(d) for d in CODE) in judgement.note

    def test_a_move_after_the_end_solves_nothing(self, puzzle):
        closed = {**empty_state(puzzle), "complete": True}
        judgement = grade_move(puzzle, {"code": list(CODE)}, closed)
        assert judgement.complete and not judgement.solved

    def test_grading_is_a_pure_function_of_the_puzzle_and_the_state(self, puzzle):
        state = empty_state(puzzle)
        payload = {"code": WRONG}
        assert grade_move(puzzle, payload, state) == grade_move(puzzle, payload, state)

    @pytest.mark.parametrize(
        "payload",
        [{}, {"code": "147"}, {"code": [1, 4]}, {"code": [1, 1, 4]}, {"code": [1, 4, 99]}],
    )
    def test_a_malformed_submission_raises_rather_than_costing_a_mistake(
        self, puzzle, payload
    ):
        """Charging the player a mistake for a client's bug is the wrong
        failure. ``CodeError`` is a ``ValueError``, so the session layer sees
        one kind."""
        with pytest.raises(ValueError):
            grade_move(puzzle, payload, empty_state(puzzle))


class TestHints:
    """The hints are derived from the witness, so these test the derivation.

    What a hint *says* is no longer a property of this module -- it is the
    cheapest statement the board's clues force -- so the tests are about the
    ladder's shape rather than about three sentences.
    """

    def ladder(self, puzzle, count=MAX_HINTS, state=None):
        board = empty_state(puzzle) if state is None else state
        return [get_hint(puzzle, board, used) for used in range(count)]

    def test_the_hints_run_cheapest_first(self, puzzle):
        """Each rung gives at least as much away as the one before it."""
        witness = witness_for(puzzle)
        costs = [step.information for step in witness.steps]
        assert costs == sorted(costs)

    def test_the_first_hint_is_an_exclusion(self, puzzle):
        """An exclusion over ten digits removes less space than an inclusion.

        The authored ladder opened by naming a digit that *is* in the code.
        Measured, that is the dearer statement, and the derived ladder offers
        the cheaper one.
        """
        hint = get_hint(puzzle, empty_state(puzzle), 0)
        assert "is not in the code" in hint.text

    def test_each_hint_differs_from_the_one_before(self, puzzle):
        texts = [hint.text for hint in self.ladder(puzzle)]
        assert len(set(texts)) == len(texts) == MAX_HINTS

    def test_a_hint_cites_the_rows_it_was_read_off(self, puzzle):
        hint = get_hint(puzzle, empty_state(puzzle), 0)
        assert hint.reveals
        assert all(
            ref.startswith(("clue:", "fact:")) for ref in hint.reveals
        )

    def test_the_dear_end_of_the_ladder_costs_more(self, puzzle):
        """The cost is read off the measurement, not chosen per sentence.

        Measured against what the player holds, so the dear rungs are not at
        a fixed depth: on an empty board the three hints this game sells are
        all cheap exclusions, and the same board offers a statement worth two
        to a player who has already ruled three digits out, because half of
        what they have left turns on it.
        """
        missing = [digit for digit in range(10) if digit not in CODE][:3]
        state = {
            **empty_state(puzzle),
            "attempts": [{"guess": missing, "exact": 0, "misplaced": 0}],
        }
        assert all(hint.cost == 1 for hint in self.ladder(puzzle))
        assert any(
            hint.cost == 2 for hint in self.ladder(puzzle, state=state)
        ), "no rung of this board's ladder is charged as expensive"

    def test_the_ladder_ends_before_the_code_does(self, puzzle):
        """Exhaustion is a refusal to spoil, not an absence of true claims."""
        last = self.ladder(puzzle, count=40)[-1]
        assert last.exhausted
        assert last.cost == 0

    def test_the_board_sells_only_as_many_hints_as_it_declares(self, puzzle):
        """The cap, which is a policy and not a claim about deduction.

        The witness on this board holds fourteen statements and the walk
        offers ten of them before the next would be the code. What a player
        can buy is three, and the fourth request is refused while there is
        still plenty left that would be true to say.
        """
        ladder = self.ladder(puzzle, count=MAX_HINTS + 2)
        assert all(not hint.exhausted for hint in ladder[: MAX_HINTS - 1])
        assert ladder[MAX_HINTS - 1].exhausted
        assert all(hint.cost == 0 for hint in ladder[MAX_HINTS:])
        assert len(upcoming(puzzle, empty_state(puzzle), 0, count=40)) > MAX_HINTS

    def test_the_rungs_are_re_costed_against_what_the_player_holds(self, puzzle):
        """The walk, not an index into the witness.

        Read absolutely, this board's ladder opens with seven exclusions
        before its first inclusion. Re-costed as the player accumulates them,
        the fourth exclusion is dearer than the first inclusion, so the two
        kinds interleave -- and three of the witness's statements go vacuous
        on the way, which is why they are dropped rather than sold.
        """
        absolute = [step.statement for step in witness_for(puzzle).steps]
        walked = [
            step.statement
            for step in upcoming(puzzle, empty_state(puzzle), 0, count=40)
        ]
        assert walked != absolute[: len(walked)]
        assert len(walked) < len(absolute)
        kinds = ["not in" in statement for statement in walked]
        assert kinds != sorted(kinds, reverse=True), (
            "the walk should interleave inclusions among the exclusions; "
            "all exclusions first is the absolute order"
        )

    def test_no_hint_gives_the_code_in_order(self, puzzle):
        """A statement that alone determines the code is never offered."""
        spelled = "".join(str(digit) for digit in CODE)
        for hint in self.ladder(puzzle, count=40):
            assert spelled not in hint.text

    def test_a_hint_the_player_already_bought_is_skipped(self, puzzle):
        """An attempt told nothing of its digits is in the code is knowledge.

        The first rung of the ladder is an exclusion, and a player whose own
        guess established that exclusion is not offered it back.
        """
        missing = [digit for digit in range(10) if digit not in CODE][:3]
        state = {
            **empty_state(puzzle),
            "attempts": [{"guess": missing, "exact": 0, "misplaced": 0}],
        }
        offered = get_hint(puzzle, state, 0).text
        for digit in missing:
            assert f"The digit {digit} is not in the code" != offered

    def test_a_finished_puzzle_offers_nothing(self, puzzle):
        hint = get_hint(puzzle, {**empty_state(puzzle), "complete": True}, 0)
        assert hint.exhausted and hint.cost == 0


class TestScoring:
    def test_deducing_it_without_a_wrong_guess_pays_both_awards(self, puzzle):
        points = score(puzzle, telemetry(difficulty_score=0.0)).points
        assert points == POINTS_FOR_CODE + POINTS_FIRST_TRY

    def test_the_first_try_bonus_is_the_largest_relative_award(self, puzzle):
        """A first-try answer means the deduction was completed before
        anything was submitted, which is the whole of what the format asks."""
        assert POINTS_FIRST_TRY / POINTS_FOR_CODE >= 0.5

    def test_difficulty_adds_up_to_its_bonus(self, puzzle):
        points = score(puzzle, telemetry(difficulty_score=1.0)).points
        assert points == POINTS_FOR_CODE + POINTS_FIRST_TRY + DIFFICULTY_BONUS

    def test_mistakes_and_hints_are_charged(self, puzzle):
        result = score(
            puzzle,
            telemetry(attempts=2, mistakes=1, hints_used=1, difficulty_score=0.0),
        )
        assert result.points == max(
            0, POINTS_FOR_CODE - PENALTY_PER_MISTAKE - PENALTY_PER_HINT
        )

    def test_an_unopened_lock_scores_nothing_positive(self, puzzle):
        assert score(
            puzzle, telemetry(completed=False, attempts=4, mistakes=4)
        ).points == 0

    def test_the_breakdown_explains_the_total(self, puzzle):
        result = score(puzzle, telemetry(mistakes=1, difficulty_score=0.5))
        assert result.points == max(0, sum(result.breakdown.values()))


class TestRendering:
    def test_the_clue_rows_and_the_stated_facts_are_elements(self, puzzle):
        model = render(puzzle, empty_state(puzzle), "en")
        kinds = {element["kind"] for element in model.elements}
        assert kinds == {"clue", "fact"}

    def test_every_clue_row_carries_its_glyphs_and_labels(self, puzzle):
        model = render(puzzle, empty_state(puzzle), "en")
        for element in model.elements:
            if element["kind"] != "clue":
                continue
            assert len(element["glyphs"]) == len(element["digits"])
            assert all(element["labels"])

    def test_a_players_own_attempts_become_rows(self, puzzle):
        judgement = grade_move(puzzle, {"code": WRONG}, empty_state(puzzle))
        model = render(puzzle, judgement.state, "en")
        attempts = [e for e in model.elements if e["kind"] == "attempt"]
        assert len(attempts) == 1
        assert attempts[0]["digits"] == WRONG

    def test_the_render_never_names_the_code_before_the_end(self, puzzle):
        model = render(puzzle, empty_state(puzzle), "en")
        assert "code" not in model.labels

    def test_the_code_appears_only_through_the_state(self, puzzle):
        state = empty_state(puzzle)
        for _ in range(MAX_MISTAKES):
            state = grade_move(puzzle, {"code": WRONG}, state).state
        model = render(puzzle, state, "en")
        assert model.labels["code"] == "".join(str(digit) for digit in CODE)

    def test_the_state_reports_the_tries_left(self, puzzle):
        model = render(puzzle, empty_state(puzzle), "en")
        assert model.state["tries_left"] == MAX_MISTAKES


class TestSharing:
    def test_one_row_per_submission_in_order(self, puzzle):
        events = (
            {"kind": "SUBMIT", "outcome": "INCORRECT"},
            {"kind": "HINT"},
            {"kind": "SUBMIT", "outcome": "CORRECT"},
        )
        artifact = create_share_artifact(
            puzzle,
            telemetry(attempts=2, mistakes=1, hints_used=1, events=events),
            "2026-10-01",
        )
        assert artifact.tokens == (("miss",), ("hint",), ("solved",))

    def test_every_token_is_in_the_declared_vocabulary(self, puzzle):
        declared = {token.name for token in SHARE_TOKENS}
        events = ({"kind": "SUBMIT", "outcome": "CORRECT"},)
        artifact = create_share_artifact(
            puzzle, telemetry(events=events), "2026-10-01"
        )
        for row in artifact.tokens:
            assert set(row) <= declared

    def test_the_share_carries_no_verdict(self, puzzle):
        """"Two exact" about an unnamed guess would still narrow the code for
        a reader who then plays the same board."""
        assert {token.name for token in SHARE_TOKENS} == {"solved", "miss", "hint"}
        artifact = create_share_artifact(
            puzzle, telemetry(attempts=2, mistakes=1), "2026-10-01"
        )
        text = repr(artifact)
        assert "".join(str(digit) for digit in CODE) not in text

    def test_an_unopened_lock_says_so(self, puzzle):
        artifact = create_share_artifact(
            puzzle, telemetry(completed=False, attempts=4, mistakes=4), "2026-10-01"
        )
        assert artifact.outcome == "unsolved"

    def test_a_board_with_no_events_still_renders_its_attempts(self, puzzle):
        artifact = create_share_artifact(puzzle, telemetry(attempts=2), "2026-10-01")
        assert len(artifact.tokens) == 2
