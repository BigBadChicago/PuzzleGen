"""Proving a board has one answer, or refusing to claim it has.

The engine never takes a solution count on trust: it compares the count
against the contract. What this module owes the engine is an honest count, and
honest here has a precise meaning. Every grouping a player could defend must
be enumerated, not merely the one the generator intended, because the second
grouping is exactly what a player will find and be marked wrong for.

Three rules carry the weight.

A group is valid when its members share a category that does not also contain
the whole board. Without the second half, "these are all animals" would make
every partition of an all-animal board valid and uniqueness unprovable.

Only the lexical axis counts. The hidden overlay group is a second reading of
the same tiles, offered after the first is exhausted, not a fifth answer to
the question the board asks. Counting it here would make every board
ambiguous by construction.

And an incomplete enumeration satisfies no uniqueness contract. A budget
overrun reports ``SOUND_INCOMPLETE`` with the count found so far as a lower
bound, never a number dressed up as exact.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ...core.types import VerificationCompleteness
from ...engine.plugin import Puzzle, VerificationResult
from ...engine.solvers import collapse_equivalent, enumerate_partitions
from ...engine.uniqueness import COLLAPSED_SOLUTIONS
from .descriptor import SEARCH_BOUND, VISIBLE_GROUPS

#: Metric names. Constants because the engine reads the first by name and the
#: others are read by difficulty measurement, and a typo in either place would
#: be a silently absent number rather than an error.
ALTERNATIVE_PARTITIONS = "alternative_partitions"
CANDIDATE_GROUPS = "candidate_groups"
INTERCHANGEABLE_TILES = "interchangeable_tiles"
DISTINGUISHING_CATEGORIES = "distinguishing_categories"


def board_tiles(puzzle: Puzzle) -> tuple[str, ...]:
    return tuple(tile["id"] for tile in puzzle.payload["tiles"])


def memberships_of(puzzle: Puzzle) -> dict[str, frozenset[str]]:
    raw: Mapping[str, Sequence[str]] = puzzle.solution.get("memberships", {})
    return {tile: frozenset(categories) for tile, categories in raw.items()}


def universal_categories(
    memberships: Mapping[str, frozenset[str]]
) -> frozenset[str]:
    """Categories every tile on the board belongs to.

    These cannot justify a group. A board drawn entirely from animals shares
    "animal" across all of it, and allowing that as a group's reason would
    make every partition valid and the uniqueness claim empty.
    """
    if not memberships:
        return frozenset()
    shared = None
    for categories in memberships.values():
        shared = set(categories) if shared is None else shared & categories
    return frozenset(shared or ())


def group_validator(
    memberships: Mapping[str, frozenset[str]], universal: frozenset[str]
):
    """A group is valid when its members share a distinguishing category."""

    def is_valid(group: tuple[str, ...]) -> bool:
        shared: frozenset[str] | None = None
        for tile in group:
            categories = memberships.get(tile, frozenset())
            shared = categories if shared is None else shared & categories
            if not shared:
                return False
        return bool((shared or frozenset()) - universal)

    return is_valid


def partition_validator(
    memberships: Mapping[str, frozenset[str]], universal: frozenset[str]
):
    """Constraints that cannot be judged one group at a time.

    A partition is valid only if its groups can be given labels that do not
    mean the same thing on this board. Four groups all reading "these are
    birds" is one group split four ways, and a player who found it would be
    right in a way the board cannot mark.

    "The same thing" is judged by extension, not by name. Two labels are
    comparable when the tiles carrying one are all among the tiles carrying the
    other, which is what nesting looks like once only this board is in view:
    ``craft`` and ``vehicle`` are different words and, on a board whose only
    vehicles are craft, exactly the same tiles. Tiles carry their whole
    ancestry, so a group is almost never justified by a single category, and
    a rule that only compared single justifications let ten craft tiles be
    split into two arbitrary groups of five in 126 ways, each "justified" by
    ``{craft, vehicle}`` twice over.

    A partition therefore needs one label per group, taken from that group's
    shared categories, with every pair of labels incomparable. The intended
    partition always has one: each visible group is the kinds of a parent, the
    parents are chosen not to be nested, and no tile carries another chosen
    parent, so each parent's extension is exactly its own group.
    """
    extension: dict[str, set[str]] = {}
    for tile, categories in memberships.items():
        for category in categories - universal:
            extension.setdefault(category, set()).add(tile)

    def reasons(group: tuple[str, ...]) -> frozenset[str]:
        shared: frozenset[str] | None = None
        for tile in group:
            categories = memberships.get(tile, frozenset())
            shared = categories if shared is None else shared & categories
        return (shared or frozenset()) - universal

    def incomparable(first: str, second: str) -> bool:
        a, b = extension[first], extension[second]
        return not (a <= b or b <= a)

    def labelable(options: list[list[str]], chosen: tuple[str, ...] = ()) -> bool:
        if not options:
            return True
        head, rest = options[0], options[1:]
        return any(
            all(incomparable(label, taken) for taken in chosen)
            and labelable(rest, (*chosen, label))
            for label in head
        )

    def is_valid(partition: tuple[tuple[str, ...], ...]) -> bool:
        claimed = [sorted(reasons(group)) for group in partition]
        if any(not options for options in claimed):
            # A group with no shared category is refused by the group level
            # check; nothing here can make it labelable.
            return False
        # Fewest options first, so the search fails early when it is going to.
        return labelable(sorted(claimed, key=len))

    return is_valid


def signature_of(
    tile: str, memberships: Mapping[str, frozenset[str]], universal: frozenset[str]
) -> frozenset[str]:
    """What distinguishes a tile from the others, as far as the graph knows."""
    return memberships.get(tile, frozenset()) - universal


def tolerance_key(
    memberships: Mapping[str, frozenset[str]], universal: frozenset[str]
):
    """The equivalence a tolerance contract is allowed to collapse.

    Two tiles carrying exactly the same distinguishing categories are
    indistinguishable: nothing in the content tells a player which of them
    belongs where, so two partitions differing only by exchanging them are the
    same puzzle rather than two answers to it. Keying each partition by its
    tiles' signatures instead of their ids collapses precisely those and
    nothing else.

    This is narrow on purpose. A tolerance rule wide enough to collapse
    genuinely different readings would let an ambiguous board claim
    uniqueness, which is the failure the contract exists to prevent.
    """

    def key(partition: tuple[tuple[str, ...], ...]):
        return frozenset(
            frozenset(
                (tile_sig, count)
                for tile_sig, count in _counted(
                    signature_of(tile, memberships, universal) for tile in group
                )
            )
            for group in partition
        )

    return key


def _counted(signatures):
    tally: dict[frozenset[str], int] = {}
    for signature in signatures:
        tally[signature] = tally.get(signature, 0) + 1
    return tuple(sorted(tally.items(), key=lambda pair: (sorted(pair[0]), pair[1])))


def interchangeable_tiles(
    memberships: Mapping[str, frozenset[str]], universal: frozenset[str]
) -> int:
    """How many tiles share their signature with another tile.

    Reported because it is the input to the tolerance rule: a board with none
    cannot have collapsed anything, and a verifier claiming a collapse on such
    a board is claiming something impossible.
    """
    tally: dict[frozenset[str], int] = {}
    for tile in memberships:
        signature = signature_of(tile, memberships, universal)
        tally[signature] = tally.get(signature, 0) + 1
    return sum(count for count in tally.values() if count > 1)


def as_sets(partition) -> frozenset[frozenset[str]]:
    return frozenset(frozenset(group) for group in partition)


def _as_solution(
    puzzle: Puzzle,
    partition,
    memberships: Mapping[str, frozenset[str]],
    universal: frozenset[str],
) -> dict:
    """One found grouping, in the shape the engine compares against.

    The engine hashes the puzzle's whole solution and looks for that hash
    among the ones the verifier reports, which is how it catches a puzzle
    whose stated answer its own solver cannot reach. That check only works if
    a found grouping matching the intended one is reported in exactly the
    intended shape, down to key order and member order, so the intended
    partition is returned as the puzzle's own solution object rather than as
    an equivalent rebuilt from the search.
    """
    intended = {
        frozenset(group["members"]): group for group in puzzle.solution["groups"]
    }
    if all(frozenset(group) in intended for group in partition):
        return dict(puzzle.solution)

    groups = sorted((sorted(group) for group in partition), key=lambda g: g[0])
    reasons = []
    for group in groups:
        shared: frozenset[str] | None = None
        for tile in group:
            categories = memberships.get(tile, frozenset())
            shared = categories if shared is None else shared & categories
        reasons.append(sorted((shared or frozenset()) - universal))
    return {
        "groups": [
            {
                "index": index,
                "category": reason[0] if reason else None,
                "category_id": reason[0] if reason else None,
                "members": members,
            }
            for index, (members, reason) in enumerate(zip(groups, reasons))
        ],
        "assignment": {
            tile: index for index, members in enumerate(groups) for tile in members
        },
        "hidden": dict(puzzle.solution["hidden"]),
        "borrowings": list(puzzle.solution.get("borrowings", ())),
        "memberships": dict(puzzle.solution.get("memberships", {})),
    }


def verify(puzzle: Puzzle, *, state_budget: int = SEARCH_BOUND) -> VerificationResult:
    """Every grouping a player could defend, counted honestly."""
    tiles = board_tiles(puzzle)
    memberships = memberships_of(puzzle)
    group_size = int(puzzle.payload["group_size"])
    universal = universal_categories(memberships)

    missing = [tile for tile in tiles if tile not in memberships]
    if missing:
        return VerificationResult(
            solvable=False,
            solution_count=0,
            completeness=VerificationCompleteness.COMPLETE,
            detail=(
                f"{len(missing)} tiles carry no category memberships, so no "
                "grouping can be judged valid or otherwise"
            ),
        )

    search = enumerate_partitions(
        tiles,
        group_size=group_size,
        group_count=VISIBLE_GROUPS,
        is_valid_group=group_validator(memberships, universal),
        is_valid_partition=partition_validator(memberships, universal),
        state_budget=state_budget,
    )

    kept, collapsed = collapse_equivalent(
        search.solutions, key=tolerance_key(memberships, universal)
    )

    intended = as_sets(
        tuple(tuple(group["members"]) for group in puzzle.solution["groups"])
    )
    found = [as_sets(partition) for partition in kept]

    metrics = {
        COLLAPSED_SOLUTIONS: float(collapsed),
        ALTERNATIVE_PARTITIONS: float(max(0, len(kept) - 1)),
        CANDIDATE_GROUPS: float(search.states_examined),
        INTERCHANGEABLE_TILES: float(interchangeable_tiles(memberships, universal)),
        DISTINGUISHING_CATEGORIES: float(
            len({c for tile in tiles for c in signature_of(tile, memberships, universal)})
        ),
    }

    detail = ""
    if not search.exhausted:
        # A lower bound, and said so. The unexamined branch is where a second
        # solution would hide, so the count is evidence of nothing on its own.
        detail = (
            f"search stopped after {search.states_examined} states; "
            f"{len(kept)} solutions found so far is a lower bound"
        )
    elif intended not in found:
        # Verifies, is unique, and marks the player wrong: the most dangerous
        # way for a board to be broken, and one hash catches it.
        detail = "the intended solution is not among the groupings found"

    return VerificationResult(
        solvable=bool(kept),
        solution_count=len(kept),
        completeness=search.completeness,
        states_examined=search.states_examined,
        solutions=tuple(
            _as_solution(puzzle, partition, memberships, universal)
            for partition in kept[:8]
        ),
        solutions_truncated=len(kept) > 8,
        metrics=metrics,
        detail=detail,
    )


__all__ = [
    "ALTERNATIVE_PARTITIONS",
    "CANDIDATE_GROUPS",
    "DISTINGUISHING_CATEGORIES",
    "INTERCHANGEABLE_TILES",
    "as_sets",
    "board_tiles",
    "group_validator",
    "interchangeable_tiles",
    "memberships_of",
    "partition_validator",
    "signature_of",
    "tolerance_key",
    "universal_categories",
    "verify",
]
