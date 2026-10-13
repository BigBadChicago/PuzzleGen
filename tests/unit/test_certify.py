"""The engine's own check on a board, and what it refuses.

The boards here are hand built, and the broken ones are built broken on
purpose. What is under test is not that a correct board certifies -- the
differential run over thirty real days per game does that, in CI -- but that
each way a board can be wrong produces a refusal with a reason, and that the
refusal is reached without the game's verifier being consulted at all.
"""

from __future__ import annotations

import pytest

from puzzlegen.engine.certify import (
    AssuranceLevel,
    CertificateResult,
    CertifierRegistry,
    Disagreement,
    default_registry,
    differential,
)
from puzzlegen.engine.certifiers import digit_lock, odd_number_out, shape_trio


class TestAssurance:
    def test_the_levels_are_ordered(self):
        assert AssuranceLevel.UNIQUENESS_PROVED.at_least(
            AssuranceLevel.SOLUTION_COUNT_KNOWN
        )
        assert AssuranceLevel.SOLUTION_COUNT_KNOWN.at_least(
            AssuranceLevel.ANSWER_VALID
        )
        assert not AssuranceLevel.ANSWER_VALID.at_least(
            AssuranceLevel.UNIQUENESS_PROVED
        )

    def test_a_level_is_at_least_itself(self):
        for level in AssuranceLevel:
            assert level.at_least(level)


class TestTheRegistry:
    def test_an_unregistered_game_certifies_to_none_not_to_acceptance(self):
        """An unexamined board has not passed.

        The whole module is worthless if a missing certifier reads as a pass.
        """
        registry = CertifierRegistry()
        assert registry.certify("nobody", {}, {}) is None
        assert registry.get("nobody") is None
        assert not registry.has("nobody")

    def test_a_second_certifier_for_one_game_is_refused(self):
        registry = CertifierRegistry()
        registry.register(digit_lock.certifier())
        with pytest.raises(ValueError, match="already registered"):
            registry.register(digit_lock.certifier())

    def test_the_default_registry_ships_the_three_symbolic_games(self):
        assert default_registry().game_ids() == (
            "digit_lock",
            "odd_number_out",
            "shape_trio",
        )

    def test_every_shipped_certifier_proves_uniqueness(self):
        """All three spaces are enumerable, so none settles for less."""
        registry = default_registry()
        for game_id in registry.game_ids():
            assert (
                registry.get(game_id).assurance
                is AssuranceLevel.UNIQUENESS_PROVED
            )


# -- digit_lock ------------------------------------------------------------
#
# A three-digit board over 0-4: sixty codes, so every count below is a figure
# this test could compute by hand.

DIGITS = [0, 1, 2, 3, 4]
FACTS = {
    "0": {"value": 0, "prime": False, "even": True},
    "1": {"value": 1, "prime": False, "even": False},
    "2": {"value": 2, "prime": True, "even": True},
    "3": {"value": 3, "prime": True, "even": False},
    "4": {"value": 4, "prime": False, "even": True},
}


def digit_board(code=(1, 2, 3), clues=(), facts=()):
    payload = {
        "game_id": "digit_lock",
        "code_length": len(code),
        "digits": DIGITS,
        "clues": [
            {
                "kind": "guess",
                "guess": list(guess),
                "exact": exact,
                "misplaced": misplaced,
            }
            for guess, exact, misplaced in clues
        ],
        "facts": [{"kind": kind, "value": value} for kind, value in facts],
    }
    solution = {"code": list(code), "digit_facts": FACTS}
    return payload, solution


def narrowing_clues(code=(1, 2, 3)):
    """Clues that leave exactly ``code`` standing, found by enumeration."""
    from itertools import permutations

    space = list(permutations(DIGITS, len(code)))
    chosen: list[tuple[tuple[int, ...], int, int]] = []
    survivors = space
    for guess in space:
        if guess == tuple(code):
            continue
        exact, misplaced = digit_lock._verdict(guess, code)
        candidate = (*chosen, (guess, exact, misplaced))
        kept = [
            option
            for option in space
            if all(
                digit_lock._verdict(g, option) == (e, m) for g, e, m in candidate
            )
        ]
        if len(kept) < len(survivors):
            chosen, survivors = list(candidate), kept
        if len(survivors) == 1:
            break
    assert survivors == [tuple(code)], survivors
    return tuple(chosen)


class TestDigitLock:
    def certify(self, payload, solution) -> CertificateResult:
        return digit_lock.certifier().certify(payload, solution)

    def test_a_board_with_one_surviving_code_is_certified(self):
        payload, solution = digit_board(clues=narrowing_clues())
        result = self.certify(payload, solution)
        assert result.accepted, result.reasons
        assert result.solution_count == 1
        assert result.states_examined == 60

    def test_a_fabricated_answer_is_refused(self):
        """The case the whole item exists for.

        The board is sound and the stated answer is not the code the clues
        describe. A verifier that trusted the solution record would publish
        it.
        """
        payload, solution = digit_board(clues=narrowing_clues())
        solution["code"] = [4, 3, 0]
        result = self.certify(payload, solution)
        assert result.rejected
        assert result.solution_count == 1
        assert "not the stated answer" in result.describe()

    def test_a_board_with_no_surviving_code_is_refused(self):
        """Two clues about the same guess that cannot both hold."""
        payload, solution = digit_board(
            clues=(((0, 1, 2), 2, 0), ((0, 1, 2), 0, 0))
        )
        result = self.certify(payload, solution)
        assert result.rejected
        assert result.solution_count == 0
        assert "unsolvable" in result.describe()

    def test_an_underdetermined_board_is_refused_with_its_count(self):
        payload, solution = digit_board(clues=(((0, 4, 2), 0, 1),))
        result = self.certify(payload, solution)
        assert result.rejected
        assert result.solution_count > 1
        assert "not unique" in result.describe()

    def test_a_clue_that_is_the_answer_is_refused(self):
        payload, solution = digit_board(clues=(((1, 2, 3), 3, 0),))
        result = self.certify(payload, solution)
        assert result.rejected
        assert any("prints its own solution" in r for r in result.reasons)

    def test_a_property_clue_is_read_from_the_graphs_facts(self):
        """Not from arithmetic performed here.

        The facts say 4 is not prime. A certifier computing primality itself
        would reach the same answer; one computing it for a build whose
        content disagreed would judge the board against something the player
        was never shown.
        """
        payload, solution = digit_board(
            code=(2, 3, 4), clues=narrowing_clues((2, 3, 4)),
            facts=(("primes", 2),),
        )
        result = self.certify(payload, solution)
        assert result.accepted, result.reasons

    def test_a_false_property_clue_makes_the_board_unsolvable(self):
        payload, solution = digit_board(
            code=(2, 3, 4),
            clues=narrowing_clues((2, 3, 4)),
            facts=(("primes", 3),),
        )
        result = self.certify(payload, solution)
        assert result.rejected
        assert result.solution_count == 0

    def test_a_fact_of_an_unknown_kind_is_a_finding_not_a_skip(self):
        """The game's verifier admits every code for an unknown kind, which is
        right for serving a board and wrong for a second opinion."""
        payload, solution = digit_board(
            clues=narrowing_clues(), facts=(("nonsense", 1),)
        )
        result = self.certify(payload, solution)
        assert result.rejected
        assert any("one fewer constraint" in r for r in result.reasons)
        # Nothing was counted, so nothing is claimed about the count.
        assert result.solution_count is None

    def test_an_answer_repeating_a_digit_is_refused(self):
        payload, solution = digit_board(code=(1, 1, 2))
        result = self.certify(payload, solution)
        assert result.rejected
        assert any("repeats a digit" in r for r in result.reasons)

    def test_an_answer_using_an_offered_digit_the_board_lacks_is_refused(self):
        payload, solution = digit_board(code=(1, 2, 9))
        result = self.certify(payload, solution)
        assert result.rejected
        assert any("does not offer" in r for r in result.reasons)

    def test_a_malformed_board_is_refused_rather_than_crashing(self):
        result = self.certify({"code_length": 3}, {})
        assert result.rejected
        assert result.solution_count is None
        assert "not readable" in result.reasons[0]

    def test_the_verdict_counts_are_disjoint(self):
        """A digit right and in place is not counted again as misplaced."""
        assert digit_lock._verdict((1, 2, 3), (1, 3, 2)) == (1, 2)
        assert digit_lock._verdict((1, 2, 3), (1, 2, 3)) == (3, 0)
        assert digit_lock._verdict((0, 4, 1), (1, 2, 3)) == (0, 1)


# -- odd_number_out --------------------------------------------------------


def numbers_board(answer="t4", memberships=None, prop="even"):
    memberships = memberships or {
        "t1": ["even", "small"],
        "t2": ["even", "small"],
        "t3": ["even", "small"],
        "t4": ["small"],
    }
    payload = {
        "game_id": "odd_number_out",
        "board_size": len(memberships),
        "tiles": [
            {"id": tile, "glyph": tile, "label": tile, "value": index}
            for index, tile in enumerate(sorted(memberships))
        ],
    }
    solution = {
        "answer": answer,
        "property": prop,
        "memberships": memberships,
    }
    return payload, solution


class TestOddNumberOut:
    def certify(self, payload, solution) -> CertificateResult:
        return odd_number_out.certifier().certify(payload, solution)

    def test_a_board_with_one_distinguished_tile_is_certified(self):
        result = self.certify(*numbers_board())
        assert result.accepted, result.reasons
        assert result.solution_count == 1
        assert result.states_examined == 4

    def test_a_fabricated_answer_is_refused(self):
        result = self.certify(*numbers_board(answer="t1"))
        assert result.rejected
        assert "not the stated" in result.describe()

    def test_a_board_where_two_tiles_could_be_the_answer_is_refused(self):
        payload, solution = numbers_board(
            memberships={
                "t1": ["even", "small"],
                "t2": ["even", "small"],
                "t3": ["even"],
                "t4": ["small"],
            }
        )
        result = self.certify(payload, solution)
        assert result.rejected
        assert result.solution_count == 2
        assert "not unique" in result.describe()

    def test_a_board_where_nothing_is_distinguished_is_refused(self):
        payload, solution = numbers_board(
            memberships={tile: ["even"] for tile in ("t1", "t2", "t3", "t4")}
        )
        result = self.certify(payload, solution)
        assert result.rejected
        assert result.solution_count == 0
        assert "no odd one out" in result.describe()

    def test_a_stated_property_that_is_not_a_reason_is_a_finding(self):
        """The answer is right and the board is playable; the record is wrong,
        and a reviewer should see that rather than have it absorbed."""
        payload, solution = numbers_board(prop="enormous")
        result = self.certify(payload, solution)
        assert result.rejected
        assert any("is not one the other tiles share" in r for r in result.reasons)
        assert result.solution_count == 1

    def test_an_answer_not_on_the_board_is_refused(self):
        result = self.certify(*numbers_board(answer="t9"))
        assert result.rejected
        assert any("not a tile on the board" in r for r in result.reasons)

    def test_a_two_tile_board_is_refused(self):
        """Two tiles share everything or nothing; neither is a puzzle."""
        payload, solution = numbers_board(
            answer="t2", memberships={"t1": ["even"], "t2": []}
        )
        result = self.certify(payload, solution)
        assert result.rejected
        assert any("cannot have an odd one out" in r for r in result.reasons)

    def test_a_tile_with_no_recorded_properties_is_refused(self):
        payload, solution = numbers_board()
        del solution["memberships"]["t1"]
        result = self.certify(payload, solution)
        assert result.rejected
        assert any("no recorded properties" in r for r in result.reasons)

    def test_a_malformed_board_is_refused_rather_than_crashing(self):
        result = self.certify({"tiles": "not a list"}, {})
        assert result.rejected
        assert "not readable" in result.reasons[0]


# -- shape_trio ------------------------------------------------------------

ATTRIBUTES = ["shape", "fill", "count", "colour"]


def trio_board(tiles, trio):
    payload = {
        "game_id": "shape_trio",
        "board_size": len(tiles),
        "columns": 3,
        "rows": len(tiles) // 3,
        "attributes": list(ATTRIBUTES),
        "trio_size": 3,
        "tiles": [
            {"id": tile_id, "glyph": tile_id, "label": tile_id, **readings}
            for tile_id, readings in tiles.items()
        ],
    }
    return payload, {"trio": sorted(trio)}


def reading(shape, fill, count, colour):
    return {"shape": shape, "fill": fill, "count": count, "colour": colour}


#: Four tiles. a/b/c are all-different on shape and all-same on the rest, so
#: they are a trio; d shares nothing with any pair, so there is exactly one.
FOUR_TILES = {
    "a": reading("circle", "solid", 1, "red"),
    "b": reading("square", "solid", 1, "red"),
    "c": reading("triangle", "solid", 1, "red"),
    "d": reading("circle", "open", 2, "blue"),
}


class TestShapeTrio:
    def certify(self, payload, solution) -> CertificateResult:
        return shape_trio.certifier().certify(payload, solution)

    def test_a_board_with_one_trio_is_certified(self):
        result = self.certify(*trio_board(FOUR_TILES, ["a", "b", "c"]))
        assert result.accepted, result.reasons
        assert result.solution_count == 1
        # Four tiles taken three at a time.
        assert result.states_examined == 4

    def test_a_fabricated_answer_is_refused(self):
        result = self.certify(*trio_board(FOUR_TILES, ["a", "b", "d"]))
        assert result.rejected
        assert result.solution_count == 1
        assert "not the stated" in result.describe()

    def test_the_rule_holds_on_every_attribute_with_none_exempt(self):
        """Two the same and one different on any one attribute is not a trio."""
        tiles = dict(FOUR_TILES)
        tiles["c"] = reading("triangle", "solid", 1, "blue")
        result = self.certify(*trio_board(tiles, ["a", "b", "c"]))
        assert result.rejected
        assert result.solution_count == 0
        assert "unsolvable" in result.describe()

    def test_all_different_on_every_attribute_is_a_trio(self):
        tiles = {
            "a": reading("circle", "solid", 1, "red"),
            "b": reading("square", "open", 2, "green"),
            "c": reading("triangle", "striped", 3, "blue"),
            "d": reading("circle", "solid", 2, "red"),
        }
        result = self.certify(*trio_board(tiles, ["a", "b", "c"]))
        assert result.accepted, result.reasons

    def test_a_board_with_two_trios_is_refused(self):
        tiles = {
            "a": reading("circle", "solid", 1, "red"),
            "b": reading("square", "solid", 1, "red"),
            "c": reading("triangle", "solid", 1, "red"),
            "d": reading("circle", "open", 1, "red"),
            "e": reading("square", "open", 1, "red"),
            "f": reading("triangle", "open", 1, "red"),
        }
        result = self.certify(*trio_board(tiles, ["a", "b", "c"]))
        assert result.rejected
        assert result.solution_count >= 2
        assert "not unique" in result.describe()

    def test_a_board_naming_no_attributes_is_refused(self):
        """Otherwise every three tiles are trivially a trio."""
        payload, solution = trio_board(FOUR_TILES, ["a", "b", "c"])
        payload["attributes"] = []
        result = self.certify(payload, solution)
        assert result.rejected
        assert any("no attributes" in r for r in result.reasons)

    def test_a_tile_missing_an_attribute_is_refused(self):
        payload, solution = trio_board(FOUR_TILES, ["a", "b", "c"])
        del payload["tiles"][0]["colour"]
        result = self.certify(payload, solution)
        assert result.rejected
        assert any("every named attribute" in r for r in result.reasons)

    def test_an_answer_naming_a_tile_off_the_board_is_refused(self):
        result = self.certify(*trio_board(FOUR_TILES, ["a", "b", "z"]))
        assert result.rejected
        assert any("not on the board" in r for r in result.reasons)

    def test_an_answer_of_the_wrong_size_is_refused(self):
        result = self.certify(*trio_board(FOUR_TILES, ["a", "b"]))
        assert result.rejected
        assert any("distinct tiles" in r for r in result.reasons)

    def test_the_answer_is_a_set_not_an_order(self):
        """A trio is three tiles; the sequence a player clicked is not part of
        the answer."""
        payload, _ = trio_board(FOUR_TILES, ["a", "b", "c"])
        for order in (["a", "b", "c"], ["c", "a", "b"], ["b", "c", "a"]):
            assert self.certify(payload, {"trio": order}).accepted

    def test_a_malformed_board_is_refused_rather_than_crashing(self):
        result = self.certify({"attributes": ["shape"]}, {})
        assert result.rejected
        assert "not readable" in result.reasons[0]


class TestTheDifferential:
    """What the CI run over thirty days per game actually compares."""

    class Outcome:
        def __init__(self, puzzle, succeeded, count):
            self.puzzle = puzzle
            self.succeeded = succeeded
            self.evaluation = type(
                "Evaluation",
                (),
                {"verification": type("V", (), {"solution_count": count})()},
            )()
            self.trace = type("Trace", (), {"day_key": "2026-10-01"})()

    class Puzzle:
        def __init__(self, game_id, payload, solution):
            self.game_id = game_id
            self.payload = payload
            self.solution = solution

    def outcome(self, *, trio, succeeded=True, count=1):
        payload, solution = trio_board(FOUR_TILES, trio)
        return self.Outcome(
            self.Puzzle("shape_trio", payload, solution), succeeded, count
        )

    def test_agreement_reports_nothing(self):
        assert differential(self.outcome(trio=["a", "b", "c"]), default_registry()) is None

    def test_a_certifier_refusing_what_generation_accepted_is_reported(self):
        found = differential(
            self.outcome(trio=["a", "b", "d"]), default_registry()
        )
        assert isinstance(found, Disagreement)
        assert found.verifier_accepted and not found.certifier_accepted
        assert found.day_key == "2026-10-01"
        assert "refused" in found.describe()

    def test_equal_verdicts_with_unequal_counts_are_reported(self):
        """Two checks agreeing by luck is the case this exists to catch."""
        found = differential(
            self.outcome(trio=["a", "b", "c"], count=2), default_registry()
        )
        assert found is not None
        assert found.certifier_count == 1
        assert found.verifier_count == 2

    def test_a_game_with_no_certifier_reports_nothing(self):
        payload, solution = trio_board(FOUR_TILES, ["a", "b", "c"])
        outcome = self.Outcome(
            self.Puzzle("unknown_game", payload, solution), True, 1
        )
        assert differential(outcome, default_registry()) is None

    def test_a_failed_run_is_not_a_disagreement(self):
        """The certifier was never offered a board."""
        outcome = self.Outcome(None, False, None)
        assert differential(outcome, default_registry()) is None
