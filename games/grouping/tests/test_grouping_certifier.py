"""The engine's certifier against this game's own verifier.

The certifier in ``puzzlegen.engine.certifiers.grouping`` reads these rules
independently and reaches the count a different way: the verifier walks tiles
into groups under a validity predicate, the certifier covers the board from
the categories inward. Two readings of one rule set are only worth having if
something compares them, and this is where they are compared -- the engine's
own suite cannot do it, because it may not import this game.

The comparison is on verdict **and** count. Two checks agreeing that a board
is acceptable while disagreeing about how many solutions it has is the exact
shape of a shared mistaken reading, and a test that compared only the verdict
would pass through it.

Boards are reused from ``test_grouping_verify`` deliberately. They are the
shapes that suite built to pin the verifier's own behaviour -- clean,
ambiguous, indistinguishable -- so a disagreement here lands on a board whose
intended reading is already documented.
"""

from __future__ import annotations

import pytest

from puzzlegen.core.types import UniquenessContract, VerificationCompleteness
from puzzlegen.engine.certify import AssuranceLevel, default_registry
from puzzlegen_grouping.descriptor import DESCRIPTOR, GAME_ID, VISIBLE_GROUPS
from puzzlegen_grouping.verify import verify

from .test_grouping_verify import (
    ambiguous_board,
    clean_board,
    make_puzzle,
)


def certify(puzzle):
    return default_registry().certify(GAME_ID, puzzle.payload, puzzle.solution)


def indistinguishable_board(size: int = 5):
    """Two tiles in one group carrying exactly the same categories.

    The board the tolerance rule exists for, and the one that would make the
    two checks disagree on every real day if the certifier did not collapse:
    real tiles repeat their signatures constantly.
    """
    groups = [[f"g{i}_{n}" for n in range(size)] for i in range(VISIBLE_GROUPS)]
    memberships = {
        tile: [f"category:c{index}", "category:root"]
        for index, members in enumerate(groups)
        for tile in members
    }
    return make_puzzle(groups, memberships)


def nested_label_board(size: int = 5):
    """Two groups whose only labels nest, so the partition is one group split.

    Group 1's members carry nothing of their own beyond a category that group
    0's members also carry, so no pair of labels is incomparable.
    """
    groups = [[f"g{i}_{n}" for n in range(size)] for i in range(VISIBLE_GROUPS)]
    memberships = {
        tile: [f"category:c{index}", "category:root"]
        for index, members in enumerate(groups)
        for tile in members
    }
    for tile in groups[0]:
        memberships[tile] += ["category:c1"]
    return make_puzzle(groups, memberships)


BOARDS = {
    "clean": clean_board,
    "ambiguous": ambiguous_board,
    "indistinguishable": indistinguishable_board,
    "nested labels": nested_label_board,
}


class TestTheCertifierAgreesWithTheVerifier:
    @pytest.mark.parametrize("name", sorted(BOARDS))
    def test_both_reach_the_same_verdict_and_the_same_count(self, name):
        puzzle = BOARDS[name]()
        verification = verify(puzzle)
        result = certify(puzzle)
        assert result is not None, "no certifier is registered for this game"

        # The verifier reports a count and leaves the contract to the engine;
        # the certifier applies the contract itself. Compared at the contract,
        # because that is the question publication turns on.
        verifier_accepts = (
            verification.solvable and verification.solution_count == 1
        )
        assert result.accepted == verifier_accepts, (
            f"{name}: certifier "
            f"{'accepted' if result.accepted else 'refused'}, verifier "
            f"{'accepted' if verifier_accepts else 'refused'} -- "
            f"{result.reasons}"
        )
        if result.solution_count is None:
            # The one asymmetry between the two checks, and it is deliberate.
            # The certifier validates the stated answer before searching for
            # rivals, so a board whose own answer breaks the rules is refused
            # without enumerating, and ``None`` means exactly that: no count
            # was taken. The verifier always enumerates and so reports 0.
            # Both are honest; what must not happen is a count being compared
            # against a number nobody computed.
            assert result.rejected
            assert result.states_examined == 0
            assert verification.solution_count == 0
        else:
            assert result.solution_count == verification.solution_count, (
                f"{name}: certifier counted {result.solution_count}, verifier "
                f"counted {verification.solution_count}; agreeing on the "
                "verdict while disagreeing on the count is the shape of a "
                "shared mistake"
            )

    def test_the_counts_are_actually_being_compared(self):
        """Guards the branch above against passing by never comparing.

        The count check has a branch for boards the certifier refuses before
        enumerating. If every board took it, the parametrized test would pass
        while comparing no counts at all, so at least one board has to reach
        a count -- and the ambiguous board is the one that matters, since its
        count is the thing the two searches could differ on.
        """
        counted = [
            name
            for name in sorted(BOARDS)
            if certify(BOARDS[name]()).solution_count is not None
        ]
        assert "ambiguous" in counted
        assert len(counted) >= 3

    @pytest.mark.parametrize("size", [5, 6, 7, 8, 9])
    def test_the_certifier_settles_every_board_size_the_game_serves(self, size):
        """All five sizes, because the cover search's cost is size driven.

        A certifier that silently ran out of budget on the widest boards would
        agree with the verifier on the narrow ones and claim nothing on the
        days hardest to generate, which is the least useful place to stop.
        """
        puzzle = clean_board(size)
        result = certify(puzzle)
        assert result.accepted
        assert result.solution_count == 1
        assert result.assurance is AssuranceLevel.SOLUTION_COUNT_KNOWN, (
            f"the {size}-tile-per-group board exhausted the cover budget, so "
            "the certifier fell back to validating the stated answer only"
        )

    @pytest.mark.parametrize("size", [5, 6, 7])
    def test_where_the_verifier_completes_the_two_counts_match(self, size):
        puzzle = clean_board(size)
        verification = verify(puzzle)
        assert verification.completeness is VerificationCompleteness.COMPLETE
        assert certify(puzzle).solution_count == verification.solution_count

    def test_the_certifier_settles_the_widest_boards_far_more_cheaply(self):
        """The finding that decides whether this certifier was worth writing.

        A 32-tile board costs the verifier's partition walk 2,881,169 states
        to exhaust. The cover search settles the same board in a handful,
        because forcing the lowest uncovered tile and building only groups
        inside a shared category's extension never enumerates the orderings
        the walk has to.

        Three orders of magnitude is not a tidier implementation of the same
        search, which is the point: a second opinion reached the same way
        would fail the same way. It is also why the bound had to be raised
        rather than the certifier relied on instead -- the engine's gate reads
        the game's own completeness, and a verifier that gives up is a day
        refused however cheaply something else settled it.
        """
        puzzle = clean_board(8)
        verification = verify(puzzle)
        assert verification.completeness is VerificationCompleteness.COMPLETE

        result = certify(puzzle)
        assert result.accepted
        assert result.assurance is AssuranceLevel.SOLUTION_COUNT_KNOWN
        assert result.solution_count == verification.solution_count
        assert result.states_examined < verification.states_examined / 1000

    def test_the_committed_bound_completes_the_widest_board_the_game_serves(
        self,
    ):
        """Re-derives the figure the committed bound was sized against.

        Opt in with ``PUZZLEGEN_WIDE_BOARD_TESTS=1``. Skipped for its cost,
        not its importance: exhausting the least prunable 36-tile board takes
        25,122,407 states and close to two minutes, and a test that adds two
        minutes to every run is a test somebody eventually deletes.

        What runs unconditionally is the descriptor test asserting the bound
        exceeds that figure. So the cheap test pins the constant to the
        measurement and this one checks the measurement is still true, which
        is the right split: the number can only go stale when the enumerator
        or the board shape changes, and both are changes whose author should
        be running this.
        """
        import os

        if os.environ.get("PUZZLEGEN_WIDE_BOARD_TESTS") != "1":
            pytest.skip("set PUZZLEGEN_WIDE_BOARD_TESTS=1 to exhaust a 36-tile board")

        verification = verify(clean_board(9))
        assert verification.completeness is VerificationCompleteness.COMPLETE
        assert verification.solution_count == 1
        assert verification.states_examined == 25_122_407
        assert certify(clean_board(9)).solution_count == 1

    def test_the_certifier_claims_no_more_than_the_game_promises(self):
        """The game declares uniqueness up to a tolerance, so a certifier
        claiming uniqueness proved would be claiming more than the contract.
        """
        assert DESCRIPTOR.uniqueness_contract is (
            UniquenessContract.UNIQUE_UP_TO_TOLERANCE
        )
        certifier = default_registry().get(GAME_ID)
        assert not certifier.assurance.at_least(
            AssuranceLevel.UNIQUENESS_PROVED
        )

    def test_the_certifier_does_not_read_the_hidden_group(self):
        """The overlay is a second reading, not a fifth answer.

        Counting it would make every board ambiguous by construction, so the
        certifier must reach the same verdict on a board whose hidden group
        has been replaced wholesale.
        """
        puzzle = clean_board()
        before = certify(puzzle)
        moved = dict(puzzle.solution)
        moved["hidden"] = {
            "category": "category:somethingelse",
            "category_id": "category:somethingelse",
            "secondary_category": None,
            "members": [],
        }
        after = default_registry().certify(GAME_ID, puzzle.payload, moved)
        assert (after.accepted, after.solution_count) == (
            before.accepted,
            before.solution_count,
        )
