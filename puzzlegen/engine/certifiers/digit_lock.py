"""An independent check on a ``digit_lock`` board.

Written from the rules as the board states them, not from the game's code.
Everything here is deliberately a re-derivation: the code space, the
disjointness of the exact and misplaced counts, and the reading of a property
clue. ``puzzlegen_digits.lock`` already has all three, and importing it is the
one thing this module must not do -- a check that called the game's own
deduction would agree with it by construction.

The rules, stated once, from what the payload shows a player:

* A code is ``code_length`` digits drawn from ``digits``, with no repeat.
* A guess clue gives a code somebody tried, how many of its digits were right
  and in the right place, and how many were right in the wrong place. The two
  counts are disjoint: a digit counted exact is not counted again as
  misplaced.
* A property clue states a fact about the code -- what its digits add to, how
  many are prime, how many are even -- read from the digit facts the graph
  holds, not from arithmetic performed here. A board whose content disagreed
  with arithmetic would be wrong consistently, and the clue a player reads is
  the one the content supports.
* The board is solvable iff exactly one code in the space satisfies every
  clue, and correct iff that code is the stated answer.

Ten digits taken four at a time is 5,040 codes, so the check is exhaustive and
costs nothing. That is why this certifier can prove uniqueness rather than
only validate an answer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import permutations
from typing import Any

from ..certify import AssuranceLevel, CertificateResult, Certifier
from ..coupling import EnumeratedSpace, Fact

GAME_ID = "digit_lock"

#: The property kinds this certifier understands. A clue of an unknown kind is
#: a finding rather than a clue it silently ignores: the game's own verifier
#: treats an unknown kind as admitting every code, which is the right
#: behaviour for serving a board and the wrong behaviour for a second opinion
#: -- a certifier that skipped a clue would accept a board with one fewer
#: constraint than the player is shown.
PROPERTY_KINDS = ("sum", "primes", "evens")


def _readings(
    facts: Mapping[str, Mapping[str, Any]], code: Sequence[int]
) -> tuple[int, int, int]:
    """``(sum, primes, evens)`` for one code, read off the graph's facts."""
    total = primes = evens = 0
    for digit in code:
        fact = facts[str(digit)]
        total += int(fact["value"])
        primes += 1 if fact["prime"] else 0
        evens += 1 if fact["even"] else 0
    return total, primes, evens


def _verdict(guess: Sequence[int], code: Sequence[int]) -> tuple[int, int]:
    """``(exact, misplaced)``, counted disjointly.

    Written out rather than reduced to a set intersection, so the disjointness
    is visible in the arithmetic: a position that matched is removed from the
    pool before the misplaced count is taken.
    """
    exact = 0
    guess_rest: list[int] = []
    code_rest: list[int] = []
    for one, two in zip(guess, code):
        if one == two:
            exact += 1
        else:
            guess_rest.append(one)
            code_rest.append(two)
    remaining = list(code_rest)
    misplaced = 0
    for digit in guess_rest:
        if digit in remaining:
            remaining.remove(digit)
            misplaced += 1
    return exact, misplaced


class DigitLockCertifier:
    """Enumerates the whole code space and proves the answer unique."""

    game_id = GAME_ID
    assurance = AssuranceLevel.UNIQUENESS_PROVED

    def certify(
        self, payload: Mapping[str, Any], solution: Mapping[str, Any]
    ) -> CertificateResult:
        reasons: list[str] = []

        try:
            length = int(payload["code_length"])
            digits = sorted(int(digit) for digit in payload["digits"])
            clues = list(payload.get("clues", ()))
            facts = list(payload.get("facts", ()))
            answer = tuple(int(digit) for digit in solution["code"])
            digit_facts: Mapping[str, Mapping[str, Any]] = dict(
                solution["digit_facts"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            return self._refuse(
                (f"the board is not readable as a digit_lock board: {exc}",)
            )

        if length < 1:
            reasons.append(f"a code of {length} digits is not a code")
        if len(digits) < length:
            reasons.append(
                f"{len(digits)} digits cannot make a code of {length} without "
                "a repeat, and a code has no repeated digit"
            )
        if len(answer) != length:
            reasons.append(
                f"the stated answer is {len(answer)} digits, the board is {length}"
            )
        if len(set(answer)) != len(answer):
            reasons.append("the stated answer repeats a digit")
        if set(answer) - set(digits):
            reasons.append(
                "the stated answer uses digits the board does not offer: "
                f"{sorted(set(answer) - set(digits))}"
            )
        missing = [digit for digit in digits if str(digit) not in digit_facts]
        if missing:
            reasons.append(
                f"no graph facts for digits {missing}, so a stated fact about "
                "the code cannot be judged"
            )
        unknown = sorted(
            {str(fact.get("kind")) for fact in facts} - set(PROPERTY_KINDS)
        )
        if unknown:
            reasons.append(
                f"the board states facts of kinds this check does not know: "
                f"{unknown}; a skipped clue would mean certifying a board with "
                "one fewer constraint than the player is shown"
            )
        if reasons:
            return self._refuse(tuple(reasons))

        guesses: list[tuple[tuple[int, ...], int, int]] = []
        for clue in clues:
            if str(clue.get("kind", "guess")) != "guess":
                reasons.append(f"unknown clue kind {clue.get('kind')!r}")
                continue
            guess = tuple(int(digit) for digit in clue["guess"])
            if len(guess) != length:
                reasons.append(
                    f"a clue's guess is {len(guess)} digits, the board is {length}"
                )
                continue
            if guess == answer:
                reasons.append(
                    "a clue's guess is the answer itself, so the board prints "
                    "its own solution"
                )
            guesses.append((guess, int(clue["exact"]), int(clue["misplaced"])))
        if reasons:
            return self._refuse(tuple(reasons))

        stated = [(str(fact["kind"]), int(fact["value"])) for fact in facts]
        survivors: list[tuple[int, ...]] = []
        examined = 0
        for code in permutations(digits, length):
            examined += 1
            total, primes, evens = _readings(digit_facts, code)
            found = {"sum": total, "primes": primes, "evens": evens}
            if any(found[kind] != value for kind, value in stated):
                continue
            if any(
                _verdict(guess, code) != (exact, misplaced)
                for guess, exact, misplaced in guesses
            ):
                continue
            survivors.append(code)

        count = len(survivors)
        if count == 0:
            reasons.append("no code satisfies every clue; the board is unsolvable")
        elif count > 1:
            reasons.append(
                f"{count} codes satisfy every clue, so the answer is not unique"
            )
        elif survivors[0] != answer:
            reasons.append(
                "the one code satisfying every clue is not the stated answer"
            )
        if reasons:
            return CertificateResult(
                game_id=GAME_ID,
                accepted=False,
                assurance=self.assurance,
                solution_count=count,
                states_examined=examined,
                reasons=tuple(reasons),
            )
        return CertificateResult(
            game_id=GAME_ID,
            accepted=True,
            assurance=self.assurance,
            solution_count=count,
            states_examined=examined,
            detail=(
                f"{examined} codes enumerated, one survives every clue and it "
                "is the stated answer"
            ),
        )

    def _refuse(self, reasons: tuple[str, ...]) -> CertificateResult:
        """A refusal before any enumeration ran.

        ``solution_count`` stays ``None``: nothing was counted, and reporting
        zero would claim the board was searched and found unsolvable.
        """
        return CertificateResult(
            game_id=GAME_ID,
            accepted=False,
            assurance=self.assurance,
            solution_count=None,
            reasons=reasons,
        )


def certifier() -> Certifier:
    return DigitLockCertifier()


#: The vocabulary this board names its answers in.
#:
#: Not ``"entity"``, and that is the whole point of the field. A digit_lock
#: payload names digits as digits -- the board shows a glyph and a label per
#: digit and never the entity id behind it -- so there is no mapping from a
#: code to the graph entities the board drew from, and a leak measured against
#: a board that does name entities would come out as exactly zero. That zero
#: would be false, and a false zero is the failure a quantity exists to
#: prevent. So this space is comparable with another digit_lock board and with
#: nothing else, and every other pairing reports unknown.
#:
#: The fix, if a day ever wants to couple this game to another, is for the
#: payload or the record to carry the digit-to-entity mapping. That is a change
#: to what a board publishes, not to this measurement.
NAMESPACE = "digit_lock/digit"


def _admits(answer: tuple[int, ...], fact: Fact) -> bool:
    """Whether a candidate code is still possible given one fact.

    A fact names a digit and a position, or a digit and ``False`` for a digit
    the code does not use at all.
    """
    if fact.value is False:
        return int(fact.ref) not in answer
    position = int(fact.value)
    return 0 <= position < len(answer) and answer[position] == int(fact.ref)


def space(
    payload: Mapping[str, Any], solution: Mapping[str, Any] | None = None
) -> EnumeratedSpace | None:
    """Every code this board's clues admit.

    The same enumeration the certifier runs, which is the point: a space that
    counted the whole permutation set rather than the surviving codes would
    make every measurement against this board a measurement of its clues'
    strength rather than of another board's influence.
    """
    try:
        length = int(payload["code_length"])
        digits = sorted(int(digit) for digit in payload["digits"])
        clues = list(payload.get("clues", ()))
        facts = list(payload.get("facts", ()))
        digit_facts: Mapping[str, Mapping[str, Any]] = dict(
            (solution or {}).get("digit_facts", {})
        )
    except (KeyError, TypeError, ValueError):
        return None
    if length < 1 or len(digits) < length:
        return None
    if any(str(digit) not in digit_facts for digit in digits):
        return None
    if {str(fact.get("kind")) for fact in facts} - set(PROPERTY_KINDS):
        return None

    guesses = [
        (tuple(int(d) for d in clue["guess"]), int(clue["exact"]), int(clue["misplaced"]))
        for clue in clues
        if str(clue.get("kind", "guess")) == "guess"
    ]
    if any(len(guess) != length for guess, _, _ in guesses):
        return None
    stated = [(str(fact["kind"]), int(fact["value"])) for fact in facts]

    answers: list[tuple[int, ...]] = []
    for code in permutations(digits, length):
        total, primes, evens = _readings(digit_facts, code)
        found = {"sum": total, "primes": primes, "evens": evens}
        if any(found[kind] != value for kind, value in stated):
            continue
        if any(
            _verdict(guess, code) != (exact, misplaced)
            for guess, exact, misplaced in guesses
        ):
            continue
        answers.append(code)

    revealed: tuple[Fact, ...] = ()
    if solution is not None and "code" in solution:
        code = tuple(int(digit) for digit in solution["code"])
        revealed = tuple(
            Fact(ref=str(digit), value=position)
            for position, digit in enumerate(code)
        ) + tuple(
            Fact(ref=str(digit), value=False)
            for digit in digits
            if digit not in code
        )
    return EnumeratedSpace(
        answers=tuple(answers),
        admits=_admits,
        revealed=revealed,
        namespace=NAMESPACE,
    )


__all__ = ["GAME_ID", "NAMESPACE", "DigitLockCertifier", "certifier", "space"]
