"""The attribute algebra. Everything this game knows about what a set is.

One rule, stated once: three figures form a trio when, for every attribute,
their three values are either all the same or all different. Nothing else in
the game restates it, because a second statement is a second thing to be
wrong, and a generator and a verifier disagreeing about what a trio is would
produce boards that verify and cannot be solved.

Two consequences of the rule carry the whole game and are worth naming before
any code.

**At three values per attribute, any two figures determine exactly one
third.** Given two values, "all the same" is possible only if they are equal
and "all different" forces the remaining one, so for each attribute the third
value is determined: the shared value when the two agree, and the one value
neither of them is when they do not. That makes :func:`completion` a
computation rather than a search, and it is what lets the generator build a
board with exactly one trio instead of hunting for one.

**So a board's trios are exactly its pairs whose completion is also present.**
That turns "does adding this figure create a second trio" into a set lookup,
which is the whole of :func:`forbidden_completions`. The generator adds a
figure only when it is not the completion of any pair already down.

Both of those are true at three values per attribute and false at four, which
is why the provider's default space has three and why this module checks the
space it is given rather than assuming it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from itertools import combinations

#: The attributes a figure is made of, in the order a board reads them.
#:
#: Named here as well as in the content because the game has to iterate them
#: in a fixed order for its own output to be reproducible, and because a
#: figure missing one of them is a board this game cannot judge. The content
#: is still the authority on what values each attribute takes; this is only
#: the list of questions asked about every tile.
ATTRIBUTES: tuple[str, ...] = ("shape", "fill", "count", "colour")

#: How many values an attribute must have for the rule to be determinate.
VALUES_PER_ATTRIBUTE = 3

#: How many figures make a trio.
TRIO = 3

#: An attribute reading of one figure: attribute name to value.
Figure = Mapping[str, str]


class SpaceError(ValueError):
    """The attribute space this game was handed cannot carry the rule."""


def attribute_values(figures: Sequence[Figure]) -> dict[str, tuple[str, ...]]:
    """Every value each attribute takes across ``figures``, sorted."""
    found: dict[str, set[str]] = {attribute: set() for attribute in ATTRIBUTES}
    for figure in figures:
        for attribute in ATTRIBUTES:
            try:
                found[attribute].add(figure[attribute])
            except KeyError:
                raise SpaceError(
                    f"a figure carries no {attribute!r}: {dict(figure)}"
                ) from None
    return {
        attribute: tuple(sorted(values)) for attribute, values in found.items()
    }


def check_space(figures: Sequence[Figure]) -> dict[str, tuple[str, ...]]:
    """The attribute space of ``figures``, refused unless the rule holds in it.

    Checked rather than assumed, and checked against the content rather than
    against a constant. A build whose figure taxonomy had four colours would
    import cleanly, generate boards, and produce trios whose third figure the
    solver could not determine; the failure would look like unfair puzzles
    rather than like a content change. One pass over the day's figures is
    cheap enough to pay every day for a failure that expensive to diagnose.
    """
    space = attribute_values(figures)
    wrong = {
        attribute: values
        for attribute, values in space.items()
        if len(values) != VALUES_PER_ATTRIBUTE
    }
    if wrong:
        raise SpaceError(
            "every attribute needs exactly "
            f"{VALUES_PER_ATTRIBUTE} values for two figures to determine a "
            f"third; found {{{', '.join(f'{k}: {len(v)}' for k, v in sorted(wrong.items()))}}}"
        )
    return space


def is_trio(figures: Sequence[Figure]) -> bool:
    """Whether these three figures satisfy the rule.

    Takes any number and answers for three, because a caller holding a pair or
    a quartet has a bug and a function that silently judged them would hide
    it.
    """
    if len(figures) != TRIO:
        return False
    for attribute in ATTRIBUTES:
        values = {figure[attribute] for figure in figures}
        if len(values) not in (1, TRIO):
            return False
    return True


def failing_attributes(figures: Sequence[Figure]) -> tuple[str, ...]:
    """Attributes on which these three are neither all same nor all different.

    The measure of how close a triple came, which is what a board's difficulty
    is built from: a triple failing on one attribute is the thing a solver
    looks at twice.
    """
    if len(figures) != TRIO:
        return ATTRIBUTES
    return tuple(
        attribute
        for attribute in ATTRIBUTES
        if len({figure[attribute] for figure in figures}) not in (1, TRIO)
    )


def completion(
    first: Figure, second: Figure, space: Mapping[str, Sequence[str]]
) -> dict[str, str]:
    """The one figure that completes a trio with these two.

    Per attribute: the shared value when the two agree, since all-different is
    then impossible; otherwise the one value neither of them has, since
    all-same is then impossible. Both branches are forced, which is the
    property the whole generator rests on.
    """
    completed: dict[str, str] = {}
    for attribute in ATTRIBUTES:
        values = tuple(space[attribute])
        if len(values) != VALUES_PER_ATTRIBUTE:
            raise SpaceError(
                f"attribute {attribute!r} has {len(values)} values, so two "
                "figures do not determine a third"
            )
        mine, theirs = first[attribute], second[attribute]
        if mine == theirs:
            completed[attribute] = mine
            continue
        remaining = [value for value in values if value not in (mine, theirs)]
        if len(remaining) != 1:
            raise SpaceError(
                f"attribute {attribute!r} does not hold {mine!r} and "
                f"{theirs!r} plus exactly one other value"
            )
        completed[attribute] = remaining[0]
    return completed


def key_of(figure: Figure) -> tuple[str, ...]:
    """A figure as a hashable tuple, in attribute order.

    The generator keeps sets of figures and needs them hashable; a mapping is
    not. In :data:`ATTRIBUTES` order, so two readings of the same figure give
    the same key regardless of how the mapping was built.
    """
    return tuple(figure[attribute] for attribute in ATTRIBUTES)


def forbidden_completions(
    figures: Iterable[Figure], space: Mapping[str, Sequence[str]]
) -> set[tuple[str, ...]]:
    """Every figure that would make a second trio with a pair already down.

    The generator's whole gate. A board whose figures are pairwise free of
    each other's completions has no trio at all; seed it with one trio and add
    only non-completions, and it has exactly one.
    """
    held = list(figures)
    return {
        key_of(completion(first, second, space))
        for first, second in combinations(held, 2)
    }


def trios_in(figures: Sequence[Figure]) -> tuple[tuple[int, ...], ...]:
    """Index triples that form a trio, by brute enumeration.

    Enumeration rather than the pair-completion shortcut, and that is
    deliberate. The shortcut is what the generator uses to build a board; if
    the verifier used it too, a bug in it would be invisible, because the
    thing that built the board and the thing that checked it would be making
    the same mistake. Twelve tiles is two hundred and twenty triples, so the
    independent check costs nothing worth saving.
    """
    return tuple(
        indices
        for indices in combinations(range(len(figures)), TRIO)
        if is_trio([figures[index] for index in indices])
    )


def near_trios(figures: Sequence[Figure]) -> tuple[tuple[int, ...], ...]:
    """Index triples that fail on exactly one attribute.

    Not trios, and not nothing either: a triple that is right about three
    attributes and wrong about the fourth is what a solver stops on, so these
    are the board's difficulty rather than its noise.
    """
    return tuple(
        indices
        for indices in combinations(range(len(figures)), TRIO)
        if len(failing_attributes([figures[index] for index in indices])) == 1
    )


def describe(figure: Figure) -> str:
    """A figure as a phrase, for a hint or a diagnostic.

    Not what a reader hears: the spoken label comes from the content through
    the symbolic form subsystem, which owns that decision. This is for text a
    curator reads and for a hint that names an attribute.
    """
    return " ".join(str(figure[attribute]) for attribute in ATTRIBUTES)


__all__ = [
    "ATTRIBUTES",
    "TRIO",
    "VALUES_PER_ATTRIBUTE",
    "Figure",
    "SpaceError",
    "attribute_values",
    "check_space",
    "completion",
    "describe",
    "failing_attributes",
    "forbidden_completions",
    "is_trio",
    "key_of",
    "near_trios",
    "trios_in",
]
