"""Counting the readings of a board, honestly.

Boards are built directly here rather than generated, so each test can create
the exact shape it is about: a board with two true readings, a board whose
marks do not spell its own answer, a board whose vocabulary overran the bound.
Generation refuses most of these, which is why they have to be built: the
verifier is the thing that would have to catch them if generation ever
stopped, and a verifier that assumed its generator was right is not a check.
"""

from __future__ import annotations

from puzzlegen.core.types import UniquenessContract, VerificationCompleteness
from puzzlegen.engine.uniqueness import check, consistency_problem
from puzzlegen_letters.descriptor import DESCRIPTOR
from puzzlegen_letters.verify import (
    ANSWER_CANDIDATES,
    LETTER_ALTERNATIVES,
    MANUFACTURED_BLANKS,
    MARKED_TILES,
    VOCABULARY_SIZE,
    board_problem,
    readings_of,
    verify,
)

from boards import FISH, fish_board, make_puzzle

#: Six gems that between them hold the letters of both ``gem`` and ``gym``.
GEMS = ("agate", "amber", "garnet", "jade", "opal", "ruby")


class TestTheOneReading:
    def test_the_worked_example_has_exactly_one(self):
        result = verify(fish_board())
        assert result.solvable
        assert result.solution_count == 1
        assert result.completeness is VerificationCompleteness.COMPLETE

    def test_it_satisfies_the_declared_contract(self):
        verdict = check(DESCRIPTOR, verify(fish_board()))
        assert verdict, verdict.reason

    def test_the_contract_it_satisfies_is_about_readings(self):
        assert (
            DESCRIPTOR.uniqueness_contract
            is UniquenessContract.NO_ALTERNATE_INTERPRETATION
        )

    def test_the_result_is_self_consistent(self):
        assert consistency_problem(verify(fish_board())) is None

    def test_the_intended_answer_is_reported_in_the_engine_s_own_shape(self):
        """The engine hashes the puzzle's solution and looks for that hash
        among the ones reported, which only works if the intended answer comes
        back as the puzzle's own solution object."""
        board = fish_board()
        result = verify(board)
        assert dict(result.solutions[0]) == dict(board.solution)

    def test_a_category_one_tile_lacks_is_not_a_reading(self):
        memberships = {f"entity:{name}": ["fish"] for name in FISH}
        memberships["entity:carp"] = ["fish"]
        memberships["entity:bass"] = ["fish", "food"]
        board = fish_board(memberships={
            name: names for name, names in
            ((key.removeprefix("entity:"), value) for key, value in memberships.items())
        })
        assert readings_of({k: frozenset(v) for k, v in board.solution["memberships"].items()}) == frozenset({"fish"})


class TestASecondReadingIsCounted:
    def test_two_categories_the_marks_and_blanks_both_spell(self):
        """``gem`` and ``gym`` are both true of every tile and both three
        letters, so a player giving either is right and the board can only
        mark one of them."""
        board = make_puzzle(
            "gem",
            {"garnet": "g"},
            [name for name in GEMS if name != "garnet"],
            blanks=2,
            memberships={name: ["gem", "gym"] for name in GEMS},
            vocabulary=("gem", "gym"),
        )
        result = verify(board)
        assert result.solution_count == 2
        assert "more than one true reading" in result.detail

    def test_the_engine_refuses_it(self):
        board = make_puzzle(
            "gem",
            {"garnet": "g"},
            [name for name in GEMS if name != "garnet"],
            blanks=2,
            memberships={name: ["gem", "gym"] for name in GEMS},
            vocabulary=("gem", "gym"),
        )
        verdict = check(DESCRIPTOR, verify(board))
        assert not verdict
        assert "one solution" in verdict.reason

    def test_a_reading_of_the_wrong_length_is_not_one(self):
        """The board shows how many slots there are."""
        board = make_puzzle(
            "fish",
            {"herring": "h", "bass": "s"},
            [name for name in FISH if name not in ("herring", "bass")],
            memberships={name: ["fish", "food"] for name in FISH},
            vocabulary=("fish", "food"),
        )
        assert verify(board).solution_count == 1


class TestTheWordsItDidNotAnswerWith:
    def test_a_word_the_letters_admit_but_the_tiles_refuse_is_a_near_miss(self):
        """Not an answer, because it is untrue of at least one tile, and not
        nothing either: it is what a solver's own anagram turns up."""
        board = make_puzzle(
            "gem",
            {"garnet": "g"},
            [name for name in GEMS if name != "garnet"],
            blanks=2,
            vocabulary=("gem", "gym", "fish"),
        )
        result = verify(board)
        assert result.solution_count == 1
        assert result.metrics[LETTER_ALTERNATIVES] == 1.0

    def test_the_metrics_name_what_was_searched(self):
        result = verify(fish_board(vocabulary=("fish", "food", "gem")))
        assert result.metrics[VOCABULARY_SIZE] == 3.0
        assert result.metrics[ANSWER_CANDIDATES] == 1.0
        assert result.metrics[MARKED_TILES] == 2.0
        assert MANUFACTURED_BLANKS in result.metrics


class TestTheBoundIsReal:
    def test_a_vocabulary_within_the_bound_is_complete(self):
        result = verify(fish_board(), word_budget=10)
        assert result.completeness is VerificationCompleteness.COMPLETE

    def test_a_vocabulary_beyond_it_reports_a_lower_bound(self):
        """The words beyond the budget are where a second answer would hide,
        so the count is evidence of nothing on its own."""
        result = verify(fish_board(vocabulary=tuple(f"word{n}" for n in range(50))), word_budget=5)
        assert result.completeness is VerificationCompleteness.SOUND_INCOMPLETE
        assert "lower bound" in result.detail

    def test_the_engine_refuses_a_board_whose_search_was_cut_short(self):
        result = verify(
            fish_board(vocabulary=tuple(f"word{n}" for n in range(50))), word_budget=5
        )
        verdict = check(DESCRIPTOR, result)
        assert not verdict
        assert "incomplete" in verdict.reason


class TestABoardThatCannotBeSolvedAsShown:
    def test_a_board_below_the_blank_floor(self):
        board = make_puzzle(
            "fish",
            {"herring": "h", "bass": "s", "halibut": "i"},
            ["salmon", "trout", "carp"],
        )
        result = verify(board)
        assert not result.solvable
        assert "floor" in result.detail

    def test_a_board_with_no_marks(self):
        board = make_puzzle("fish", {}, list(FISH), blanks=4)
        result = verify(board)
        assert not result.solvable
        assert "no tile carries a mark" in result.detail

    def test_a_board_where_every_tile_is_marked(self):
        board = make_puzzle(
            "obscurity",
            {
                "opal": "o",
                "amber": "b",
                "jade": "a",
                "ruby": "r",
                "agate": "t",
                "garnet": "e",
            },
            [],
            blanks=3,
        )
        result = verify(board)
        assert not result.solvable
        assert "one does not" in result.detail

    def test_marks_and_blanks_that_do_not_add_up(self):
        board = make_puzzle(
            "fish", {"herring": "h", "bass": "s"}, ["salmon", "trout"], blanks=5
        )
        result = verify(board)
        assert not result.solvable
        assert "cannot spell" in result.detail

    def test_a_board_showing_a_different_number_of_slots_than_the_answer_has(self):
        board = make_puzzle(
            "fish",
            {"herring": "h", "bass": "s"},
            ["salmon", "trout"],
            answer_length=9,
        )
        result = verify(board)
        assert not result.solvable
        assert "slots" in result.detail

    def test_a_mark_that_is_not_in_its_own_tile(self):
        board = make_puzzle(
            "fish", {"salmon": "f", "bass": "s"}, ["trout", "carp"], blanks=2
        )
        result = verify(board)
        assert not result.solvable
        assert "decoration" in result.detail

    def test_a_mark_the_answer_does_not_contain(self):
        board = make_puzzle(
            "fish", {"trout": "t", "bass": "s"}, ["salmon", "carp"], blanks=2
        )
        result = verify(board)
        assert not result.solvable
        assert "does not have" in result.detail

    def test_tiles_with_no_memberships_at_all(self):
        board = fish_board(memberships={})
        result = verify(board)
        assert not result.solvable
        assert "no category memberships" in result.detail

    def test_an_answer_the_board_cannot_spell_is_not_found(self):
        """Verifies, is unique, and marks the player wrong: the most dangerous
        way for a board to be broken."""
        board = make_puzzle(
            "fish",
            {"herring": "h", "bass": "s"},
            ["salmon", "trout"],
            memberships={name: ["food"] for name in ("herring", "bass", "salmon", "trout")},
            vocabulary=("food",),
        )
        result = verify(board)
        assert result.solution_count == 0 or "not among the readings" in result.detail


class TestTheProblemCheckInIsolation:
    def test_a_clean_board_has_no_problem(self):
        assert board_problem(fish_board()) is None
