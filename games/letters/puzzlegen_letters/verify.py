"""Proving a board has one answer, or refusing to claim it has.

The engine never takes a solution count on trust: it compares the count
against the contract. What this module owes the engine is an honest count, and
honest here has a precise meaning. A solution is a word a player could defend,
which on this board means two things at once: every tile is one of those, and
the marks the board shows plus the blanks it withholds spell exactly that
word. A second such word is the answer a player will give and be marked wrong
for, which is the whole reason the contract is NO_ALTERNATE_INTERPRETATION
rather than a promise about the generator's intent.

Two rules carry the weight.

A reading must be true of every tile. The candidates are therefore the
categories the whole board belongs to, read off the tiles' own memberships,
which is an intersection rather than a vocabulary scan: one tile outside a
category is enough to make that reading wrong, however well its name fits the
letters.

And the vocabulary searched is recorded, because it is not English. Over the
categories the build holds, this enumeration is exhaustive and the result says
COMPLETE. Over the language it is not, which is what the descriptor's
SOUND_INCOMPLETE declares once, in advance, with the bound this module
reports against. A board whose vocabulary overran the bound reports
SOUND_INCOMPLETE and is refused publication, which is correct: the words it
did not examine are exactly where a second answer would hide.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from puzzlegen.core.types import VerificationCompleteness
from puzzlegen.engine.plugin import Puzzle, VerificationResult

from .assemble import board_marks, board_tiles, unmarked_tiles
from .descriptor import MINIMUM_BLANKS, SEARCH_BOUND
from .marks import letters_of, spells

#: Metric names. Constants because difficulty measurement reads them by name,
#: and a typo in either place would be a silently absent number rather than an
#: error.
ANSWER_CANDIDATES = "answer_candidates"
LETTER_ALTERNATIVES = "letter_alternatives"
VOCABULARY_SIZE = "vocabulary_size"
MARKED_TILES = "marked_tiles"
MANUFACTURED_BLANKS = "manufactured_blanks"


def memberships_of(puzzle: Puzzle) -> dict[str, frozenset[str]]:
    raw: Mapping[str, Sequence[str]] = puzzle.solution.get("memberships", {})
    return {tile: frozenset(names) for tile, names in raw.items()}


def readings_of(memberships: Mapping[str, frozenset[str]]) -> frozenset[str]:
    """Categories every tile belongs to: the only candidate answers."""
    if not memberships:
        return frozenset()
    shared: frozenset[str] | None = None
    for names in memberships.values():
        shared = names if shared is None else shared & names
    return shared or frozenset()


def board_problem(puzzle: Puzzle) -> str | None:
    """Anything that makes this board unsolvable as shown.

    Each of these is a rule the generator already applies, so a finding here
    is a bug in this game rather than a property of the content. They are
    checked anyway, because the failure each one produces is a board that
    cannot be solved from what it shows, and a verifier that assumed the
    generator was right would report it as a fine puzzle with one answer.
    """
    payload = puzzle.payload
    tiles = board_tiles(puzzle)
    marks = board_marks(puzzle)
    blanks = int(payload["blanks"])
    letters = str(puzzle.solution["letters"])

    if blanks < MINIMUM_BLANKS:
        return f"{blanks} blanks is below the floor of {MINIMUM_BLANKS}"
    if not marks:
        return "no tile carries a mark, so the board shows nothing to rearrange"
    if not unmarked_tiles(puzzle):
        return "every tile carries a mark, and a settled rule says one does not"
    if len(marks) + blanks != len(letters):
        return (
            f"{len(marks)} marks and {blanks} blanks cannot spell a "
            f"{len(letters)} letter answer"
        )
    if int(payload["answer_length"]) != len(letters):
        return "the board shows a different number of slots than the answer has"
    labels = {tile["id"]: tile["label"] for tile in payload["tiles"]}
    for tile_id, letter in marks.items():
        if letter not in letters_of(labels[tile_id]):
            return (
                f"the mark {letter!r} is not a letter of {labels[tile_id]!r}, so "
                "it is decoration rather than a clue"
            )
    shown = Counter(marks.values())
    available = Counter(letters)
    if any(available[letter] < count for letter, count in shown.items()):
        return "the marks include a letter the answer does not have"

    memberships = memberships_of(puzzle)
    missing = [tile for tile in tiles if tile not in memberships]
    if missing:
        return (
            f"{len(missing)} tiles carry no category memberships, so no reading "
            "of the board can be judged true or otherwise"
        )
    return None


def _as_solution(puzzle: Puzzle, word: str) -> dict:
    """One found reading, in the shape the engine compares against.

    The engine hashes the puzzle's whole solution and looks for that hash
    among the ones the verifier reports, which is how it catches a board whose
    stated answer its own solver cannot reach. That check only works if the
    intended answer is reported in exactly the intended shape, down to key
    order, so it is returned as the puzzle's own solution object rather than
    as an equivalent rebuilt here.
    """
    if word == puzzle.solution["answer"]:
        return dict(puzzle.solution)
    return {
        **dict(puzzle.solution),
        "category": word,
        "answer": word,
        "letters": letters_of(word),
    }


def verify(puzzle: Puzzle, *, word_budget: int = SEARCH_BOUND) -> VerificationResult:
    """Every reading of this board a player could defend, counted honestly."""
    problem = board_problem(puzzle)
    if problem is not None:
        return VerificationResult(
            solvable=False,
            solution_count=0,
            completeness=VerificationCompleteness.COMPLETE,
            detail=problem,
        )

    marks = board_marks(puzzle)
    blanks = int(puzzle.payload["blanks"])
    shown = Counter(marks.values())
    memberships = memberships_of(puzzle)
    readings = sorted(readings_of(memberships))
    vocabulary = sorted({str(word) for word in puzzle.solution.get("vocabulary", ())})

    examined = len(readings) + len(vocabulary)
    found = [word for word in readings if spells(word, shown, blanks)]
    # Words the letters admit that the board is not actually about. Not
    # answers, because they are untrue of at least one tile, and not nothing
    # either: they are what a solver's own anagram turns up, so they are the
    # measure of how much the letters alone give away.
    near_misses = [
        word
        for word in vocabulary
        if word not in readings and spells(word, shown, blanks)
    ]

    metrics = {
        ANSWER_CANDIDATES: float(len(readings)),
        LETTER_ALTERNATIVES: float(len(near_misses)),
        VOCABULARY_SIZE: float(len(vocabulary)),
        MARKED_TILES: float(len(marks)),
        MANUFACTURED_BLANKS: float(len(puzzle.solution.get("manufactured_blanks", ()))),
    }

    detail = ""
    completeness = VerificationCompleteness.COMPLETE
    if examined > word_budget:
        # A lower bound, and said so. The words beyond the budget are where a
        # second answer would hide, so the count is evidence of nothing on its
        # own and the uniqueness gate is right to refuse it.
        completeness = VerificationCompleteness.SOUND_INCOMPLETE
        detail = (
            f"stopped after {examined} words; {len(found)} answers found so far "
            "is a lower bound"
        )
    elif puzzle.solution["answer"] not in found:
        # Verifies, is unique, and marks the player wrong: the most dangerous
        # way for a board to be broken, and one scan catches it.
        detail = "the intended answer is not among the readings found"
    elif len(found) > 1:
        detail = "the board has more than one true reading: " + ", ".join(found)

    return VerificationResult(
        solvable=bool(found),
        solution_count=len(found),
        completeness=completeness,
        states_examined=examined,
        solutions=tuple(_as_solution(puzzle, word) for word in found[:8]),
        solutions_truncated=len(found) > 8,
        metrics=metrics,
        detail=detail,
    )


__all__ = [
    "ANSWER_CANDIDATES",
    "LETTER_ALTERNATIVES",
    "MANUFACTURED_BLANKS",
    "MARKED_TILES",
    "VOCABULARY_SIZE",
    "board_problem",
    "memberships_of",
    "readings_of",
    "verify",
]
