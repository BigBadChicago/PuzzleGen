"""Turning a candidate into a board, and keeping the code off it.

The payload/solution split is the sharpest in the repository, because the
answer is a short sequence of digits and the board is full of digit sequences.
A clue row is a code; so is the answer. The rules that keep them apart are
specific rather than general, and each has a test that breaks it.
"""

from __future__ import annotations

import pytest

from puzzlegen.core.rng import DeterministicRng
from puzzlegen.engine.plugin import PuzzleCandidate
from puzzlegen_digits.assemble import (
    answer_code,
    assemble,
    board_digits,
    board_facts,
    board_guesses,
    clue_order,
    leaks_solution,
)
from puzzlegen_digits.descriptor import GAME_ID, MAX_CLUES
from puzzlegen_digits.generate import payload_for, plan_for, readable
from puzzlegen_digits.lock import PROPERTY_KINDS, CodeError, verdict

from boards import digits, result


def plan(code_length: int = 3, properties=PROPERTY_KINDS, seed: bytes = b"asm" * 10):
    chosen, known, entity_ids, index = readable(result(digits()))
    made, reason = plan_for(
        digits=chosen,
        facts=known,
        entity_ids=entity_ids,
        index=index,
        code_length=code_length,
        properties=properties,
        rng=DeterministicRng(seed),
    )
    assert made is not None, reason
    return made


def candidate(code_length: int = 3, **overrides) -> PuzzleCandidate:
    made = plan(code_length)
    return PuzzleCandidate(
        candidate_id=made.candidate_id(),
        payload={**payload_for(made), **overrides},
        fact_refs=made.dependency_ids(),
    )


def rng() -> DeterministicRng:
    return DeterministicRng(b"layout" * 8)


class TestTheBoard:
    def test_the_board_lists_every_digit_with_a_glyph_and_a_label(self):
        puzzle = assemble(candidate(), rng())
        assert board_digits(puzzle) == tuple(range(10))
        for digit in board_digits(puzzle):
            assert puzzle.payload["glyphs"][str(digit)]
            assert puzzle.payload["labels"][str(digit)]

    def test_the_clue_rows_are_the_guesses_with_their_counts(self):
        puzzle = assemble(candidate(), rng())
        assert board_guesses(puzzle)
        for clue in board_guesses(puzzle):
            assert verdict(clue.guess, answer_code(puzzle)) == (
                clue.exact,
                clue.misplaced,
            )

    def test_the_stated_facts_carry_their_wording(self):
        puzzle = assemble(candidate(), rng())
        assert board_facts(puzzle)
        for row in puzzle.payload["facts"]:
            assert row["text"]

    def test_a_fact_arriving_without_wording_gets_it(self):
        """A client inventing its own phrasing would be inventing the
        puzzle's statement."""
        made = plan()
        payload = payload_for(made)
        payload["properties"] = [
            {"kind": row["kind"], "value": row["value"]}
            for row in payload["properties"]
        ]
        built = PuzzleCandidate(
            candidate_id="x", payload=payload, fact_refs=made.dependency_ids()
        )
        puzzle = assemble(built, rng())
        assert all(row["text"] for row in puzzle.payload["facts"])

    def test_the_clue_rows_are_laid_out_rather_than_left_in_build_order(self):
        """The construction adds the most informative clue first, so a board
        in that order would put the clue worth reading at the top, every day.
        """
        made = plan(4)
        built = PuzzleCandidate(
            candidate_id=made.candidate_id(),
            payload=payload_for(made),
            fact_refs=made.dependency_ids(),
        )
        puzzle = assemble(built, rng())
        assert [list(clue.guess) for clue in board_guesses(puzzle)] != [
            list(clue.guess) for clue in made.guesses
        ]

    def test_the_layout_is_reproducible(self):
        first = assemble(candidate(4), rng())
        second = assemble(candidate(4), rng())
        assert board_guesses(first) == board_guesses(second)

    def test_a_different_assembly_stream_lays_out_differently(self):
        first = assemble(candidate(4), DeterministicRng(b"one" * 10))
        second = assemble(candidate(4), DeterministicRng(b"two" * 10))
        assert set(board_guesses(first)) == set(board_guesses(second))
        assert board_guesses(first) != board_guesses(second)

    def test_the_shuffle_is_a_permutation_of_the_clues(self):
        rows = [{"guess": [index]} for index in range(5)]
        assert sorted(clue_order(rows, rng()), key=lambda r: r["guess"]) == rows


class TestTheSolution:
    def test_the_code_is_a_legal_code(self):
        puzzle = assemble(candidate(), rng())
        code = answer_code(puzzle)
        assert len(code) == 3 == len(set(code))
        assert set(code) <= set(board_digits(puzzle))

    def test_the_digit_facts_travel_with_the_solution(self):
        """So the verifier evaluates a stated fact against the facts the clue
        was made from rather than against arithmetic of its own."""
        puzzle = assemble(candidate(), rng())
        stated = puzzle.solution["digit_facts"]
        assert len(stated) == 10
        assert stated["7"] == {"value": 7, "prime": True, "even": False}

    def test_the_search_the_generator_did_is_recorded(self):
        puzzle = assemble(candidate(), rng())
        assert puzzle.solution["space_size"] == 720
        assert puzzle.solution["before_last"] >= 1


class TestThePayloadDoesNotAnswerIt:
    def test_nothing_in_the_payload_leaks_the_code(self):
        assert leaks_solution(assemble(candidate(), rng())) == ()

    def test_the_payload_holds_no_answer_shaped_key(self):
        puzzle = assemble(candidate(), rng())
        for key in ("code", "answer", "solution", "secret"):
            assert key not in puzzle.payload

    def test_a_planted_code_key_is_caught(self):
        puzzle = assemble(candidate(), rng())
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload={**puzzle.payload, "code": []},
            solution=puzzle.solution,
            fact_refs=puzzle.fact_refs,
        )
        assert leaks_solution(tampered)

    def test_a_planted_clue_equal_to_the_code_is_caught(self):
        puzzle = assemble(candidate(), rng())
        clues = [
            {
                "kind": "guess",
                "guess": list(answer_code(puzzle)),
                "exact": 1,
                "misplaced": 0,
            }
        ]
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload={**puzzle.payload, "clues": clues},
            solution=puzzle.solution,
            fact_refs=puzzle.fact_refs,
        )
        assert leaks_solution(tampered)

    def test_a_planted_list_holding_the_code_in_order_is_caught(self):
        """The check that matters: every digit of the code is legitimately in
        the payload many times, so what cannot appear is the order."""
        puzzle = assemble(candidate(), rng())
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload={**puzzle.payload, "hint": list(answer_code(puzzle))},
            solution=puzzle.solution,
            fact_refs=puzzle.fact_refs,
        )
        assert leaks_solution(tampered)

    def test_the_codes_digits_scattered_across_a_clue_are_not_a_leak(self):
        """A detector that flagged the digits rather than the order would
        flag every correct board, since a clue row is made of digits."""
        puzzle = assemble(candidate(), rng())
        assert leaks_solution(puzzle) == ()
        assert any(
            set(clue.guess) & set(answer_code(puzzle))
            for clue in board_guesses(puzzle)
        )


class TestAssemblyChecksWhatGenerationPromised:
    @pytest.mark.parametrize(
        "key",
        ["code_length", "digits", "code", "guesses", "properties", "glyphs", "digit_facts"],
    )
    def test_a_missing_field_is_named(self, key):
        made = plan()
        payload = {k: v for k, v in payload_for(made).items() if k != key}
        broken = PuzzleCandidate(
            candidate_id="x", payload=payload, fact_refs=made.dependency_ids()
        )
        with pytest.raises(ValueError, match=key):
            assemble(broken, rng())

    def test_an_illegal_code_is_refused(self):
        with pytest.raises(CodeError, match="no repeated digit"):
            assemble(candidate(3, code=[1, 1, 2]), rng())

    def test_a_code_of_the_wrong_length_is_refused(self):
        with pytest.raises(CodeError, match="is 3 digits"):
            assemble(candidate(3, code=[1, 2, 3, 4]), rng())

    def test_a_clue_whose_counts_are_not_true_is_refused(self):
        """Caught here rather than by the verifier, because a board whose
        clues contradict its answer costs a whole enumeration to discover."""
        made = plan()
        payload = payload_for(made)
        payload["guesses"][0] = {**payload["guesses"][0], "exact": 2, "misplaced": 1}
        broken = PuzzleCandidate(
            candidate_id="x", payload=payload, fact_refs=made.dependency_ids()
        )
        with pytest.raises(ValueError, match="not true of the code"):
            assemble(broken, rng())

    def test_a_clue_reporting_every_digit_exact_is_refused(self):
        made = plan()
        payload = payload_for(made)
        payload["guesses"][0] = {
            "guess": list(made.code),
            "exact": len(made.code),
            "misplaced": 0,
        }
        broken = PuzzleCandidate(
            candidate_id="x", payload=payload, fact_refs=made.dependency_ids()
        )
        with pytest.raises(ValueError, match="is the answer"):
            assemble(broken, rng())

    def test_a_board_with_no_clues_at_all_is_refused(self):
        made = plan(properties=())
        payload = {**payload_for(made), "guesses": [], "properties": []}
        broken = PuzzleCandidate(
            candidate_id="x", payload=payload, fact_refs=made.dependency_ids()
        )
        with pytest.raises(ValueError, match="states nothing"):
            assemble(broken, rng())

    def test_too_many_clues_is_refused(self):
        made = plan()
        row = payload_for(made)["guesses"][0]
        payload = {**payload_for(made), "guesses": [row] * (MAX_CLUES + 1)}
        broken = PuzzleCandidate(
            candidate_id="x", payload=payload, fact_refs=made.dependency_ids()
        )
        with pytest.raises(ValueError, match="at most"):
            assemble(broken, rng())

    def test_a_digit_with_no_glyph_is_refused(self):
        made = plan()
        payload = payload_for(made)
        payload["glyphs"] = {**payload["glyphs"], "7": ""}
        broken = PuzzleCandidate(
            candidate_id="x", payload=payload, fact_refs=made.dependency_ids()
        )
        with pytest.raises(ValueError, match="no glyph"):
            assemble(broken, rng())

    def test_a_digit_with_no_facts_is_refused(self):
        made = plan()
        payload = payload_for(made)
        payload["digit_facts"] = {
            k: v for k, v in payload["digit_facts"].items() if k != "7"
        }
        broken = PuzzleCandidate(
            candidate_id="x", payload=payload, fact_refs=made.dependency_ids()
        )
        with pytest.raises(ValueError, match="no facts for digits"):
            assemble(broken, rng())

    def test_the_puzzle_claims_this_games_id(self):
        assert assemble(candidate(), rng()).game_id == GAME_ID

    def test_assembly_widens_nothing(self):
        built = candidate()
        assert set(assemble(built, rng()).fact_refs) == set(built.fact_refs)
