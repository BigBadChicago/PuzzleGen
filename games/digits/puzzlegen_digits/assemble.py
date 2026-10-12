"""Turning one candidate into a board.

The payload/solution split is the sharpest in the repository, because the
answer is a short sequence of digits and the board is full of digit sequences.
A clue row is a code; so is the answer. The rules that keep them apart are
therefore specific rather than general:

* no clue may be the code, which means no clue may report every digit exact;
* the code may not appear as any clue's guess, whatever that clue's verdict,
  because a client could show the clue and a reader would recognise it;
* nothing in the payload may carry the code under any key.

The first two are checked here and the third is what ``leaks_solution``
checks. The clue order is drawn, because the construction adds the most
informative clues first and a board in that order would be a board whose first
row is always the one worth reading.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from puzzlegen.core.rng import DeterministicRng
from puzzlegen.engine.plugin import Puzzle, PuzzleCandidate

from .descriptor import GAME_ID, MAX_CLUES
from .lock import CodeError, DigitFacts, GuessClue, PropertyClue, check_code, verdict

#: Substream that decides the clue order.
CLUE_STREAM = "clues"


def _require(payload: Mapping[str, Any], key: str) -> Any:
    try:
        return payload[key]
    except KeyError as exc:
        raise ValueError(f"candidate payload is missing {key!r}") from exc


def clue_order(
    clues: Sequence[Mapping[str, Any]], rng: DeterministicRng
) -> list[Mapping[str, Any]]:
    """Shuffle the clue rows.

    The construction adds clues in the order it found them informative, so a
    board laid out that way would put the clue that halves the space first,
    every day. Drawn from the assembly RNG rather than a fresh one, so the
    same candidate on the same day lays out the same way every time it is
    regenerated, which the engine's reproducibility check compares against.
    """
    return rng.derive(CLUE_STREAM).shuffled(list(clues))


def assemble(candidate: PuzzleCandidate, rng: DeterministicRng) -> Puzzle:
    """One candidate, one board.

    Validates what the candidate claims rather than trusting it. Generation
    and assembly are separated so each can be tested alone, and the price of
    that separation is that assembly checks the claim: a mismatch here is a
    bug in this game, and a bug that reaches the verifier costs a whole
    enumeration to discover.
    """
    payload = candidate.payload
    code_length = int(_require(payload, "code_length"))
    digits = [int(digit) for digit in _require(payload, "digits")]
    code = check_code(_require(payload, "code"), length=code_length, digits=digits)
    guesses = list(_require(payload, "guesses"))
    properties = list(_require(payload, "properties"))
    glyphs: Mapping[str, str] = dict(_require(payload, "glyphs"))
    labels: Mapping[str, str] = dict(_require(payload, "labels"))

    if not guesses and not properties:
        raise ValueError("a board with no clues states nothing to deduce from")
    if len(guesses) > MAX_CLUES:
        raise ValueError(
            f"a board shows at most {MAX_CLUES} guess clues, got {len(guesses)}"
        )
    for digit in digits:
        if not glyphs.get(str(digit)):
            raise ValueError(f"digit {digit} has no glyph to draw")
        if not labels.get(str(digit)):
            raise ValueError(f"digit {digit} has no label to announce")

    for row in guesses:
        guess = check_code(row["guess"], length=code_length, digits=digits)
        clue = GuessClue(
            guess=guess, exact=int(row["exact"]), misplaced=int(row["misplaced"])
        )
        if verdict(guess, code) != (clue.exact, clue.misplaced):
            raise ValueError(
                f"the clue {list(guess)} claims {clue.exact} exact and "
                f"{clue.misplaced} misplaced, which is not true of the code"
            )
        if clue.solves():
            raise ValueError(
                "a clue reporting every digit exact is the answer, which a "
                "board may never show"
            )
        if guess == code:
            # Unreachable given the check above, and checked anyway: a guess
            # equal to the code with a doctored verdict would pass the first
            # test only if the verdict were also wrong, and this is the rule
            # being stated rather than its consequence.
            raise ValueError("a clue's guess may not be the code itself")

    ordered = clue_order(guesses, rng)

    board_payload: dict[str, Any] = {
        "game_id": GAME_ID,
        "code_length": code_length,
        # Every digit the board may name, so the solver can rule each in or
        # out, with the glyph a client draws and the word a reader hears.
        "digits": sorted(digits),
        "glyphs": {str(digit): glyphs[str(digit)] for digit in sorted(digits)},
        "labels": {str(digit): labels[str(digit)] for digit in sorted(digits)},
        "clues": [
            {
                "kind": "guess",
                "guess": list(row["guess"]),
                "exact": int(row["exact"]),
                "misplaced": int(row["misplaced"]),
            }
            for row in ordered
        ],
        # Stated facts as sentences as well as values, because the wording is
        # part of the clue: a client inventing its own phrasing would be
        # inventing the puzzle's statement.
        "facts": [
            {
                "kind": row["kind"],
                "value": int(row["value"]),
                "text": row.get("text")
                or PropertyClue(kind=row["kind"], value=int(row["value"])).sentence(),
            }
            for row in properties
        ],
        "clue_count": len(ordered),
        "fact_count": len(properties),
    }

    digit_facts: Mapping[str, Mapping[str, Any]] = dict(
        _require(payload, "digit_facts")
    )
    missing = sorted(digit for digit in digits if str(digit) not in digit_facts)
    if missing:
        raise ValueError(
            f"the content supplied no facts for digits {missing}, so a stated "
            "fact about the code cannot be judged"
        )

    solution: dict[str, Any] = {
        "code": list(code),
        # The graph's facts, carried so the verifier evaluates a property
        # clue against the same facts the clue was made from rather than
        # against arithmetic of its own. Solution side rather than payload
        # because the client needs none of it: which digits are prime is
        # public knowledge, not a clue.
        "digit_facts": {
            str(digit): {
                "value": int(digit_facts[str(digit)]["value"]),
                "prime": bool(digit_facts[str(digit)]["prime"]),
                "even": bool(digit_facts[str(digit)]["even"]),
            }
            for digit in sorted(digits)
        },
        # The search the generator did, kept so the difficulty model reads
        # measured numbers rather than recomputing an elimination.
        "space_size": int(payload.get("space_size", 0)),
        "after_properties": int(payload.get("after_properties", 0)),
        "before_last": int(payload.get("before_last", 0)),
        "clue_count": len(ordered),
        "fact_count": len(properties),
    }

    presentation: dict[str, Any] = {
        "layout": "grid",
        "columns": code_length,
        "rows": len(ordered),
        # One submission, if the solver deduces it. Declared so the engine can
        # measure efficiency against something real.
        "optimal_attempts": 1,
        "instructions": (
            f"Work out the {code_length} digit code. No digit repeats. Each "
            "clue row shows a code someone tried, how many digits were right "
            "and in the right place, and how many were right in the wrong "
            "place."
        ),
    }

    return Puzzle(
        game_id=GAME_ID,
        payload=board_payload,
        solution=solution,
        fact_refs=candidate.fact_refs,
        presentation=presentation,
        candidate_id=candidate.candidate_id,
    )


def board_digits(puzzle: Puzzle) -> tuple[int, ...]:
    """Every digit the board may name."""
    return tuple(int(digit) for digit in puzzle.payload["digits"])


def board_guesses(puzzle: Puzzle) -> tuple[GuessClue, ...]:
    """The guess clues as the player sees them, read off the payload."""
    return tuple(
        GuessClue(
            guess=tuple(int(digit) for digit in row["guess"]),
            exact=int(row["exact"]),
            misplaced=int(row["misplaced"]),
        )
        for row in puzzle.payload["clues"]
        if row.get("kind") == "guess"
    )


def board_facts(puzzle: Puzzle) -> tuple[PropertyClue, ...]:
    """The stated facts as the player sees them, read off the payload."""
    return tuple(
        PropertyClue(kind=row["kind"], value=int(row["value"]))
        for row in puzzle.payload.get("facts", ())
    )


def answer_code(puzzle: Puzzle) -> tuple[int, ...]:
    return tuple(int(digit) for digit in puzzle.solution["code"])


def _strings(value: Any) -> list[str]:
    """Every string anywhere inside a payload, numbers included as text."""
    if isinstance(value, bool):
        return [str(value)]
    if isinstance(value, (str, int, float)):
        return [str(value)]
    if isinstance(value, Mapping):
        found: list[str] = []
        for key, item in value.items():
            found.append(str(key))
            found.extend(_strings(item))
        return found
    if isinstance(value, (list, tuple)):
        found = []
        for item in value:
            found.extend(_strings(item))
        return found
    return []


def leaks_solution(puzzle: Puzzle) -> tuple[str, ...]:
    """Anything in the payload that answers the puzzle.

    Two checks, and the second is specific to this game. A key by an answer's
    name, which is the ordinary one. And the code appearing as a *sequence*
    anywhere in the payload: every digit of the code is legitimately in the
    payload many times over, as a digit the board may name and as a digit of
    some clue, so the thing that cannot appear is the order. Checked against
    every clue row and every list of digits rather than against the flattened
    strings, because the code's digits scattered across a row are not a leak
    and the same digits in order are.
    """
    leaks: list[str] = []
    code = answer_code(puzzle)
    for key in ("code", "answer", "solution", "secret"):
        if key in puzzle.payload:
            leaks.append(f"payload carries {key!r}")
    for row in puzzle.payload.get("clues", ()):
        if tuple(int(digit) for digit in row.get("guess", ())) == code:
            leaks.append("a clue's guess is the code itself")
    for key, value in puzzle.payload.items():
        if key in ("clues", "facts"):
            continue
        if isinstance(value, (list, tuple)) and tuple(value) == code:
            leaks.append(f"payload key {key!r} is the code in order")
    return tuple(leaks)


__all__ = [
    "CLUE_STREAM",
    "answer_code",
    "assemble",
    "board_digits",
    "board_facts",
    "board_guesses",
    "clue_order",
    "leaks_solution",
]
