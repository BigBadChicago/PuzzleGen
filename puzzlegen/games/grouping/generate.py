"""Choosing four groups that hide a fifth.

A candidate here is a claim that five specific groups can share one board: four
visible ones the player is asked for, and an overlay group whose members are
already among those tiles. The claim is cheap and most are discarded, which is
the contract: filtering is the engine's job, and a game that pre-filters on
freshness, confidence or policy is duplicating a gate it cannot see the inputs
to.

Two things this module does refuse on its own, because they are facts about the
board rather than about content policy. A tile appearing in two chosen groups
makes the intended partition one of several and the puzzle unfair, and a hidden
group whose members do not touch every visible group is findable by counting.
Both are properties of a combination, not of any group the content service
could have rejected.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ...content.query import ContentResult, GroupView
from ...core.rng import DeterministicRng
from ...engine.plugin import GenerationContext, PuzzleCandidate
from . import content as content_module
from . import descriptor as descriptor_module
from .descriptor import GAME_ID, VISIBLE_GROUPS, group_size_for

#: Chosen visible-group combinations examined per hidden group.
#:
#: The search is combinatorial and most combinations fail the disjointness
#: check immediately, so it is bounded rather than exhaustive. Sized so a day
#: with a generous content pool still terminates quickly; a day that finds
#: nothing within it has a content problem that a longer search would only
#: delay discovering.
COMBINATION_BUDGET = 4_000

#: Visible groups considered per hidden group, most promising first.
#:
#: Every group in the pool could in principle pair with every other, which is
#: C(40, 4) = 91,390 combinations. Narrowing to the groups that actually
#: contain a hidden member first cuts that to the ones that could possibly
#: work.
VISIBLE_SHORTLIST = 16

#: Cross-group temptation a board should have at least this much of.
#:
#: Zero temptation is four groups with nothing in common, which is a sorting
#: exercise rather than a puzzle. Measured as shared category ancestry between
#: members of different groups, so it counts real family resemblance rather
#: than a similarity number.
MINIMUM_TEMPTATION = 1

#: And at most this much.
#:
#: Past this the groups blur into each other and the board stops having one
#: convincing reading, which the verifier would catch anyway but far more
#: expensively than a count does here.
MAXIMUM_TEMPTATION = 24


@dataclass(frozen=True, slots=True)
class BoardPlan:
    """Five groups that fit one board, with the evidence for that claim."""

    hidden: GroupView
    visible: tuple[GroupView, ...]
    #: Hidden members, grouped by which visible group holds each one.
    borrowings: tuple[tuple[int, str], ...]
    temptation: int
    enriched: bool

    def tiles(self) -> tuple[str, ...]:
        return tuple(
            member.entity_id for group in self.visible for member in group.members
        )

    def dependency_ids(self) -> tuple[str, ...]:
        ids: list[str] = []
        for group in (*self.visible, self.hidden):
            ids.extend(group.dependency_ids())
        return tuple(dict.fromkeys(ids))

    def candidate_id(self) -> str:
        """Derived from the groups, so the same five groups are one candidate.

        Deterministic and order independent: the same board discovered by two
        different search paths must not look like two candidates, or the
        engine's budget is spent verifying the same puzzle twice.
        """
        parts = sorted(
            group.shared_category_id or group.shared_category or ""
            for group in self.visible
        )
        hidden = self.hidden.shared_category_id or self.hidden.shared_category or ""
        return f"{GAME_ID}:{hidden}:" + "+".join(parts)


def member_ids(group: GroupView) -> frozenset[str]:
    return frozenset(member.entity_id for member in group.members)


def ancestry(group: GroupView) -> frozenset[str]:
    """Every category any member of this group belongs to.

    Used for temptation rather than membership: a tile's own group is decided
    by the chosen categories, but its resemblance to another group is carried
    by everything else it happens to be.
    """
    found: set[str] = set()
    for member in group.members:
        found.update(member.type_ids)
    return frozenset(found)


def is_disjoint(groups: Sequence[GroupView]) -> bool:
    seen: set[str] = set()
    for group in groups:
        ids = member_ids(group)
        if ids & seen:
            return False
        seen |= ids
    return True


def has_no_cross_membership(groups: Sequence[GroupView]) -> bool:
    """No tile belongs to another chosen group's category.

    Disjointness says a tile is listed once. This says it could only have been
    listed once: a tile in group A that also carries B's category makes the
    intended partition one of several, and the player who puts it in B is not
    wrong.
    """
    categories = {
        group.shared_category_id for group in groups if group.shared_category_id
    }
    for group in groups:
        own = group.shared_category_id
        for member in group.members:
            if (set(member.type_ids) & categories) - {own}:
                return False
    return True


def temptation_of(groups: Sequence[GroupView]) -> int:
    """How much the groups resemble each other.

    Counted as shared category ancestry between every pair of groups, minus
    the categories that define them. A board with none is four unrelated piles
    and sorts itself; a board with too much has no single convincing reading.
    """
    defining = {
        group.shared_category_id for group in groups if group.shared_category_id
    }
    total = 0
    for left, right in itertools.combinations(groups, 2):
        shared = (ancestry(left) & ancestry(right)) - defining
        total += len(shared)
    return total


def borrowings_for(
    hidden: GroupView, visible: Sequence[GroupView]
) -> tuple[tuple[int, str], ...] | None:
    """Which visible group holds each hidden member, or None if the fit fails.

    Two conditions, and both matter for a different reason. Every hidden
    member must already be on the board, because the hidden group is a second
    reading of the same tiles rather than extra ones. And every visible group
    must give up at least one, because a hidden group drawn from three of the
    four is findable by noticing which group is untouched.
    """
    placement: list[tuple[int, str]] = []
    touched: set[int] = set()
    for member in hidden.members:
        home = None
        for index, group in enumerate(visible):
            if member.entity_id in member_ids(group):
                home = index
                break
        if home is None:
            return None
        placement.append((home, member.entity_id))
        touched.add(home)
    if len(touched) != len(visible):
        return None
    return tuple(placement)


def shortlist_for(
    hidden: GroupView, pool: Sequence[GroupView], group_size: int
) -> list[GroupView]:
    """Visible groups worth trying against this hidden group.

    Ordered by how many hidden members they could contribute, then by id, so
    the search reaches workable combinations early and the ordering is stable
    across runs.
    """
    wanted = member_ids(hidden)
    scored = [
        (len(member_ids(group) & wanted), group)
        for group in pool
        if len(group.members) == group_size
    ]
    scored.sort(
        key=lambda pair: (
            -pair[0],
            pair[1].shared_category_id or pair[1].shared_category or "",
        )
    )
    ranked = [group for count, group in scored if count > 0]

    # One group per defining category before any category gets a second. Groups
    # drawn from one parent overlap heavily and tie on how many hidden members
    # they hold, so filling the shortlist in rank order spends every slot on
    # variations of a few parents and leaves the four-way combination search
    # to discover, thousands of times, that they share tiles.
    chosen: list[GroupView] = []
    seen_categories: set[str] = set()
    remainder: list[GroupView] = []
    for group in ranked:
        key = group.shared_category_id or group.shared_category or ""
        if key in seen_categories:
            remainder.append(group)
        else:
            seen_categories.add(key)
            chosen.append(group)
    return (chosen + remainder)[:VISIBLE_SHORTLIST]


#: Why a combination of four visible groups was not a board. Counted rather
#: than logged, because an empty day is a number problem: knowing that 9,000
#: combinations failed is useless, and knowing that 8,997 of them failed the
#: borrowing rule says exactly which constraint to argue with.
SHORTLIST_TOO_SMALL = "shortlist_too_small"
NOT_DISJOINT = "not_disjoint"
CROSS_MEMBERSHIP = "cross_membership"
BORROWING_FAILED = "borrowing_failed"
TEMPTATION_TOO_LOW = "temptation_too_low"
TEMPTATION_TOO_HIGH = "temptation_too_high"
BUDGET_EXHAUSTED = "budget_exhausted"


def plans_for(
    hidden: GroupView,
    pool: Sequence[GroupView],
    group_size: int,
    *,
    enriched: bool,
    budget: int,
    rejected: dict[str, int] | None = None,
) -> Iterable[BoardPlan]:
    """Every board this hidden group can sit in, within a budget."""

    def refuse(reason: str) -> None:
        if rejected is not None:
            rejected[reason] = rejected.get(reason, 0) + 1

    shortlist = shortlist_for(hidden, pool, group_size)
    if len(shortlist) < VISIBLE_GROUPS:
        refuse(SHORTLIST_TOO_SMALL)
        return

    examined = 0
    for combination in itertools.combinations(shortlist, VISIBLE_GROUPS):
        examined += 1
        if examined > budget:
            refuse(BUDGET_EXHAUSTED)
            return
        if not is_disjoint(combination):
            refuse(NOT_DISJOINT)
            continue
        if not has_no_cross_membership(combination):
            refuse(CROSS_MEMBERSHIP)
            continue
        borrowings = borrowings_for(hidden, combination)
        if borrowings is None:
            refuse(BORROWING_FAILED)
            continue
        temptation = temptation_of(combination)
        if temptation < MINIMUM_TEMPTATION:
            refuse(TEMPTATION_TOO_LOW)
            continue
        if temptation > MAXIMUM_TEMPTATION:
            refuse(TEMPTATION_TOO_HIGH)
            continue
        yield BoardPlan(
            hidden=hidden,
            visible=tuple(combination),
            borrowings=borrowings,
            temptation=temptation,
            enriched=enriched,
        )


def hidden_candidates(
    context: GenerationContext, group_size: int | None = None
) -> list[tuple[GroupView, bool]]:
    """Hidden groups to try, richer ones first.

    A group that also shares a second overlay category gives the hint system
    something true to say that is not the answer, so those are tried first. The
    plain ones follow rather than being excluded, because the enriched query is
    optional and most days will have none.
    """
    ordered: list[tuple[GroupView, bool]] = []
    seen: set[frozenset[str]] = set()
    if group_size is None:
        group_size = group_size_for(context.day_key)
    for name, enriched in (
        (content_module.sized(content_module.HIDDEN_ENRICHED, group_size), True),
        (content_module.sized(content_module.HIDDEN, group_size), False),
    ):
        result = context.content.get(name)
        if result is None:
            continue
        for group in result.groups:
            key = member_ids(group)
            if key in seen:
                continue
            seen.add(key)
            ordered.append((group, enriched))
    return ordered


def payload_for(plan: BoardPlan) -> dict[str, object]:
    """What a candidate carries forward.

    Ids and names, not view objects: everything here crosses the plugin
    boundary and has to survive being JSON on the way to a subprocess.
    """
    memberships: dict[str, list[str]] = {}
    for group in plan.visible:
        for member in group.members:
            memberships[member.entity_id] = list(member.type_ids)

    return {
        "group_size": len(plan.visible[0].members),
        # Every category each tile belongs to. The verifier needs it to
        # enumerate the groupings a player could defend, and without it
        # "unique" would mean only "we did not look".
        "memberships": memberships,
        "visible": [
            {
                "category": group.shared_category,
                "category_id": group.shared_category_id,
                "members": [m.entity_id for m in group.members],
                "names": [m.name for m in group.members],
            }
            for group in plan.visible
        ],
        "hidden": {
            "category": plan.hidden.shared_category,
            "category_id": plan.hidden.shared_category_id,
            "secondary_category": plan.hidden.secondary_category,
            "members": [m.entity_id for m in plan.hidden.members],
            "names": [m.name for m in plan.hidden.members],
        },
        "borrowings": [list(pair) for pair in plan.borrowings],
        "temptation": plan.temptation,
        "enriched": plan.enriched,
    }


def rationale_for(plan: BoardPlan) -> str:
    spread = sorted({index for index, _ in plan.borrowings})
    return (
        f"hidden {plan.hidden.shared_category!r} across {len(spread)} groups, "
        f"temptation {plan.temptation}"
        + (", two-axis hidden group" if plan.enriched else "")
    )


def generate_candidates(context: GenerationContext) -> Sequence[PuzzleCandidate]:
    """Offer as many boards as the budget allows, best first.

    "Best" is ordered rather than filtered: a candidate this module likes can
    still fail verification, and one it likes less can be the only one that
    survives. Ordering costs nothing and throwing away costs a day.
    """
    # Cleared first, so a run that returns early cannot leave the previous
    # day's refusals to be reported as its own.
    _LAST_REJECTIONS.clear()
    candidates: list[PuzzleCandidate] = []
    rejected: dict[str, int] = {}
    for group_size in descriptor_module.group_sizes_for(context.day_key):
        candidates = _candidates_at(context, group_size, rejected)
        if candidates:
            break
    _LAST_REJECTIONS.clear()
    _LAST_REJECTIONS.update(rejected)
    # A tuple, as the protocol's return type says: the sequence a plugin hands
    # back is not the engine's to mutate.
    return tuple(_ordered(candidates))


def _candidates_at(
    context: GenerationContext, group_size: int, rejected: dict[str, int]
) -> list[PuzzleCandidate]:
    """Boards at one size, from that size's own content.

    Refusals accumulate across sizes into one mapping, keyed by size, because
    "nothing worked" is only diagnosable if each size says what stopped it.
    """
    visible_result: ContentResult | None = context.content.get(
        content_module.sized(content_module.VISIBLE, group_size)
    )
    if visible_result is None or not visible_result.groups:
        rejected[f"size {group_size}: no visible groups"] = 1
        return []

    pool = list(visible_result.groups)
    candidates: list[PuzzleCandidate] = []
    seen: set[str] = set()

    # Every hidden word must be a tile in at least one visible group, or it can
    # never be on the board. Checked once here rather than discovered inside a
    # combination search, where it shows up as thousands of identical
    # "shortlist too small" refusals that say nothing about which word was
    # missing.
    tiles: frozenset[str] = frozenset().union(*(member_ids(g) for g in pool))
    placeable = []
    for hidden, enriched in hidden_candidates(context, group_size):
        if len(hidden.members) != group_size:
            # The hidden group is exactly as large as a visible one, so it
            # cannot be identified by counting.
            continue
        if not member_ids(hidden) <= tiles:
            key = f"size {group_size}: hidden_word_on_no_visible_group"
            rejected[key] = rejected.get(key, 0) + 1
            continue
        placeable.append((hidden, enriched))
    budget = max(1, COMBINATION_BUDGET // max(1, len(placeable)))
    at_size: dict[str, int] = {}

    for hidden, enriched in placeable:
        for plan in plans_for(
            hidden,
            pool,
            group_size,
            enriched=enriched,
            budget=budget,
            rejected=at_size,
        ):
            candidate_id = plan.candidate_id()
            if candidate_id in seen:
                continue
            seen.add(candidate_id)
            candidates.append(
                PuzzleCandidate(
                    candidate_id=candidate_id,
                    payload=payload_for(plan),
                    fact_refs=plan.dependency_ids(),
                    rationale=rationale_for(plan),
                )
            )
            if len(candidates) >= context.candidate_budget:
                return candidates
    for reason, count in at_size.items():
        rejected[f"size {group_size}: {reason}"] = count
    return candidates


#: Why the last generation run found nothing. Module state, and deliberately
#: not part of any return value: the plugin protocol has no channel for it,
#: and inventing one would put a diagnostic in the contract every game then
#: has to implement. Read by ``unusable_reason`` and by nothing else.
_LAST_REJECTIONS: dict[str, int] = {}


def last_rejections() -> dict[str, int]:
    return dict(_LAST_REJECTIONS)


def _ordered(candidates: list[PuzzleCandidate]) -> list[PuzzleCandidate]:
    """Two-axis hidden groups first, then more tempting boards.

    Ties broken by candidate id so two runs offer the same list in the same
    order, which the engine's day-level reproducibility depends on.
    """
    return sorted(
        candidates,
        key=lambda c: (
            not c.payload.get("enriched", False),
            -int(c.payload.get("temptation", 0)),
            c.candidate_id,
        ),
    )


def unusable_reason(context: GenerationContext) -> str | None:
    """Why today produced nothing, in words a curator can act on.

    The content result already counts what each gate rejected; this says which
    of the game's own requirements went unmet, which is the part no engine gate
    can know.
    """
    # Reported for the day's own size. The fallbacks are visible in the
    # refusal counts, which are keyed by size; repeating this narrative five
    # times would bury the day's actual shape.
    group_size = group_size_for(context.day_key)
    visible = context.content.get(content_module.sized(content_module.VISIBLE, group_size))
    if visible is None or not visible.groups:
        return f"no visible groups returned at size {group_size}"
    sized = [g for g in visible.groups if len(g.members) == group_size]
    if len(sized) < VISIBLE_GROUPS:
        return (
            f"{len(sized)} visible groups of size {group_size}, need "
            f"{VISIBLE_GROUPS}"
        )
    hidden = hidden_candidates(context, group_size)
    if not hidden:
        return f"no overlay groups returned at size {group_size}"
    if not any(len(group.members) == group_size for group, _ in hidden):
        return f"no overlay group has exactly {group_size} members"

    counts = last_rejections()
    if counts:
        # The content was there and every combination of it was refused, so
        # the useful answer is which constraint did the refusing.
        cause, count = max(counts.items(), key=lambda pair: (pair[1], pair[0]))
        total = sum(counts.values())
        detail = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        return (
            f"{total} combinations refused, mostly {cause} ({count}); {detail}"
        )
    return None


def _unused(rng: DeterministicRng) -> None:  # pragma: no cover - documentation
    """Generation draws no randomness.

    The day's shape is already decided by the day key, and the choice among
    candidates belongs to the engine, which verifies them in order. A generator
    that also sampled would make two sources of randomness responsible for one
    board.
    """


__all__ = [
    "COMBINATION_BUDGET",
    "MAXIMUM_TEMPTATION",
    "MINIMUM_TEMPTATION",
    "VISIBLE_SHORTLIST",
    "BoardPlan",
    "ancestry",
    "borrowings_for",
    "generate_candidates",
    "has_no_cross_membership",
    "hidden_candidates",
    "is_disjoint",
    "member_ids",
    "payload_for",
    "plans_for",
    "shortlist_for",
    "temptation_of",
    "unusable_reason",
]
