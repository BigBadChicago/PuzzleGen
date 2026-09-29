#!/usr/bin/env python3
"""Whether the graph can actually produce a board, and what would fix it.

The handoff's coverage table counted lexical categories holding at least
``group_size`` members and at least one overlay member. That is a property of
one category; the generator's precondition is a property of a *set* of four
plus a specific hidden group, so the count can rise while the number of
generatable boards stays at zero. This module measures the precondition
itself, exactly as ``puzzlegen/games/grouping/generate.py`` states it:

* four visible groups, each exactly ``group_size`` members drawn from one
  lexical category (``_group_combinations`` groups by *direct* membership)
* pairwise disjoint members (``is_disjoint``)
* no tile carrying another chosen group's defining category, ancestors
  included, because ``has_no_cross_membership`` reads ``type_ids`` and an
  ``EntityView``'s types are direct membership plus inherited ancestry
* one overlay category supplying ``group_size`` members, every one of them on
  the board, and every visible group giving up at least one
  (``borrowings_for``)

What this does not apply is the similarity, frequency-spread, confidence and
freshness gates the day's queries carry. Those can only ever remove boards, so
a structural count is an upper bound: zero here cannot be fixed by loosening a
threshold, and a positive number still has to survive the real query.

Imported by ``tools/measure_coverage.py`` and ``tools/propose_overlay.py`` by
putting ``tools/`` on the path. ``tools/`` is deliberately not a package.
"""

from __future__ import annotations

import itertools
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from puzzlegen.core.types import ReviewStatus
from puzzlegen.games.grouping.content import LEXICAL_TAXONOMY, OVERLAY_TAXONOMY
from puzzlegen.games.grouping.descriptor import GROUP_SIZES, VISIBLE_GROUPS
from puzzlegen.graph import GraphRepositories

MEMBERSHIP_PREDICATE = "is_a"

#: Quadruples examined per hidden group before the search reports itself
#: bounded. The engine's own solvers report an honest ``exhausted`` flag
#: rather than a count dressed up as exact; a measurement that guessed would
#: be worse than one that says it stopped looking.
DEFAULT_BUDGET = 200_000

# -- why a hidden group could not become a board ------------------------------

TOO_FEW_HIDDEN_MEMBERS = "too_few_hidden_members"
MEMBERS_WITHOUT_HOME = "members_without_home"
TOO_FEW_DISTINCT_HOMES = "too_few_distinct_homes"
NO_VALID_QUADRUPLE = "no_valid_quadruple"
BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Everything the precondition reads, loaded once.

    ``lexical_members`` is direct membership because that is what a group can
    be drawn from; ``types_of`` is direct plus ancestry because that is what
    the cross-membership gate compares against. Conflating the two would make
    a category look larger than any group it can actually supply.
    """

    lexical_members: dict[str, frozenset[str]]
    lexical_names: dict[str, str]
    overlay_members: dict[str, frozenset[str]]
    overlay_names: dict[str, str]
    types_of: dict[str, frozenset[str]]
    entity_names: dict[str, str]

    def usable_lexical(self, group_size: int) -> list[str]:
        """Categories large enough to supply one visible group."""
        return sorted(
            cid
            for cid, members in self.lexical_members.items()
            if len(members) >= group_size
        )

    def name_of(self, identifier: str) -> str:
        return (
            self.lexical_names.get(identifier)
            or self.overlay_names.get(identifier)
            or self.entity_names.get(identifier)
            or identifier
        )

    def with_membership(self, entity_id: str, category_id: str) -> Snapshot:
        """This snapshot plus one overlay membership, for asking "what if".

        Only the overlay side changes. An overlay membership never moves an
        entity off its lexical shelf, which is the whole point of the
        primary-plus-crosscutting pattern, so the lexical structure is shared
        rather than copied.
        """
        overlay = dict(self.overlay_members)
        overlay[category_id] = overlay.get(category_id, frozenset()) | {entity_id}
        return Snapshot(
            lexical_members=self.lexical_members,
            lexical_names=self.lexical_names,
            overlay_members=overlay,
            overlay_names=self.overlay_names,
            types_of=self.types_of,
            entity_names=self.entity_names,
        )


@dataclass(frozen=True, slots=True)
class Finding:
    """One hidden group at one group size."""

    group_size: int
    category_id: str
    category_name: str
    feasible: bool
    exhausted: bool
    examined: int
    reason: str | None = None
    #: Hidden members sitting in no lexical category large enough to be a
    #: visible group. These are the words a proposer run has to displace.
    homeless: tuple[str, ...] = ()
    #: Distinct lexical categories the members do reach.
    homes: tuple[str, ...] = ()
    quadruple: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CoverageGain:
    """What accepting one proposed membership would do to feasibility."""

    entity_id: str
    category_id: str
    #: Usable lexical categories this entity would newly represent for that
    #: overlay category, by smallest group size at which they are usable.
    new_homes: tuple[str, ...] = ()
    #: Group sizes whose feasibility flips from false to true. Empty until an
    #: exact check runs, which is why ``checked`` is reported separately: an
    #: unchecked zero and a checked zero are different facts.
    unblocks: tuple[int, ...] = ()
    checked: bool = False

    @property
    def rank(self) -> tuple:
        """Sort key, best first. Negated so a plain ascending sort works."""
        return (-len(self.unblocks), -len(self.new_homes), self.entity_id)


@dataclass
class Report:
    group_sizes: tuple[int, ...]
    findings: list[Finding] = field(default_factory=list)

    def at(self, group_size: int) -> list[Finding]:
        return [f for f in self.findings if f.group_size == group_size]

    def feasible_at(self, group_size: int) -> list[Finding]:
        return [f for f in self.at(group_size) if f.feasible]

    def any_feasible(self) -> bool:
        return any(f.feasible for f in self.findings)


# -- loading ------------------------------------------------------------------


def _ancestors(parents: dict[str, tuple[str, ...]], start: str) -> set[str]:
    """Every category above ``start``, by breadth-first walk.

    No cycle guard, deliberately: ``CategoryRepository.put`` refuses a parent
    that does not already exist, so a cycle would need a category to predate
    its own ancestor. A guard here would imply the store's invariant is not
    trusted, and a guard that is never exercised is a guard nobody maintains.
    """
    found: set[str] = set()
    frontier = list(parents.get(start, ()))
    while frontier:
        current = frontier.pop()
        if current in found:
            continue
        found.add(current)
        frontier.extend(parents.get(current, ()))
    return found


def load(
    repos: GraphRepositories,
    *,
    lexical_taxonomy: str = LEXICAL_TAXONOMY,
    overlay_taxonomy: str = OVERLAY_TAXONOMY,
) -> Snapshot:
    """Read the active graph into the shape the precondition needs."""
    active_entities = {e.id: e.canonical_name for e in repos.entities.active()}

    lexical = [
        c
        for c in repos.categories.live(lexical_taxonomy)
        if c.status is ReviewStatus.ACTIVE
    ]
    overlay = [
        c
        for c in repos.categories.live(overlay_taxonomy)
        if c.status is ReviewStatus.ACTIVE
    ]

    parents = {c.id: tuple(c.parent_ids) for c in lexical}
    ancestry = {c.id: _ancestors(parents, c.id) for c in lexical}

    lexical_members: dict[str, frozenset[str]] = {}
    types_of: dict[str, set[str]] = {}
    for category in lexical:
        members = {
            rel.subject_id
            for rel in repos.relationships.by_object(
                category.id, MEMBERSHIP_PREDICATE
            )
            if rel.status is ReviewStatus.ACTIVE and rel.subject_id in active_entities
        }
        lexical_members[category.id] = frozenset(members)
        for entity_id in members:
            types_of.setdefault(entity_id, set()).add(category.id)
            types_of[entity_id] |= ancestry[category.id]

    overlay_members: dict[str, frozenset[str]] = {}
    for category in overlay:
        overlay_members[category.id] = frozenset(
            rel.subject_id
            for rel in repos.relationships.by_object(
                category.id, MEMBERSHIP_PREDICATE
            )
            if rel.status is ReviewStatus.ACTIVE and rel.subject_id in active_entities
        )

    return Snapshot(
        lexical_members=lexical_members,
        lexical_names={c.id: c.canonical_name for c in lexical},
        overlay_members=overlay_members,
        overlay_names={c.id: c.canonical_name for c in overlay},
        types_of={k: frozenset(v) for k, v in types_of.items()},
        entity_names=active_entities,
    )


# -- the precondition ---------------------------------------------------------


def _can_fill(eligible: list[set[str]], needed: list[int]) -> bool:
    """Can each group take ``needed`` members from ``eligible``, disjointly.

    A bipartite matching rather than a greedy fill: greedy takes an entity
    that two groups could use and strands the one that had no alternative,
    which reports an infeasible board for a board that exists. Slots are
    expanded because a group needs several entities and an entity serves one
    group, and 36 slots is the widest board the game supports.
    """
    slots = [index for index, count in enumerate(needed) for _ in range(count)]
    taken: dict[str, int] = {}

    def assign(slot_index: int, visited: set[str]) -> bool:
        group = slots[slot_index]
        for entity_id in sorted(eligible[group]):
            if entity_id in visited:
                continue
            visited.add(entity_id)
            holder = taken.get(entity_id)
            if holder is None or assign(holder, visited):
                taken[entity_id] = slot_index
                return True
        return False

    return all(assign(index, set()) for index in range(len(slots)))


def _quadruple_works(
    snapshot: Snapshot,
    categories: tuple[str, ...],
    hidden: frozenset[str],
    group_size: int,
) -> bool:
    chosen = set(categories)
    home_of: dict[str, str] = {}
    for member in hidden:
        owning = [c for c in categories if member in snapshot.lexical_members[c]]
        # Exactly one: none means the tile is not on the board, and two means
        # the tile belongs to a second chosen group's category, which is the
        # cross-membership the assembler refuses.
        if len(owning) != 1:
            return False
        home_of[member] = owning[0]

    if len(set(home_of.values())) != len(categories):
        return False

    for member, home in home_of.items():
        if (snapshot.types_of.get(member, frozenset()) & chosen) - {home}:
            return False

    borrowed = Counter(home_of.values())
    eligible: list[set[str]] = []
    needed: list[int] = []
    for category in categories:
        shortfall = group_size - borrowed[category]
        if shortfall < 0:
            return False
        eligible.append(
            {
                entity_id
                for entity_id in snapshot.lexical_members[category]
                if entity_id not in home_of
                and not (
                    (snapshot.types_of.get(entity_id, frozenset()) & chosen)
                    - {category}
                )
            }
        )
        needed.append(shortfall)

    return _can_fill(eligible, needed)


def assess(
    snapshot: Snapshot,
    category_id: str,
    group_size: int,
    *,
    budget: int = DEFAULT_BUDGET,
) -> Finding:
    """Can this overlay category be a hidden group at this size, today."""
    members = snapshot.overlay_members.get(category_id, frozenset())
    name = snapshot.overlay_names.get(category_id, category_id)
    usable = set(snapshot.usable_lexical(group_size))

    homes_of = {
        member: sorted(
            cid
            for cid in usable
            if member in snapshot.lexical_members[cid]
        )
        for member in sorted(members)
    }
    reachable = sorted({cid for homes in homes_of.values() for cid in homes})
    homeless = tuple(member for member, homes in homes_of.items() if not homes)

    if len(members) < group_size:
        return Finding(
            group_size=group_size,
            category_id=category_id,
            category_name=name,
            feasible=False,
            exhausted=True,
            examined=0,
            reason=TOO_FEW_HIDDEN_MEMBERS,
            homeless=homeless,
            homes=tuple(reachable),
        )

    placeable = sorted(member for member, homes in homes_of.items() if homes)
    if len(placeable) < group_size:
        return Finding(
            group_size=group_size,
            category_id=category_id,
            category_name=name,
            feasible=False,
            exhausted=True,
            examined=0,
            reason=MEMBERS_WITHOUT_HOME,
            homeless=homeless,
            homes=tuple(reachable),
        )

    if len(reachable) < VISIBLE_GROUPS:
        return Finding(
            group_size=group_size,
            category_id=category_id,
            category_name=name,
            feasible=False,
            exhausted=True,
            examined=0,
            reason=TOO_FEW_DISTINCT_HOMES,
            homeless=homeless,
            homes=tuple(reachable),
        )

    examined = 0
    exhausted = True
    for subset in itertools.combinations(placeable, group_size):
        hidden = frozenset(subset)
        candidates = sorted(
            {cid for member in hidden for cid in homes_of[member]}
        )
        if len(candidates) < VISIBLE_GROUPS:
            continue
        for quadruple in itertools.combinations(candidates, VISIBLE_GROUPS):
            if examined >= budget:
                exhausted = False
                break
            examined += 1
            if _quadruple_works(snapshot, quadruple, hidden, group_size):
                return Finding(
                    group_size=group_size,
                    category_id=category_id,
                    category_name=name,
                    feasible=True,
                    exhausted=True,
                    examined=examined,
                    homeless=homeless,
                    homes=tuple(reachable),
                    quadruple=quadruple,
                )
        if not exhausted:
            break

    return Finding(
        group_size=group_size,
        category_id=category_id,
        category_name=name,
        feasible=False,
        exhausted=exhausted,
        examined=examined,
        reason=NO_VALID_QUADRUPLE if exhausted else BUDGET_EXHAUSTED,
        homeless=homeless,
        homes=tuple(reachable),
    )


def survey(
    snapshot: Snapshot,
    *,
    group_sizes: tuple[int, ...] = GROUP_SIZES,
    budget: int = DEFAULT_BUDGET,
) -> Report:
    """Every overlay category against every supported group size."""
    report = Report(group_sizes=group_sizes)
    for group_size in group_sizes:
        for category_id in sorted(snapshot.overlay_members):
            report.findings.append(
                assess(snapshot, category_id, group_size, budget=budget)
            )
    return report


# -- what would fix it --------------------------------------------------------


def distinct_homes(
    snapshot: Snapshot, category_id: str, group_size: int
) -> set[str]:
    """Usable lexical categories this overlay category already reaches."""
    members = snapshot.overlay_members.get(category_id, frozenset())
    return {
        cid
        for cid in snapshot.usable_lexical(group_size)
        if snapshot.lexical_members[cid] & members
    }


def gain_of(
    snapshot: Snapshot,
    entity_id: str,
    category_id: str,
    *,
    group_sizes: tuple[int, ...] = GROUP_SIZES,
    exact: bool = False,
    budget: int = DEFAULT_BUDGET,
) -> CoverageGain:
    """What accepting ``entity_id`` into ``category_id`` would buy.

    The cheap half counts lexical categories the overlay category would newly
    reach, which is the quantity the two commonest blockers are made of. The
    exact half re-runs the precondition and reports which group sizes actually
    flip, and is off by default because it is a search per candidate and a
    proposer run scores thousands.
    """
    smallest = min(group_sizes)
    already = distinct_homes(snapshot, category_id, smallest)
    new_homes = tuple(
        sorted(
            cid
            for cid in snapshot.usable_lexical(smallest)
            if entity_id in snapshot.lexical_members[cid] and cid not in already
        )
    )
    if not exact:
        return CoverageGain(
            entity_id=entity_id,
            category_id=category_id,
            new_homes=new_homes,
        )

    after = snapshot.with_membership(entity_id, category_id)
    unblocks = tuple(
        size
        for size in group_sizes
        if not assess(snapshot, category_id, size, budget=budget).feasible
        and assess(after, category_id, size, budget=budget).feasible
    )
    return CoverageGain(
        entity_id=entity_id,
        category_id=category_id,
        new_homes=new_homes,
        unblocks=unblocks,
        checked=True,
    )


__all__ = [
    "BUDGET_EXHAUSTED",
    "CoverageGain",
    "DEFAULT_BUDGET",
    "Finding",
    "MEMBERS_WITHOUT_HOME",
    "NO_VALID_QUADRUPLE",
    "Report",
    "Snapshot",
    "TOO_FEW_DISTINCT_HOMES",
    "TOO_FEW_HIDDEN_MEMBERS",
    "assess",
    "distinct_homes",
    "gain_of",
    "load",
    "survey",
]
