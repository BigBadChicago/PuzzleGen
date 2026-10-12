"""Turning a candidate into a board, and keeping the answer off it.

The board's order is its values, so there is no shuffle and nothing random
about the layout. That leaves two things to test: assembly validates every
promise the candidate makes, and the payload does not answer the puzzle. The
second is stricter here than in the other games, because the shared property
is a name with no legitimate place on the board at all -- a client printing it
would be printing the answer in words.
"""

from __future__ import annotations

import pytest

from puzzlegen.core.rng import DeterministicRng
from puzzlegen.engine.plugin import PuzzleCandidate
from puzzlegen_numbers.assemble import (
    assemble,
    board_memberships,
    board_tiles,
    board_values,
    leaks_solution,
)
from puzzlegen_numbers.descriptor import GAME_ID
from puzzlegen_numbers.generate import payload_for, plan_for, readable
from puzzlegen_numbers.properties import MIN_TIER, holds_for_all_but

from boards import result, window


def plan(board_size: int = 6, tier: int = MIN_TIER, seed: bytes = b"assemble" * 4):
    pool, values, memberships, index = readable(result(window()))
    made, reason = plan_for(
        pool=pool,
        values=values,
        memberships=memberships,
        index=index,
        board_size=board_size,
        tier=tier,
        rng=DeterministicRng(seed),
    )
    assert made is not None, reason
    return made


def candidate(board_size: int = 6, **overrides) -> PuzzleCandidate:
    made = plan(board_size)
    return PuzzleCandidate(
        candidate_id=made.candidate_id(),
        payload={**payload_for(made), **overrides},
        fact_refs=made.dependency_ids(),
    )


def rng() -> DeterministicRng:
    return DeterministicRng(b"unused" * 8)


class TestTheBoard:
    def test_a_board_carries_every_tile_once(self):
        puzzle = assemble(candidate(), rng())
        tiles = board_tiles(puzzle)
        assert len(tiles) == 6 == puzzle.payload["board_size"]
        assert len(set(tiles)) == 6

    def test_every_tile_carries_a_glyph_a_label_and_a_value(self):
        puzzle = assemble(candidate(), rng())
        for tile in puzzle.payload["tiles"]:
            assert tile["glyph"] and tile["label"]
            assert isinstance(tile["value"], int)

    def test_the_board_is_one_row_in_ascending_order(self):
        puzzle = assemble(candidate(), rng())
        assert puzzle.payload["layout"] == "row"
        assert puzzle.payload["ordered"] == "ascending"
        values = [tile["value"] for tile in puzzle.payload["tiles"]]
        assert values == sorted(values)

    def test_the_layout_does_not_depend_on_the_assembly_stream(self):
        """There is no shuffle: the order is a pure function of the values the
        player can read off the tiles, so a drawn order would only cost the
        solver a mental sort."""
        first = assemble(candidate(), DeterministicRng(b"one" * 10))
        second = assemble(candidate(), DeterministicRng(b"two" * 10))
        assert board_tiles(first) == board_tiles(second)

    def test_the_glyph_is_the_digits(self):
        puzzle = assemble(candidate(), rng())
        for tile in puzzle.payload["tiles"]:
            assert tile["glyph"] == str(tile["value"])


class TestTheSolution:
    def test_the_answer_is_a_tile_on_the_board(self):
        puzzle = assemble(candidate(), rng())
        assert puzzle.solution["answer"] in board_tiles(puzzle)

    def test_the_property_is_true_of_every_tile_but_the_answer(self):
        puzzle = assemble(candidate(), rng())
        assert holds_for_all_but(
            puzzle.solution["property"],
            puzzle.solution["answer"],
            board_memberships(puzzle),
        )

    def test_the_memberships_cover_every_tile(self):
        puzzle = assemble(candidate(), rng())
        assert set(board_memberships(puzzle)) == set(board_tiles(puzzle))

    def test_the_spread_is_the_boards_own(self):
        puzzle = assemble(candidate(), rng())
        values = board_values(puzzle)
        assert puzzle.solution["spread"] == max(values.values()) - min(values.values())


class TestThePayloadDoesNotAnswerIt:
    def test_nothing_in_the_payload_leaks_the_answer(self):
        assert leaks_solution(assemble(candidate(), rng())) == ()

    def test_the_payload_holds_no_answer_shaped_key(self):
        puzzle = assemble(candidate(), rng())
        for key in ("answer", "property", "reasons", "memberships", "vocabulary"):
            assert key not in puzzle.payload

    def test_a_planted_property_name_is_caught(self):
        """The leak that would matter most: a client showing the property
        would be printing the answer in words."""
        puzzle = assemble(candidate(), rng())
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload={**puzzle.payload, "hint": puzzle.solution["property"]},
            solution=puzzle.solution,
            fact_refs=puzzle.fact_refs,
        )
        assert any("property" in leak for leak in leaks_solution(tampered))

    def test_a_planted_second_appearance_of_the_answer_tile_is_caught(self):
        """The check is for a second appearance, because the first is the tile
        itself. A detector flagging the id outright would flag every board."""
        puzzle = assemble(candidate(), rng())
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload={**puzzle.payload, "note": puzzle.solution["answer"]},
            solution=puzzle.solution,
            fact_refs=puzzle.fact_refs,
        )
        assert leaks_solution(tampered)


class TestAssemblyChecksWhatGenerationPromised:
    @pytest.mark.parametrize(
        "key", ["board_size", "tiles", "answer", "property", "memberships"]
    )
    def test_a_missing_field_is_named(self, key):
        made = plan()
        payload = {k: v for k, v in payload_for(made).items() if k != key}
        broken = PuzzleCandidate(
            candidate_id="x", payload=payload, fact_refs=made.dependency_ids()
        )
        with pytest.raises(ValueError, match=key):
            assemble(broken, rng())

    def test_a_board_whose_tile_count_disagrees_is_refused(self):
        made = plan()
        broken = PuzzleCandidate(
            candidate_id="x",
            payload={**payload_for(made), "board_size": 9},
            fact_refs=made.dependency_ids(),
        )
        with pytest.raises(ValueError, match="was built from"):
            assemble(broken, rng())

    def test_an_answer_not_on_the_board_is_refused(self):
        with pytest.raises(ValueError, match="not a tile on the board"):
            assemble(candidate(6, answer="entity:1000"), rng())

    def test_a_property_that_is_not_a_reading_is_refused(self):
        """Caught here rather than by the verifier, because a board with no
        reading verifies as unsolvable and costs a whole enumeration."""
        with pytest.raises(ValueError, match="is not true of every tile but"):
            assemble(candidate(6, property="Fibonacci number"), rng())

    def test_tiles_out_of_order_are_refused_rather_than_sorted(self):
        """Generation already sorts, so this is a bug rather than a repair.
        Quietly sorting would hide a candidate that disagrees with its plan
        while producing a board that happens to look right."""
        made = plan()
        payload = payload_for(made)
        payload["tiles"] = list(reversed(payload["tiles"]))
        broken = PuzzleCandidate(
            candidate_id="x", payload=payload, fact_refs=made.dependency_ids()
        )
        with pytest.raises(ValueError, match="not in ascending order"):
            assemble(broken, rng())

    def test_a_tile_with_no_glyph_is_refused(self):
        made = plan()
        payload = payload_for(made)
        payload["tiles"][0] = {**payload["tiles"][0], "glyph": ""}
        broken = PuzzleCandidate(
            candidate_id="x", payload=payload, fact_refs=made.dependency_ids()
        )
        with pytest.raises(ValueError, match="no glyph"):
            assemble(broken, rng())

    def test_a_tile_with_no_value_is_refused(self):
        made = plan()
        payload = payload_for(made)
        payload["tiles"][0] = {**payload["tiles"][0], "value": "ten"}
        broken = PuzzleCandidate(
            candidate_id="x", payload=payload, fact_refs=made.dependency_ids()
        )
        with pytest.raises(ValueError, match="no numeric value"):
            assemble(broken, rng())

    def test_a_tile_with_no_memberships_is_refused(self):
        made = plan()
        payload = payload_for(made)
        payload["memberships"] = dict(list(payload["memberships"].items())[1:])
        broken = PuzzleCandidate(
            candidate_id="x", payload=payload, fact_refs=made.dependency_ids()
        )
        with pytest.raises(ValueError, match="carry no memberships"):
            assemble(broken, rng())

    def test_two_tiles_sharing_an_id_are_refused(self):
        made = plan()
        payload = payload_for(made)
        payload["tiles"][1] = {**payload["tiles"][1], "id": payload["tiles"][0]["id"]}
        broken = PuzzleCandidate(
            candidate_id="x", payload=payload, fact_refs=made.dependency_ids()
        )
        with pytest.raises(ValueError, match="share an id"):
            assemble(broken, rng())

    def test_the_puzzle_claims_this_games_id(self):
        assert assemble(candidate(), rng()).game_id == GAME_ID

    def test_assembly_widens_nothing(self):
        built = candidate()
        assert set(assemble(built, rng()).fact_refs) == set(built.fact_refs)
