"""Proving the clues entail exactly one code.

The second verifier in the repository able to promise COMPLETE without
qualification, and for the same reason as the first: the solution space is the
board's own rather than the language's. Ten digits taken three at a time is
720 codes and taken four at a time is 5,040, and every one of them is tested
against every clue. The count reported is the count.

What the contract promises is slightly stronger than one solution, and the
enumeration is what makes the difference checkable. ``UNIQUE_DEDUCTION`` says
the clues *entail* the code: a solver who applies every clue correctly is left
holding it, with nothing to choose between. A board with two survivors has one
intended answer and no argument for it, and a player who reasons to the end has
two codes and a coin. The enumeration counts survivors, so that board reports
two solutions and is refused, which is the right outcome and not an
approximation of it.

One thing deliberately not checked here: whether the clues are *minimal*. A
board with a redundant clue entails the same code and is a slightly easier
puzzle, not a broken one. The generator drops uninformative clues because the
difficulty model counts rows, which is a reason to keep boards tidy rather
than a soundness rule, so the verifier has no opinion about it.
"""

from __future__ import annotations

from collections.abc import Mapping

from puzzlegen.core.types import VerificationCompleteness
from puzzlegen.engine.plugin import Puzzle, VerificationResult

from .assemble import answer_code, board_digits, board_facts, board_guesses
from .descriptor import MAX_CLUES, SEARCH_BOUND
from .lock import CodeError, DigitFacts, check_code, space_for, survivors, verdict

#: Metric names. Constants because the difficulty model reads them by name,
#: and a typo in either place would be a silently absent number rather than an
#: error.
CODES_EXAMINED = "codes_examined"
SURVIVORS = "survivors"
CLUE_COUNT = "clue_count"
FACT_COUNT = "fact_count"
BEFORE_LAST = "before_last"
AFTER_FACTS = "after_facts"


def digit_facts(puzzle: Puzzle) -> dict[int, DigitFacts]:
    """The digit facts this board's clues are about, as the graph stated them.

    Read from the solution, where assembly put them, rather than recomputed.
    The verifier runs engine side and holds no content port, so if it computed
    primality itself a build whose digit taxonomy differed from textbook
    arithmetic would produce a board whose clue said one thing and whose
    verifier said another: solvable from the content and unsolvable from what
    the board shows, or the reverse. Carrying the facts makes the clue, the
    elimination and the enumeration one set of facts.
    """
    stated: Mapping[str, Mapping[str, object]] = puzzle.solution.get(
        "digit_facts", {}
    )
    found: dict[int, DigitFacts] = {}
    for digit in board_digits(puzzle):
        row = stated.get(str(digit))
        if row is None:
            continue
        found[digit] = DigitFacts(
            value=int(row["value"]),
            prime=bool(row["prime"]),
            even=bool(row["even"]),
        )
    return found


def board_problem(puzzle: Puzzle) -> str | None:
    """Anything that makes this board unjudgeable as shown.

    Each of these is a rule generation and assembly already apply, so a
    finding here is a bug in this game rather than a property of the content.
    They are checked anyway, because what each produces is a board that cannot
    be solved from what it shows.
    """
    payload = puzzle.payload
    length = int(payload["code_length"])
    digits = board_digits(puzzle)

    if len(set(digits)) != len(digits):
        return "the board lists a digit twice"
    if len(digits) < length:
        return f"a {length} digit code cannot be built from {len(digits)} digits"
    try:
        code = check_code(puzzle.solution.get("code", ()), length=length, digits=digits)
    except CodeError as exc:
        return f"the stated answer is not a legal code: {exc}"
    guesses = board_guesses(puzzle)
    if len(guesses) > MAX_CLUES:
        return f"the board shows {len(guesses)} guess clues, over the {MAX_CLUES} limit"
    if not guesses and not board_facts(puzzle):
        return "the board shows no clues, so nothing entails anything"
    facts = digit_facts(puzzle)
    unknown = sorted(digit for digit in digits if digit not in facts)
    if unknown:
        return (
            f"the board carries no facts for digits {unknown}, so a stated "
            "fact about the code cannot be judged"
        )
    for clue in guesses:
        try:
            check_code(clue.guess, length=length, digits=digits)
        except CodeError as exc:
            return f"a clue is not a legal code: {exc}"
        if verdict(clue.guess, code) != (clue.exact, clue.misplaced):
            return (
                f"the clue {list(clue.guess)} is not true of the stated code, "
                "so the board contradicts its own answer"
            )
        if clue.solves():
            return "a clue reports every digit exact, which is the answer shown"
    return None


def verify(puzzle: Puzzle, *, code_budget: int = SEARCH_BOUND) -> VerificationResult:
    """Every code the clues admit, counted by examining the whole space."""
    problem = board_problem(puzzle)
    if problem is not None:
        return VerificationResult(
            solvable=False,
            solution_count=0,
            completeness=VerificationCompleteness.COMPLETE,
            detail=problem,
        )

    length = int(puzzle.payload["code_length"])
    digits = board_digits(puzzle)
    facts = digit_facts(puzzle)
    space = space_for(length, digits)

    if len(space) > code_budget:
        # Not a truncated search: it is not started. A board this large is one
        # this game was not built for, and an honest report of an unfinished
        # enumeration is refused publication by the uniqueness gate.
        return VerificationResult(
            solvable=False,
            solution_count=0,
            completeness=VerificationCompleteness.SOUND_INCOMPLETE,
            states_examined=0,
            detail=(
                f"{len(digits)} digits taken {length} at a time is "
                f"{len(space)} codes, beyond the {code_budget} this game "
                "enumerates"
            ),
        )

    found = survivors(space, board_guesses(puzzle), board_facts(puzzle), facts)
    code = answer_code(puzzle)

    metrics = {
        CODES_EXAMINED: float(len(space)),
        SURVIVORS: float(len(found)),
        CLUE_COUNT: float(len(board_guesses(puzzle))),
        FACT_COUNT: float(len(board_facts(puzzle))),
        BEFORE_LAST: float(puzzle.solution.get("before_last", 0)),
        AFTER_FACTS: float(puzzle.solution.get("after_properties", 0)),
    }

    detail = ""
    if not found:
        detail = "no code is consistent with every clue on this board"
    elif code not in found:
        # Verifies, is unique, and marks the player wrong: the most dangerous
        # way for a board to be broken.
        detail = "the stated code is not among those the clues admit"
    elif len(found) > 1:
        detail = (
            f"{len(found)} codes are consistent with every clue, so the board "
            "does not entail its answer"
        )

    return VerificationResult(
        solvable=bool(found),
        solution_count=len(found),
        completeness=VerificationCompleteness.COMPLETE,
        states_examined=len(space),
        solutions=tuple(
            {**dict(puzzle.solution), "code": list(item)} for item in found[:8]
        ),
        solutions_truncated=len(found) > 8,
        metrics=metrics,
        detail=detail,
    )


def surviving_codes(puzzle: Puzzle) -> tuple[tuple[int, ...], ...]:
    """Every code the clues admit, for tests and diagnostics."""
    digits = board_digits(puzzle)
    length = int(puzzle.payload["code_length"])
    return tuple(
        survivors(
            space_for(length, digits),
            board_guesses(puzzle),
            board_facts(puzzle),
            digit_facts(puzzle),
        )
    )


__all__ = [
    "AFTER_FACTS",
    "BEFORE_LAST",
    "CLUE_COUNT",
    "CODES_EXAMINED",
    "FACT_COUNT",
    "SURVIVORS",
    "board_problem",
    "digit_facts",
    "surviving_codes",
    "verify",
]
