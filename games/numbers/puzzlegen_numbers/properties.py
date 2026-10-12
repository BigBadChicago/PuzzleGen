"""The property vocabulary, and what makes one property easier than another.

This game asks a player to see what all but one of some numbers have in
common. Everything it knows about "have in common" is here: the properties are
the categories the graph holds over numbers, read off the tiles' own
memberships, and nothing in this module decides what a property *is*. What it
does decide is two things the graph cannot.

**Which properties are familiar.** "Even" and "triangular" are both true of
six, and a player who needs a moment for the first needs a different kind of
moment for the second. The tier table below is this game's one piece of
judgement about its content, stated as data so a curator can argue with it,
and it is what the difficulty bands turn: an easy day is a board whose answer
is a property everybody knows.

**Which properties cannot be a board's answer at all.** A property true of
every number in the day's window is not a reading of any board drawn from it,
because it has no outsider; the taxonomy root is always such a property and
"two-digit number" is one as soon as the window is the two-digit numbers.
Excluding them is not an optimisation. A board whose window makes a property
universal and whose generator did not know would be a board with a property
that distinguishes nothing, counted as a reading, and refused as ambiguous.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

#: How familiar a property is, lowest first.
#:
#: Three tiers, because the difficulty bands are three. The split is
#: judgement rather than measurement and is written down as data for that
#: reason: nobody has play data, so the honest thing is a table somebody can
#: disagree with rather than a number that looks fitted.
#:
#: Tier 1 is arithmetic every player does without noticing. Tier 2 is school
#: arithmetic: a definition everybody has met, applied deliberately. Tier 3 is
#: a sequence a player either recognises or reconstructs, which is a different
#: and slower act than applying a definition.
TIERS: Mapping[str, int] = {
    "even number": 1,
    "odd number": 1,
    "multiple of ten": 1,
    "multiple of five": 2,
    "multiple of three": 2,
    "prime number": 2,
    "square number": 2,
    "triangular number": 3,
    "Fibonacci number": 3,
}

#: Tier given to a property the table has never heard of.
#:
#: The most obscure, which is the safe direction. A build that grew a new
#: number category would otherwise have it rated as familiar as "even", and an
#: easy day would serve a board nobody can read. Rating it obscure instead
#: means it only ever appears on a hard day until a curator rates it.
UNKNOWN_TIER = max(TIERS.values())

#: The lowest and highest tiers a board can be built at.
MIN_TIER = min(TIERS.values())
MAX_TIER = max(TIERS.values())


def tier_of(name: str) -> int:
    """How familiar a property is, by name."""
    return TIERS.get(name, UNKNOWN_TIER)


def obscurity_of(name: str) -> float:
    """The tier as a share, for the difficulty model.

    Normalised across the declared range rather than from zero, so the
    easiest tier scores zero and the hardest one: a feature whose floor was
    a third would compress the scale for no reason.
    """
    span = MAX_TIER - MIN_TIER
    if span <= 0:  # pragma: no cover - the table is a real range
        return 0.0
    return max(0.0, min(1.0, (tier_of(name) - MIN_TIER) / span))


def vocabulary_of(
    memberships: Mapping[str, Sequence[str]], *, exclude_universal: bool = True
) -> tuple[str, ...]:
    """Every property the day's numbers carry, in a fixed order.

    ``memberships`` maps a tile id to the properties it has. Sorted by name,
    so the vocabulary is a pure function of the content and two runs scan it
    in the same order.

    A property every number carries is dropped by default, for the reason the
    module docstring gives: it has no outsider, so it is a reading of no
    board. The flag exists because the verifier wants to know what was
    dropped, not because anything should keep them.
    """
    counts: dict[str, int] = {}
    for properties in memberships.values():
        for name in properties:
            counts[name] = counts.get(name, 0) + 1
    total = len(memberships)
    return tuple(
        sorted(
            name
            for name, count in counts.items()
            if not (exclude_universal and count == total)
        )
    )


def universal_in(memberships: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    """Properties every tile carries, which distinguish nothing."""
    if not memberships:
        return ()
    shared: set[str] | None = None
    for properties in memberships.values():
        names = set(properties)
        shared = names if shared is None else shared & names
    return tuple(sorted(shared or ()))


def holds_for_all_but(
    name: str,
    outsider: str,
    memberships: Mapping[str, Sequence[str]],
) -> bool:
    """Whether ``name`` is true of every tile except ``outsider``.

    The whole rule of the game, in one function. A board is read by finding a
    tile and a property standing in this relation; the verifier counts the
    tiles for which any property does.
    """
    if outsider not in memberships:
        return False
    for tile, properties in memberships.items():
        has = name in properties
        if tile == outsider:
            if has:
                return False
        elif not has:
            return False
    return True


def readings_of(
    memberships: Mapping[str, Sequence[str]],
    vocabulary: Iterable[str],
) -> dict[str, tuple[str, ...]]:
    """Which tiles could be the odd one out, and under which properties.

    Keyed by tile, because the answer a player gives is a tile. Two properties
    picking out the same tile are not two answers: they are one answer with
    two reasons, and a board that had them would be *easier* rather than
    ambiguous. Only a second tile makes a board ambiguous, which is why this
    returns a mapping rather than a list of pairs.
    """
    found: dict[str, list[str]] = {}
    names = list(vocabulary)
    for tile in memberships:
        reasons = [
            name for name in names if holds_for_all_but(name, tile, memberships)
        ]
        if reasons:
            found[tile] = reasons
    return {tile: tuple(sorted(reasons)) for tile, reasons in sorted(found.items())}


def near_readings_of(
    memberships: Mapping[str, Sequence[str]],
    vocabulary: Iterable[str],
) -> tuple[str, ...]:
    """Properties true of all but exactly two tiles.

    Not readings: two outsiders is not an odd one out. They are what a solver
    tries and discards, so they are the board's difficulty rather than its
    noise, in the same way a near trio is on a figure board.
    """
    total = len(memberships)
    near: list[str] = []
    for name in vocabulary:
        holders = sum(1 for properties in memberships.values() if name in properties)
        if holders == total - 2:
            near.append(name)
    return tuple(sorted(near))


__all__ = [
    "MAX_TIER",
    "MIN_TIER",
    "TIERS",
    "UNKNOWN_TIER",
    "holds_for_all_but",
    "near_readings_of",
    "obscurity_of",
    "readings_of",
    "tier_of",
    "universal_in",
    "vocabulary_of",
]
