"""An independent check on a ``grouping`` board.

The rules, as the game states them:

* The board is ``group_count`` groups of ``group_size`` tiles, and every tile
  belongs to exactly one group.
* A group is valid when its members share a category that does not also
  contain the whole board. Without the second half, "these are all animals"
  would make every partition of an all-animal board valid.
* A partition is valid only if its groups can be given one label each, taken
  from that group's shared distinguishing categories, with every pair of
  labels incomparable by extension. Four groups all reading "these are birds"
  is one group split four ways. Comparable means nested on this board: the
  tiles carrying one label are all among the tiles carrying the other.
* Two partitions differing only by exchanging tiles that carry exactly the
  same distinguishing categories are the same puzzle, not two answers to it.
* The board is correct when, after that collapse, one partition survives and
  it is the stated one.

The hidden overlay group is deliberately not a rule here. It is a second
reading of the same tiles offered after the first is exhausted, not a fifth
answer to the question the board asks, and counting it would make every board
ambiguous by construction.

**The search is a different search.** This is the point of the module, and on
this game it needs saying, because the game's own verifier is a tile-by-tile
partition walk under a validity predicate: a certifier that walked the same
way would be re-running the same algorithm and would agree with it for the
same reasons, including the wrong ones. So this one goes the other way round,
from the categories inward. Every valid group is a subset of some
distinguishing category's extension, so the certifier builds groups out of
those extensions and solves an exact cover of the board, forcing the
lowest-numbered uncovered tile at each step. Same rules, different route to
the count.

The honest ceiling is lower here than for the three enumerable games. The
board is 20 to 36 tiles and the cover search is bounded, so the count is exact
only when the search exhausts; and the surviving count is a count of collapsed
classes, which is uniqueness up to the game's declared tolerance rather than
uniqueness. The certifier claims ``SOLUTION_COUNT_KNOWN`` at best and
``ANSWER_VALID`` when the budget runs out, and says which in the detail.

Nothing here imports ``puzzlegen_grouping``. In particular it does not import
``verify.py``, which holds these same rules.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from itertools import combinations
from typing import Any

from ..certify import AssuranceLevel, CertificateResult, Certifier

GAME_ID = "grouping"

#: Groups the board asks for when the payload does not say. Four, which is
#: what the rule above is about; read from the payload where it is stated,
#: because a certifier that hard-coded the shape would stop checking the day
#: the shape changed.
DEFAULT_GROUP_COUNT = 4

#: Candidate groups the cover search may examine before giving up. Sized from
#: the game's own worst board rather than its average: 36 tiles into four
#: groups of nine is a far larger search than 20 into groups of five, and a
#: budget sized for the mean would run out only on the widest boards, which is
#: the least debuggable way for a budget to be wrong.
STATE_BUDGET = 2_000_000


def _universal(memberships: Mapping[str, frozenset[str]]) -> frozenset[str]:
    """Categories every tile on the board carries.

    These cannot justify a group, which is the second half of the validity
    rule and the half that makes uniqueness provable at all.
    """
    shared: set[str] | None = None
    for categories in memberships.values():
        shared = set(categories) if shared is None else shared & categories
    return frozenset(shared or ())


def _shared(
    group: Sequence[str],
    distinguishing: Mapping[str, frozenset[str]],
) -> frozenset[str]:
    """The distinguishing categories every member of a group carries."""
    shared: frozenset[str] | None = None
    for tile in group:
        categories = distinguishing.get(tile, frozenset())
        shared = categories if shared is None else shared & categories
        if not shared:
            return frozenset()
    return shared or frozenset()


def _labelable(
    partition: Sequence[Sequence[str]],
    distinguishing: Mapping[str, frozenset[str]],
    extension: Mapping[str, frozenset[str]],
) -> bool:
    """Whether the groups can be labelled without two labels meaning the same.

    One label per group from that group's shared categories, every pair
    incomparable. Tried fewest options first, so a partition that cannot be
    labelled fails early rather than after the deepest branch.
    """
    options = sorted(
        (sorted(_shared(group, distinguishing)) for group in partition),
        key=len,
    )
    if any(not choices for choices in options):
        return False

    def incomparable(first: str, second: str) -> bool:
        a, b = extension[first], extension[second]
        return not (a <= b or b <= a)

    def walk(remaining: Sequence[Sequence[str]], taken: tuple[str, ...]) -> bool:
        if not remaining:
            return True
        head, rest = remaining[0], remaining[1:]
        return any(
            all(incomparable(label, already) for already in taken)
            and walk(rest, (*taken, label))
            for label in head
        )

    return walk(options, ())


def _signature_key(
    partition: Sequence[Sequence[str]],
    distinguishing: Mapping[str, frozenset[str]],
) -> frozenset[frozenset[tuple[frozenset[str], int]]]:
    """The equivalence the game's tolerance contract is allowed to collapse.

    Keyed by each group's multiset of tile signatures rather than its tile
    ids, which collapses exactly the partitions that differ by swapping
    indistinguishable tiles and nothing else. A wider rule would let an
    ambiguous board claim uniqueness, which is the failure the contract
    exists to prevent.
    """
    key = []
    for group in partition:
        tally: dict[frozenset[str], int] = {}
        for tile in group:
            signature = distinguishing.get(tile, frozenset())
            tally[signature] = tally.get(signature, 0) + 1
        key.append(frozenset(tally.items()))
    return frozenset(key)


class _Cover:
    """Exact cover of the board by valid groups, from the categories inward.

    Holds the search's own state because the budget has to be shared across
    the whole recursion and a module-level counter would be shared across
    boards instead.
    """

    def __init__(
        self,
        tiles: Sequence[str],
        group_size: int,
        group_count: int,
        distinguishing: Mapping[str, frozenset[str]],
        extension: Mapping[str, frozenset[str]],
        budget: int,
    ) -> None:
        self._tiles = tuple(sorted(tiles))
        self._group_size = group_size
        self._group_count = group_count
        self._distinguishing = distinguishing
        self._extension = extension
        self._budget = budget
        self.examined = 0
        self.exhausted = True

    def solve(self) -> list[tuple[tuple[str, ...], ...]]:
        found: list[tuple[tuple[str, ...], ...]] = []
        self._walk(frozenset(self._tiles), (), found)
        return found

    def _walk(
        self,
        remaining: frozenset[str],
        chosen: tuple[tuple[str, ...], ...],
        found: list[tuple[tuple[str, ...], ...]],
    ) -> None:
        if not self.exhausted:
            return
        if not remaining:
            if len(chosen) == self._group_count and _labelable(
                chosen, self._distinguishing, self._extension
            ):
                found.append(chosen)
            return
        if len(chosen) == self._group_count:
            # Tiles left over with no group to put them in. Not a dead end
            # worth reporting: the cover simply does not close this way.
            return

        for group in self._groups_covering(min(remaining), remaining):
            self.examined += 1
            if self.examined > self._budget:
                self.exhausted = False
                return
            self._walk(remaining - frozenset(group), (*chosen, group), found)
            if not self.exhausted:
                return

    def _groups_covering(
        self, forced: str, remaining: frozenset[str]
    ) -> Iterator[tuple[str, ...]]:
        """Every valid group containing ``forced``, each yielded once.

        Forcing one tile is what keeps this bounded: a group is only ever
        built around the lowest-numbered uncovered tile, so the search never
        enumerates the same partition in several member orders.

        A group can sit inside more than one category's extension, so the
        same set of tiles is reachable by more than one route. Yielded once,
        tracked here rather than deduplicated by the caller, because a
        duplicate group is a duplicate subtree and the budget would be spent
        on it twice.
        """
        seen: set[frozenset[str]] = set()
        for category in sorted(self._distinguishing.get(forced, frozenset())):
            pool = sorted((self._extension[category] & remaining) - {forced})
            if len(pool) < self._group_size - 1:
                continue
            for rest in combinations(pool, self._group_size - 1):
                group = (forced, *rest)
                members = frozenset(group)
                if members in seen:
                    continue
                seen.add(members)
                # Re-derived rather than assumed from the category the group
                # was built around: membership in one shared category is the
                # reason this group was reachable, not proof that the rule
                # holds for it.
                if _shared(group, self._distinguishing):
                    yield tuple(sorted(group))


class GroupingCertifier:
    """Solves the board as an exact cover and counts the groupings found."""

    game_id = GAME_ID
    assurance = AssuranceLevel.SOLUTION_COUNT_KNOWN

    def __init__(self, *, state_budget: int = STATE_BUDGET) -> None:
        self._budget = state_budget

    def certify(
        self, payload: Mapping[str, Any], solution: Mapping[str, Any]
    ) -> CertificateResult:
        try:
            tiles = [str(tile["id"]) for tile in payload["tiles"]]
            group_size = int(payload["group_size"])
            group_count = int(payload.get("group_count", DEFAULT_GROUP_COUNT))
            memberships = {
                str(tile): frozenset(str(c) for c in categories)
                for tile, categories in solution["memberships"].items()
            }
            stated = tuple(
                tuple(sorted(str(member) for member in group["members"]))
                for group in solution["groups"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            return self._refuse(
                (f"the board is not readable as a grouping board: {exc}",)
            )

        reasons = self._shape_problems(
            tiles, group_size, group_count, memberships, stated
        )
        if reasons:
            return self._refuse(tuple(reasons))

        universal = _universal(memberships)
        distinguishing = {
            tile: categories - universal for tile, categories in memberships.items()
        }
        extension: dict[str, frozenset[str]] = {}
        for tile, categories in distinguishing.items():
            for category in categories:
                extension[category] = extension.get(category, frozenset()) | {tile}

        # The claim before its rivals. A board whose own stated answer breaks
        # the rules is refused on that, and the finding survives a search that
        # later runs out of budget -- which on this game it sometimes will.
        for group in stated:
            if not _shared(group, distinguishing):
                return self._refuse(
                    (
                        f"the stated group {list(group)} shares no category "
                        "beyond the ones the whole board shares, so nothing "
                        "justifies it as a group",
                    )
                )
        if not _labelable(stated, distinguishing, extension):
            return self._refuse(
                (
                    "the stated groups cannot be given distinct labels: two of "
                    "them are justified only by categories that nest on this "
                    "board, so they read as one group split in two",
                )
            )

        cover = _Cover(
            tiles,
            group_size,
            group_count,
            distinguishing,
            extension,
            self._budget,
        )
        found = cover.solve()

        collapsed: dict[Any, tuple[tuple[str, ...], ...]] = {}
        for partition in found:
            collapsed.setdefault(_signature_key(partition, distinguishing), partition)
        kept = list(collapsed.values())

        as_sets = frozenset(frozenset(group) for group in stated)
        reached = any(
            frozenset(frozenset(group) for group in partition) == as_sets
            for partition in found
        )

        if not cover.exhausted:
            # The stated answer is already known valid, and that is all this
            # run establishes. A count from a truncated search is a lower
            # bound, so it is not reported as the count.
            return CertificateResult(
                game_id=GAME_ID,
                accepted=True,
                assurance=AssuranceLevel.ANSWER_VALID,
                solution_count=None,
                states_examined=cover.examined,
                detail=(
                    f"the stated grouping satisfies the rules; the cover "
                    f"search stopped after {cover.examined} candidate groups, "
                    f"so {len(kept)} is a lower bound and uniqueness is not "
                    "established"
                ),
            )

        if not reached:
            # Verifies, is unique, and marks the player wrong: the most
            # dangerous way for a board to be broken.
            reasons.append(
                "the stated grouping is valid in isolation but the cover "
                "search did not reach it, so the two readings of the rules "
                "disagree about this board"
            )
        elif len(kept) > 1:
            reasons.append(
                f"{len(kept)} groupings survive the tolerance collapse, so a "
                "player could defend a grouping the board would mark wrong"
            )

        if reasons:
            return CertificateResult(
                game_id=GAME_ID,
                accepted=False,
                assurance=self.assurance,
                solution_count=len(kept),
                states_examined=cover.examined,
                reasons=tuple(reasons),
            )
        return CertificateResult(
            game_id=GAME_ID,
            accepted=True,
            assurance=self.assurance,
            solution_count=len(kept),
            states_examined=cover.examined,
            detail=(
                f"{cover.examined} candidate groups covered the board "
                f"exhaustively; {len(found)} groupings collapse to one and it "
                "is the stated grouping"
            ),
        )

    def _shape_problems(
        self,
        tiles: Sequence[str],
        group_size: int,
        group_count: int,
        memberships: Mapping[str, frozenset[str]],
        stated: Sequence[Sequence[str]],
    ) -> list[str]:
        """Everything wrong with the board before any rule is applied.

        Collected rather than returned one at a time, because a board that is
        wrong two ways is a different diagnosis from one that is wrong once.
        """
        reasons: list[str] = []
        if group_size < 2:
            reasons.append(f"a group of {group_size} tiles is not a group")
        if group_count < 2:
            reasons.append(
                f"{group_count} groups is not a partition the board can ask for"
            )
        if len(set(tiles)) != len(tiles):
            reasons.append("two tiles share an id, so the board is short a tile")
        expected = group_size * group_count
        if len(tiles) != expected:
            reasons.append(
                f"the board shows {len(tiles)} tiles, which is not "
                f"{group_count} groups of {group_size}"
            )
        uncategorised = sorted(set(tiles) - set(memberships))
        if uncategorised:
            reasons.append(
                f"tiles {uncategorised} carry no category memberships, so no "
                "grouping of them can be judged valid or otherwise"
            )
        if len(stated) != group_count:
            reasons.append(
                f"the stated answer names {len(stated)} groups, the board asks "
                f"for {group_count}"
            )
        claimed = [tile for group in stated for tile in group]
        if sorted(claimed) != sorted(tiles):
            missing = sorted(set(tiles) - set(claimed))
            extra = sorted(set(claimed) - set(tiles))
            repeated = len(claimed) != len(set(claimed))
            reasons.append(
                "the stated groups are not a partition of the board"
                + (f"; unplaced tiles {missing}" if missing else "")
                + (f"; tiles not on the board {extra}" if extra else "")
                + ("; a tile is placed twice" if repeated else "")
            )
        elif any(len(group) != group_size for group in stated):
            sizes = sorted({len(group) for group in stated})
            reasons.append(
                f"the stated groups have sizes {sizes}, not {group_size} each"
            )
        return reasons

    def _refuse(self, reasons: tuple[str, ...]) -> CertificateResult:
        return CertificateResult(
            game_id=GAME_ID,
            accepted=False,
            assurance=self.assurance,
            solution_count=None,
            reasons=reasons,
        )


def certifier() -> Certifier:
    return GroupingCertifier()


__all__ = [
    "DEFAULT_GROUP_COUNT",
    "GAME_ID",
    "STATE_BUDGET",
    "GroupingCertifier",
    "certifier",
]
