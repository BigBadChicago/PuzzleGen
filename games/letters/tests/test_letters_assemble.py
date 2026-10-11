"""Turning a candidate into a board, and the spoiler it must not be.

Two groups of tests. The first is what assembly checks about what generation
handed it: a mismatch there is a bug in this game, and a bug that reaches the
verifier costs a whole enumeration to discover. The second is the
payload/solution split, which on this board is sharper than on a grouping
board because the answer is one word: a payload carrying it anywhere is a
board a client can read the answer off.
"""

from __future__ import annotations

import pytest

from puzzlegen.core.rng import DeterministicRng, derive_seed
from puzzlegen.engine.plugin import PuzzleCandidate
from puzzlegen.surface import SurfaceIndex
from puzzlegen_letters.assemble import (
    assemble,
    board_marks,
    board_tiles,
    columns_for,
    leaks_solution,
    tile_order,
    unmarked_tiles,
)
from puzzlegen_letters.descriptor import BOARD_SIZES, GAME_ID
from puzzlegen_letters.generate import payload_for, plan_for
from puzzlegen_letters.marks import SURFACE_POLICY

from boards import FISH, group

DAY = "2026-10-01"


def rng(label: str = "assemble") -> DeterministicRng:
    return DeterministicRng(derive_seed(DAY, GAME_ID)).derive(label)


def payload(category: str = "fish", members=FISH, *, blanks: int = 2, board_size: int = 6):
    plan, reason = plan_for(
        group(category, members),
        board_size=board_size,
        blanks=blanks,
        vocabulary=(category,),
        index=SurfaceIndex(policy=SURFACE_POLICY),
        rng=rng("marks"),
    )
    assert plan is not None, reason
    return payload_for(plan)


def candidate(**overrides) -> PuzzleCandidate:
    built = payload(**overrides.pop("payload", {}))
    built.update(overrides)
    return PuzzleCandidate(
        candidate_id="missing_letters:test",
        payload=built,
        fact_refs=("entity:salmon", "category:fish"),
    )


def board(**overrides):
    return assemble(candidate(**overrides), rng())


class TestTheBoardItBuilds:
    def test_every_member_becomes_a_tile(self):
        assembled = board()
        assert len(board_tiles(assembled)) == 6

    def test_the_marks_travel_with_their_tiles(self):
        assembled = board()
        assert board_marks(assembled) == assembled.solution["marks"]

    def test_an_unmarked_tile_says_so_rather_than_omitting_the_key(self):
        """A client cannot mistake an unmarked tile for one whose mark it
        failed to read."""
        assembled = board()
        assert all("mark" in tile for tile in assembled.payload["tiles"])
        assert unmarked_tiles(assembled)

    def test_the_board_shows_how_many_slots_the_answer_has(self):
        assembled = board()
        assert assembled.payload["answer_length"] == len("fish")
        assert assembled.payload["blanks"] == 2

    def test_the_layout_is_two_rows_at_every_supported_size(self):
        for size in BOARD_SIZES:
            assert size % columns_for(size) == 0
            assert size // columns_for(size) == 2

    def test_it_carries_the_candidate_s_dependencies_unchanged(self):
        """Assembly may narrow what a candidate declared but never widen it."""
        assembled = board()
        assert set(assembled.fact_refs) == {"entity:salmon", "category:fish"}

    def test_the_presentation_declares_an_optimum(self):
        """A game that declares none is scored as if every player were
        perfect."""
        assert board().presentation["optimal_attempts"] == 1


class TestTheLayoutIsReproducible:
    def test_the_same_candidate_lays_out_the_same_way(self):
        assert board_tiles(board()) == board_tiles(board())

    def test_the_board_is_not_in_the_order_the_content_issued(self):
        """Tiles arrive in entity id order, so an unshuffled board is
        alphabetical and its marks sit in a pattern with nothing to do with
        the puzzle."""
        tiles = sorted(f"entity:{name}" for name in FISH)
        assert tile_order(tiles, rng()) != tiles

    def test_a_different_stream_lays_out_differently(self):
        tiles = sorted(f"entity:{name}" for name in FISH)
        assert tile_order(tiles, rng("one")) != tile_order(tiles, rng("two"))


class TestItChecksWhatGenerationPromised:
    def test_a_tile_count_that_is_not_the_board_size(self):
        with pytest.raises(ValueError, match="tiles"):
            board(board_size=8)

    def test_fewer_blanks_than_the_floor(self):
        with pytest.raises(ValueError, match="fewer than"):
            board(blanks=1)

    def test_marks_and_blanks_that_cannot_spell_the_answer(self):
        with pytest.raises(ValueError, match="cannot spell"):
            board(blanks=3)

    def test_a_mark_on_a_tile_the_board_does_not_have(self):
        with pytest.raises(ValueError, match="not on the board"):
            board(marks={"entity:nothing": "n"})

    def test_a_mark_that_is_not_a_letter_of_its_own_tile(self):
        """Rule 4.2. A mark that is not in its own tile's word is
        decoration."""
        with pytest.raises(ValueError, match="decoration"):
            board(marks={"entity:carp": "z", "entity:bass": "s"})

    def test_a_board_where_every_tile_is_marked(self):
        """The settled rule: a solver has to be able to rule a tile out, which
        needs a tile that shows nothing. The arithmetic is made to add up so
        the board fails on the rule and not on the count."""
        with pytest.raises(ValueError, match="no mark"):
            board(
                letters="aaaaaaaa",
                blanks=2,
                marks={f"entity:{name}": "a" for name in FISH},
            )

    def test_letters_that_are_not_the_letters_of_the_category(self):
        with pytest.raises(ValueError, match="not the letters"):
            board(letters="fsh", blanks=2, marks={"entity:bass": "s"})

    def test_a_missing_key_names_itself(self):
        with pytest.raises(ValueError, match="missing 'letters'"):
            assemble(
                PuzzleCandidate(candidate_id="x", payload={}, fact_refs=("entity:a",)),
                rng(),
            )


class TestThePayloadIsNotASpoiler:
    def test_a_clean_board_leaks_nothing(self):
        assert leaks_solution(board()) == ()

    def test_the_answer_is_only_in_the_solution(self):
        assembled = board()
        assert assembled.solution["answer"] == "fish"
        assert "answer" not in assembled.payload

    def test_the_memberships_travel_with_the_answer(self):
        """A client holding them could intersect them and read the answer off
        the result."""
        assembled = board()
        assert "memberships" not in assembled.payload
        assert assembled.solution["memberships"]

    def test_the_record_of_which_blanks_were_manufactured_is_solution_side(self):
        assembled = board()
        assert "manufactured_blanks" not in assembled.payload
        assert "manufactured_blanks" in assembled.solution

    def test_a_planted_answer_in_the_payload_is_caught(self):
        assembled = board()
        tampered = type(assembled)(
            game_id=GAME_ID,
            payload={**assembled.payload, "hint": "fish"},
            solution=assembled.solution,
            fact_refs=assembled.fact_refs,
        )
        assert leaks_solution(tampered)

    def test_a_tile_label_that_merely_contains_the_answer_is_not_a_leak(self):
        """A ``sunflower`` tile on a ``flower`` board is legitimate; a
        substring check could not tell it from a real leak."""
        assembled = assemble(
            candidate(
                payload={
                    "category": "flower",
                    "members": (
                        "sunflower",
                        "tulip",
                        "daisy",
                        "orchid",
                        "poppy",
                        "lilac",
                    ),
                    "board_size": 6,
                    "blanks": 2,
                }
            ),
            rng(),
        )
        assert leaks_solution(assembled) == ()
