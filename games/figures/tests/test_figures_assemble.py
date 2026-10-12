"""Turning a candidate into a board, and keeping the answer off it.

Two things are tested here. Assembly validates what generation promised
rather than trusting it, so every promise has a test that breaks it. And the
payload must not answer the puzzle, which on this board means something more
specific than "no key called answer": the trio's tile ids are legitimately in
the payload once each, as their own tiles, so what must not happen is a
second appearance.
"""

from __future__ import annotations

import pytest

from puzzlegen.core.rng import DeterministicRng
from puzzlegen.engine.plugin import PuzzleCandidate
from puzzlegen_figures.assemble import (
    assemble,
    board_figures,
    board_tiles,
    columns_for,
    leaks_solution,
    position_of,
    tile_order,
)
from puzzlegen_figures.descriptor import BOARD_SIZES, GAME_ID, TRIO
from puzzlegen_figures.generate import payload_for, plan_for, readable
from puzzlegen_figures.trio import ATTRIBUTES, check_space, is_trio

from boards import SPACE, context, result, whole_space


def plan(board_size: int = 9, seed: bytes = b"assemble" * 4):
    views = whole_space()
    pool, figures, index = readable(result(views))
    space = check_space([figures[v.entity_id] for v in pool])
    made, reason = plan_for(
        pool=pool,
        figures=figures,
        index=index,
        space=space,
        board_size=board_size,
        rng=DeterministicRng(seed),
    )
    assert made is not None, reason
    return made


def candidate(board_size: int = 9, **overrides) -> PuzzleCandidate:
    made = plan(board_size)
    payload = {**payload_for(made), **overrides}
    return PuzzleCandidate(
        candidate_id=made.candidate_id(),
        payload=payload,
        fact_refs=made.dependency_ids(),
    )


def rng() -> DeterministicRng:
    return DeterministicRng(b"layout" * 8)


class TestTheBoard:
    @pytest.mark.parametrize("board_size", BOARD_SIZES)
    def test_a_board_carries_every_tile_once(self, board_size):
        puzzle = assemble(candidate(board_size), rng())
        tiles = board_tiles(puzzle)
        assert len(tiles) == board_size == puzzle.payload["board_size"]
        assert len(set(tiles)) == board_size

    def test_every_tile_carries_a_glyph_a_label_and_every_attribute(self):
        puzzle = assemble(candidate(), rng())
        for tile in puzzle.payload["tiles"]:
            assert tile["glyph"] and tile["label"]
            assert all(tile[attribute] for attribute in ATTRIBUTES)

    def test_the_colour_is_a_word_on_every_tile(self):
        """This game's whole claim to being operable without colour vision.

        Two figures can differ only in colour, so a client given a hue and
        nothing else would be drawing a board some players cannot read, and
        the engine's accessibility gate would be accepting a false promise.
        """
        puzzle = assemble(candidate(), rng())
        colours = {tile["colour"] for tile in puzzle.payload["tiles"]}
        assert colours <= set(SPACE["colour"])

    @pytest.mark.parametrize("board_size", BOARD_SIZES)
    def test_the_grid_is_three_wide_and_never_ragged(self, board_size):
        puzzle = assemble(candidate(board_size), rng())
        columns = puzzle.payload["columns"]
        assert columns == columns_for(board_size) == 3
        assert puzzle.payload["rows"] * columns == board_size

    def test_the_tiles_are_laid_out_rather_than_left_in_build_order(self):
        """A board in construction order would show its trio in the first
        three positions, every day, forever."""
        made = plan()
        puzzle = assemble(candidate(), rng())
        built_order = [view.entity_id for view in made.tiles]
        assert board_tiles(puzzle) != tuple(built_order)

    def test_the_layout_is_reproducible(self):
        first = assemble(candidate(), rng())
        second = assemble(candidate(), rng())
        assert board_tiles(first) == board_tiles(second)

    def test_a_different_assembly_stream_lays_out_differently(self):
        first = assemble(candidate(), DeterministicRng(b"one" * 10))
        second = assemble(candidate(), DeterministicRng(b"two" * 10))
        assert set(board_tiles(first)) == set(board_tiles(second))
        assert board_tiles(first) != board_tiles(second)

    def test_the_shuffle_is_a_permutation_of_the_tiles(self):
        ids = [f"entity:{index}" for index in range(9)]
        assert sorted(tile_order(ids, rng())) == sorted(ids)

    def test_positions_cover_the_grid(self):
        puzzle = assemble(candidate(9), rng())
        positions = position_of(puzzle)
        assert sorted(positions.values()) == [(r, c) for r in range(3) for c in range(3)]


class TestTheSolution:
    def test_the_solution_is_a_set_of_three_tiles(self):
        puzzle = assemble(candidate(), rng())
        trio = puzzle.solution["trio"]
        assert len(trio) == TRIO == len(set(trio))
        assert trio == sorted(trio)

    def test_the_trio_is_on_the_board_and_satisfies_the_rule(self):
        puzzle = assemble(candidate(), rng())
        figures = board_figures(puzzle)
        trio = puzzle.solution["trio"]
        assert set(trio) <= set(figures)
        assert is_trio([figures[tile_id] for tile_id in trio])

    def test_the_varying_attributes_are_the_ones_all_different(self):
        puzzle = assemble(candidate(), rng())
        figures = board_figures(puzzle)
        trio = puzzle.solution["trio"]
        expected = sorted(
            attribute
            for attribute in ATTRIBUTES
            if len({figures[tile_id][attribute] for tile_id in trio}) == TRIO
        )
        assert puzzle.solution["varying"] == expected


class TestThePayloadDoesNotAnswerIt:
    def test_nothing_in_the_payload_leaks_the_trio(self):
        puzzle = assemble(candidate(), rng())
        assert leaks_solution(puzzle) == ()

    def test_the_payload_holds_no_answer_shaped_key(self):
        puzzle = assemble(candidate(), rng())
        for key in ("trio", "answer", "solution", "varying", "figures"):
            assert key not in puzzle.payload

    def test_a_planted_second_appearance_is_caught(self):
        """The check is for a second appearance, because the first is the
        tile itself. A detector that flagged the id outright would flag every
        correct board."""
        puzzle = assemble(candidate(), rng())
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload={**puzzle.payload, "note": puzzle.solution["trio"][0]},
            solution=puzzle.solution,
            fact_refs=puzzle.fact_refs,
        )
        assert leaks_solution(tampered)

    def test_a_planted_answer_key_is_caught(self):
        puzzle = assemble(candidate(), rng())
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload={**puzzle.payload, "trio": []},
            solution=puzzle.solution,
            fact_refs=puzzle.fact_refs,
        )
        assert leaks_solution(tampered)


class TestAssemblyChecksWhatGenerationPromised:
    @pytest.mark.parametrize(
        "key", ["board_size", "tiles", "answer"]
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
            payload={**payload_for(made), "board_size": 12},
            fact_refs=made.dependency_ids(),
        )
        with pytest.raises(ValueError, match="was built from"):
            assemble(broken, rng())

    def test_a_trio_naming_a_tile_not_on_the_board_is_refused(self):
        with pytest.raises(ValueError, match="not on the board"):
            assemble(candidate(9, answer=["entity:nope", "entity:x", "entity:y"]), rng())

    def test_a_trio_of_fewer_than_three_distinct_tiles_is_refused(self):
        made = plan()
        repeated = [made.answer[0], made.answer[0], made.answer[1]]
        with pytest.raises(ValueError, match="distinct tiles"):
            assemble(candidate(9, answer=repeated), rng())

    def test_a_trio_that_does_not_satisfy_the_rule_is_refused(self):
        """Caught here rather than by the verifier, because a board whose own
        trio is not a trio verifies as unsolvable and costs a whole
        enumeration to find out."""
        made = plan()
        others = [
            view.entity_id for view in made.tiles if view.entity_id not in made.answer
        ]
        wrong = [made.answer[0], made.answer[1], others[0]]
        with pytest.raises(ValueError, match="does not satisfy the rule"):
            assemble(candidate(9, answer=wrong), rng())

    def test_a_tile_with_no_glyph_is_refused(self):
        made = plan()
        payload = payload_for(made)
        payload["tiles"][0] = {**payload["tiles"][0], "glyph": ""}
        broken = PuzzleCandidate(
            candidate_id="x", payload=payload, fact_refs=made.dependency_ids()
        )
        with pytest.raises(ValueError, match="no glyph"):
            assemble(broken, rng())

    def test_a_tile_with_no_label_is_refused(self):
        made = plan()
        payload = payload_for(made)
        payload["tiles"][0] = {**payload["tiles"][0], "label": ""}
        broken = PuzzleCandidate(
            candidate_id="x", payload=payload, fact_refs=made.dependency_ids()
        )
        with pytest.raises(ValueError, match="no label"):
            assemble(broken, rng())

    def test_a_tile_missing_an_attribute_is_refused(self):
        made = plan()
        payload = payload_for(made)
        attributes = dict(payload["tiles"][0]["attributes"])
        attributes.pop("fill")
        payload["tiles"][0] = {**payload["tiles"][0], "attributes": attributes}
        broken = PuzzleCandidate(
            candidate_id="x", payload=payload, fact_refs=made.dependency_ids()
        )
        with pytest.raises(ValueError, match="fill"):
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

    def test_assembly_narrows_nothing_and_widens_nothing(self):
        """The engine refuses a puzzle depending on records its candidate did
        not claim, so assembly passes the candidate's own references."""
        made = plan()
        built = candidate()
        puzzle = assemble(built, rng())
        assert set(puzzle.fact_refs) == set(built.fact_refs)
