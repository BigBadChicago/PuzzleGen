"""The deduction. Everything this game knows about a code and a clue.

Two kinds of clue, and the difference between them is the whole shape of the
game.

A **guess clue** is a code somebody tried and what they were told: how many
digits were right and in the right place, and how many were right in the wrong
place. That is the classic pair, and the one thing worth stating precisely
about it is that the two numbers are disjoint -- a digit counted as exact is
not also counted as misplaced -- because a solver who assumed otherwise would
read every clue as weaker than it is.

A **property clue** is a fact about the code, read off the graph: how many of
its digits are prime, how many are even, what they add to. Those are not
deductions a solver makes from guesses; they are given, and they come from the
digit entities' own facts rather than from arithmetic written here. That is
what makes this a game over the knowledge graph rather than a Mastermind
clone with a graph bolted on: the properties a clue can state are exactly the
properties the content holds about digits, and a build whose digit taxonomy
said something different would produce different clues.

The codes are sequences of **distinct** digits. Repeats were considered and
rejected: with a repeat, "two digits right in the wrong place" has two
readings a solver cannot tell apart, the space grows from five thousand to ten
thousand, and the extra five thousand are codes the clue language describes
worse. Distinctness is stated once, here, and checked everywhere a code
arrives from outside.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import permutations

#: The digits a code is built from.
#:
#: Ten, and not a constant this module invents: the content supplies the
#: digits and this is only the ceiling the permutation count is computed
#: against. A build whose digits taxonomy held eight would make a smaller
#: space, which the verifier would report honestly.
BASE = 10

#: The property kinds a clue can state.
#:
#: Each reads one fact the digit entities carry. ``sum`` reads ``value``,
#: ``primes`` reads ``is_prime``, ``evens`` reads ``parity``. The names are
#: the wire form -- they travel in a payload a client renders -- so they are
#: short and stable rather than descriptive.
PROPERTY_KINDS = ("sum", "primes", "evens")


class CodeError(ValueError):
    """A code that is not a code: wrong length, a repeat, a digit out of range."""


@dataclass(frozen=True, slots=True)
class DigitFacts:
    """What the graph says about one digit.

    A record rather than a lookup into the entity view, because the deduction
    runs over thousands of candidate codes and every one of them asks these
    questions. Built once from the content, read many times.
    """

    value: int
    prime: bool
    even: bool


def facts_from(
    values: Mapping[int, DigitFacts], code: Sequence[int]
) -> tuple[int, int, int]:
    """``(sum, primes, evens)`` for one code, from the graph's own facts.

    Looked up rather than computed. A build whose digit taxonomy disagreed
    with arithmetic would be wrong, and it would be wrong *consistently*:
    generation, verification and the clue a player reads would all say the
    same thing, which is the property that matters for a puzzle being fair.
    Computing primality here instead would make the clue and the content
    disagree and the board unsolvable from what it shows.
    """
    total = 0
    primes = 0
    evens = 0
    for digit in code:
        try:
            fact = values[digit]
        except KeyError:
            raise CodeError(f"the content holds no digit {digit}") from None
        total += fact.value
        primes += fact.prime
        evens += fact.even
    return total, primes, evens


def check_code(code: Sequence[int], *, length: int, digits: Iterable[int]) -> tuple[int, ...]:
    """``code`` as a tuple, refused unless it is a legal code.

    Refused rather than coerced, and checked on everything arriving from
    outside: a candidate payload, a submitted guess, a clue in a stored
    puzzle. The three failures are different and each gets its own message,
    because a client sending a four-digit code to a three-digit board and a
    client sending a repeat are different bugs.
    """
    chosen = tuple(int(digit) for digit in code)
    if len(chosen) != length:
        raise CodeError(f"a code is {length} digits, got {len(chosen)}")
    if len(set(chosen)) != len(chosen):
        raise CodeError(f"a code has no repeated digit, got {list(chosen)}")
    allowed = set(digits)
    outside = sorted(set(chosen) - allowed)
    if outside:
        raise CodeError(f"these are not digits this board uses: {outside}")
    return chosen


def verdict(guess: Sequence[int], secret: Sequence[int]) -> tuple[int, int]:
    """``(exact, misplaced)`` for one guess against one code.

    Disjoint counts: a digit in the right place is exact and is not counted
    again as misplaced. With distinct digits the arithmetic is simply the
    overlap of the two sets minus the positional matches, which is why
    distinctness is worth the constraint -- with repeats this function needs a
    multiset argument and the clue it produces has two readings.
    """
    exact = sum(one == two for one, two in zip(guess, secret))
    shared = len(set(guess) & set(secret))
    return exact, shared - exact


@dataclass(frozen=True, slots=True)
class GuessClue:
    """One code tried and what it was told."""

    guess: tuple[int, ...]
    exact: int
    misplaced: int

    def admits(self, code: Sequence[int]) -> bool:
        return verdict(self.guess, code) == (self.exact, self.misplaced)

    def solves(self) -> bool:
        """Whether this clue is the answer.

        A guess with every digit exact is the code, so such a clue would print
        the answer on the board. Generation never makes one and assembly
        refuses one; this is the predicate both use.
        """
        return self.misplaced == 0 and self.exact == len(self.guess)


@dataclass(frozen=True, slots=True)
class PropertyClue:
    """One fact about the code, stated.

    ``kind`` is from :data:`PROPERTY_KINDS` and ``value`` is what the graph
    says about the secret. A clue whose kind this release has never heard of
    admits every code rather than none: a snapshot carrying a new kind should
    serve a board with one fewer usable clue rather than a board nothing can
    solve.
    """

    kind: str
    value: int

    def admits(self, code: Sequence[int], values: Mapping[int, DigitFacts]) -> bool:
        total, primes, evens = facts_from(values, code)
        if self.kind == "sum":
            return total == self.value
        if self.kind == "primes":
            return primes == self.value
        if self.kind == "evens":
            return evens == self.value
        return True

    def sentence(self) -> str:
        """What a client shows. Here because the wording is part of the clue.

        A client inventing its own phrasing would be inventing the puzzle's
        statement, and two clients wording "exactly two are prime" and "two or
        more are prime" differently would be two different games.
        """
        if self.kind == "sum":
            return f"The digits add up to {self.value}"
        if self.kind == "primes":
            return _count_sentence(self.value, "prime")
        if self.kind == "evens":
            return _count_sentence(self.value, "even")
        return f"{self.kind}: {self.value}"


def _count_sentence(count: int, adjective: str) -> str:
    if count == 0:
        return f"None of the digits is {adjective}"
    if count == 1:
        return f"Exactly one digit is {adjective}"
    return f"Exactly {count} of the digits are {adjective}"


def property_clues_for(
    code: Sequence[int], values: Mapping[int, DigitFacts], kinds: Sequence[str]
) -> tuple[PropertyClue, ...]:
    """The stated facts about ``code``, for the kinds asked for.

    True by construction, which is the point: a property clue is read off the
    code rather than chosen and checked, so there is no way for one to be
    false about the board it is on.
    """
    total, primes, evens = facts_from(values, code)
    found = {"sum": total, "primes": primes, "evens": evens}
    return tuple(
        PropertyClue(kind=kind, value=found[kind])
        for kind in kinds
        if kind in found
    )


def space_for(length: int, digits: Sequence[int]) -> list[tuple[int, ...]]:
    """Every code of ``length`` over ``digits``, in a fixed order.

    The whole solution space, which this game can afford to hold: ten digits
    taken four at a time is five thousand and forty codes. That is the reason
    the verifier can promise COMPLETE without qualification, and the reason
    the generator can build a board by elimination rather than by search.
    """
    return list(permutations(sorted(digits), length))


def survivors(
    space: Iterable[Sequence[int]],
    guesses: Sequence[GuessClue],
    properties: Sequence[PropertyClue],
    values: Mapping[int, DigitFacts],
) -> list[tuple[int, ...]]:
    """Every code in ``space`` that every clue admits.

    The solver's own work, and the verifier's. Property clues are applied
    first because they are cheaper -- three integer comparisons against a
    precomputed triple, against a set intersection per guess -- and because on
    a board that has them they remove most of the space before any guess is
    read.
    """
    kept: list[tuple[int, ...]] = []
    for code in space:
        chosen = tuple(code)
        if not all(clue.admits(chosen, values) for clue in properties):
            continue
        if not all(clue.admits(chosen) for clue in guesses):
            continue
        kept.append(chosen)
    return kept


__all__ = [
    "BASE",
    "PROPERTY_KINDS",
    "CodeError",
    "DigitFacts",
    "GuessClue",
    "PropertyClue",
    "check_code",
    "facts_from",
    "property_clues_for",
    "space_for",
    "survivors",
    "verdict",
]
