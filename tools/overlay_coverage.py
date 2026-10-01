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
* the four groups sharing at least ``MINIMUM_TEMPTATION`` categories of
  ancestry beyond the ones that define them (``temptation_of``). Measured here
  as what each group's members all share, a lower bound on the real figure,
  which also counts categories only some members carry.

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

from puzzlegen.content.representatives import choose_representatives
from puzzlegen.core.types import ReviewStatus
from puzzlegen.games.grouping.content import LEXICAL_TAXONOMY, OVERLAY_TAXONOMY
from puzzlegen.games.grouping.descriptor import GROUP_SIZES, VISIBLE_GROUPS
from puzzlegen.games.grouping.generate import MINIMUM_TEMPTATION
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
#: Boards exist except that the four groups share no ancestry. The game
#: requires some, because four unrelated piles sort themselves, and separate
#: export roots share none, so a board has to live inside one domain.
NO_SHARED_DOMAIN = "no_shared_domain"
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
    #: What ``explain_homeless`` needs to say why a word has no home. Optional
    #: so a snapshot built by hand for a test still works without them.
    direct_categories: dict[str, tuple[str, ...]] = field(default_factory=dict)
    representative: dict[str, str] = field(default_factory=dict)
    parents: dict[str, tuple[str, ...]] = field(default_factory=dict)
    grouping: str = "shared_category"
    #: Direct members per lexical category, before any sibling regrouping. Kept
    #: because tagging a word can change which word stands for its child, and
    #: a what-if has to rebuild the groups from these rather than reuse them.
    direct_members: dict[str, frozenset[str]] = field(default_factory=dict)
    #: Frequency band name per entity, None when unscored. The game applies a
    #: band filter to every tile, and a coverage model that ignores it reports
    #: boards the game can never build.
    bands: dict[str, str | None] = field(default_factory=dict)
    #: Kinds each parent would have with no frequency filter, so a parent that
    #: shrank can be told from one WordNet made small.
    kinds_before_filter: dict[str, int] = field(default_factory=dict)
    #: Bands a tile may have, or None for no filter.
    allowed_bands: frozenset[str] | None = None
    difficulty: str = "any"

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
        lexical_members, representative = self.lexical_members, self.representative
        if self.grouping == "siblings" and self.direct_members:
            # Tagging a word can change which word stands for its child, so the
            # groups are rebuilt from the direct members instead of shared.
            lexical_members, representative = _sibling_members(
                self.lexical_names,
                self.parents,
                self.direct_members,
                self.entity_names,
                _carrying(overlay),
            )
        return Snapshot(
            lexical_members=lexical_members,
            lexical_names=self.lexical_names,
            overlay_members=overlay,
            overlay_names=self.overlay_names,
            types_of=self.types_of,
            entity_names=self.entity_names,
            direct_categories=self.direct_categories,
            representative=representative,
            parents=self.parents,
            grouping=self.grouping,
            direct_members=self.direct_members,
            bands=self.bands,
            kinds_before_filter=self.kinds_before_filter,
            allowed_bands=self.allowed_bands,
            difficulty=self.difficulty,
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
    #: For a failed search: which check ended each choice of four groups, most
    #: common first. Without it "no valid quadruple" says only that something
    #: failed, and a curator cannot tell content from structure.
    failures: tuple[tuple[str, int], ...] = ()
    #: One concrete instance per cause, so "in two chosen groups" arrives with
    #: the word and the two parents rather than as a bare count.
    examples: tuple[tuple[str, str], ...] = ()


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


SHARED_CATEGORY = "shared_category"
SIBLINGS = "siblings"


def load(
    repos: GraphRepositories,
    *,
    lexical_taxonomy: str = LEXICAL_TAXONOMY,
    overlay_taxonomy: str = OVERLAY_TAXONOMY,
    grouping: str = SHARED_CATEGORY,
    allowed_bands: frozenset[str] | None = None,
    difficulty: str = "any",
) -> Snapshot:
    """Read the active graph into the shape the precondition needs.

    ``grouping`` is how the game under measurement forms a group, and it
    decides what a "lexical group" is here. Under ``shared_category`` a group
    is drawn from one category's direct members. Under ``siblings`` a group is
    drawn from a parent, and its members are the representatives of that
    parent's children, one entity per child, the one carrying the child's own
    name. Everything downstream (homes, matching, cross-membership) is the
    same question asked of a different set of sets, which is why the mode
    changes only what ``lexical_members`` holds.
    """
    entities = list(repos.entities.active())
    active_entities = {e.id: e.canonical_name for e in entities}
    bands = {
        e.id: (e.frequency_band.name if e.frequency_band is not None else None)
        for e in entities
    }

    def usable_as_tile(entity_id: str) -> bool:
        """The game's frequency filter: an unscored word is never a tile."""
        return allowed_bands is None or bands.get(entity_id) in allowed_bands


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
    in_lexicon: dict[str, list[str]] = {}
    all_direct: dict[str, frozenset[str]] = {}
    for category in lexical:
        everyone = {
            rel.subject_id
            for rel in repos.relationships.by_object(
                category.id, MEMBERSHIP_PREDICATE
            )
            if rel.status is ReviewStatus.ACTIVE and rel.subject_id in active_entities
        }
        all_direct[category.id] = frozenset(everyone)
        for entity_id in everyone:
            in_lexicon.setdefault(entity_id, []).append(category.id)
        members = {e for e in everyone if usable_as_tile(e)}
        lexical_members[category.id] = frozenset(members)
        for entity_id in members:
            types_of.setdefault(entity_id, set()).add(category.id)
            types_of[entity_id] |= ancestry[category.id]

    # Unfiltered on purpose: explaining why a word has no home needs to know
    # the lexicon has it, so that "too rare" can be told from "not there".
    direct_categories = in_lexicon

    representative: dict[str, str] = {}
    direct_members = dict(lexical_members)
    if grouping not in (SIBLINGS, SHARED_CATEGORY):
        raise ValueError(f"unknown grouping {grouping!r}")

    overlay_members: dict[str, frozenset[str]] = {}
    for category in overlay:
        overlay_members[category.id] = frozenset(
            rel.subject_id
            for rel in repos.relationships.by_object(
                category.id, MEMBERSHIP_PREDICATE
            )
            if rel.status is ReviewStatus.ACTIVE and rel.subject_id in active_entities
        )

    lexical_names = {c.id: c.canonical_name for c in lexical}
    parents = {c.id: tuple(c.parent_ids) for c in lexical}
    kinds_before: dict[str, int] = {}
    if grouping == SIBLINGS and allowed_bands is not None:
        unfiltered, _ = _sibling_members(
            lexical_names, parents, all_direct, active_entities, _carrying(overlay_members)
        )
        kinds_before = {pid: len(found) for pid, found in unfiltered.items()}
    if grouping == SIBLINGS:
        lexical_members, representative = _sibling_members(
            lexical_names, parents, direct_members, active_entities, _carrying(overlay_members)
        )

    return Snapshot(
        lexical_members=lexical_members,
        lexical_names=lexical_names,
        overlay_members=overlay_members,
        overlay_names={c.id: c.canonical_name for c in overlay},
        types_of={k: frozenset(v) for k, v in types_of.items()},
        entity_names=active_entities,
        direct_categories={k: tuple(sorted(v)) for k, v in direct_categories.items()},
        representative=representative,
        parents=parents,
        grouping=grouping,
        direct_members=direct_members,
        bands=bands,
        kinds_before_filter=kinds_before,
        allowed_bands=allowed_bands,
        difficulty=difficulty,
    )


def _carrying(overlay_members: dict[str, frozenset[str]]) -> frozenset[str]:
    """Every entity tagged in any overlay category."""
    return frozenset().union(*overlay_members.values()) if overlay_members else frozenset()


def _sibling_members(
    names: dict[str, str],
    parents: dict[str, tuple[str, ...]],
    direct: dict[str, frozenset[str]],
    entity_names: dict[str, str],
    carrying: frozenset[str],
) -> tuple[dict[str, frozenset[str]], dict[str, str]]:
    """Parent to one representative entity per child, and who represents whom.

    The choice is ``choose_representatives``, the same function the content
    service calls, so the coverage numbers describe the tiles the game would
    actually show. A child with no eligible word is not offered.
    """
    members_of = {
        category_id: [
            (entity_id, entity_names[entity_id])
            for entity_id in sorted(entity_ids)
            if entity_id in entity_names
        ]
        for category_id, entity_ids in direct.items()
    }
    representative = choose_representatives(names, members_of, carrying)

    grouped: dict[str, set[str]] = {}
    for category_id, entity_id in representative.items():
        for parent_id in parents.get(category_id, ()):
            if parent_id in names:
                grouped.setdefault(parent_id, set()).add(entity_id)
    return {pid: frozenset(found) for pid, found in grouped.items()}, representative


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


OK = "ok"
NEEDS_DOMAIN = "needs_domain"

#: Why one choice of four groups was not a board: the first check that failed.
OFF_BOARD = "hidden_member_on_no_chosen_group"
IN_TWO_GROUPS = "hidden_member_in_two_chosen_groups"
UNUSED_GROUP = "a_group_gives_up_no_hidden_member"
CROSS_MEMBERSHIP = "hidden_member_also_in_another_chosen_parent"
OVER_BORROWED = "a_group_holds_too_many_hidden_members"
CANNOT_FILL = "too_few_other_tiles_to_fill_the_groups"


def _shared_ancestry(snapshot: Snapshot, category_id: str) -> frozenset[str]:
    """Everything every member of a group has in common, defining category
    and its ancestors included."""
    common: frozenset[str] | None = None
    for member in snapshot.lexical_members[category_id]:
        types = snapshot.types_of.get(member, frozenset())
        common = types if common is None else common & types
    return frozenset(common or ())


def _quadruple_works(
    snapshot: Snapshot,
    categories: tuple[str, ...],
    hidden: frozenset[str],
    group_size: int,
    ancestry: dict[str, frozenset[str]],
) -> tuple[str, str]:
    chosen = set(categories)
    home_of: dict[str, str] = {}

    def word(entity_id: str) -> str:
        return snapshot.entity_names.get(entity_id, entity_id)

    def named(category_ids) -> str:
        return " and ".join(sorted(snapshot.lexical_names.get(c, c) for c in category_ids))

    # Every word is looked at before any verdict, and in sorted order. A set
    # iterates in an order that changes from one run to the next, and the first
    # problem found used to win, so one board could be reported as leaving a
    # word out on one run and as a word in two groups on the next.
    owners = {
        member: [c for c in categories if member in snapshot.lexical_members[c]]
        for member in sorted(hidden)
    }
    if any(not owning for owning in owners.values()):
        return OFF_BOARD, ""
    for member, owning in owners.items():
        if len(owning) > 1:
            return IN_TWO_GROUPS, f"{word(member)} is a kind of both {named(owning)}"
    home_of = {member: owning[0] for member, owning in owners.items()}

    if len(set(home_of.values())) != len(categories):
        idle = [c for c in categories if c not in set(home_of.values())]
        return UNUSED_GROUP, f"no hidden word sits in {named(idle)}"

    for member, home in sorted(home_of.items()):
        clash = (snapshot.types_of.get(member, frozenset()) & chosen) - {home}
        if clash:
            return CROSS_MEMBERSHIP, (
                f"{word(member)} is under {named(clash)} as well as "
                f"{snapshot.lexical_names.get(home, home)}"
            )

    borrowed = Counter(home_of.values())
    eligible: list[set[str]] = []
    needed: list[int] = []
    for category in categories:
        shortfall = group_size - borrowed[category]
        if shortfall < 0:
            return OVER_BORROWED, (
                f"{snapshot.lexical_names.get(category, category)} would hold "
                f"{borrowed[category]} hidden words but has {group_size} tiles"
            )
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

    if not _can_fill(eligible, needed):
        return CANNOT_FILL, ""

    resemblance = sum(
        len((ancestry[left] & ancestry[right]) - chosen)
        for left, right in itertools.combinations(categories, 2)
    )
    return (OK if resemblance >= MINIMUM_TEMPTATION else NEEDS_DOMAIN), ""


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

    ancestry = {cid: _shared_ancestry(snapshot, cid) for cid in reachable}
    examined = 0
    exhausted = True
    domain_blocked = False
    causes: Counter[str] = Counter()
    examples: dict[str, str] = {}
    uncovered = 0
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
            verdict, detail = _quadruple_works(
                snapshot, quadruple, hidden, group_size, ancestry
            )
            if verdict == NEEDS_DOMAIN:
                domain_blocked = True
            elif verdict == OFF_BOARD:
                # Not a cause, only a choice of four that left a hidden word
                # out. With five homes on offer most choices do, and counting
                # them buried the checks that actually said something.
                uncovered += 1
            elif verdict != OK:
                causes[verdict] += 1
                examples.setdefault(verdict, detail)
            if verdict == OK:
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
        reason=(
            (NO_SHARED_DOMAIN if domain_blocked else NO_VALID_QUADRUPLE)
            if exhausted
            else BUDGET_EXHAUSTED
        ),
        homeless=homeless,
        homes=tuple(reachable),
        failures=(
            tuple(sorted(causes.items(), key=lambda pair: (-pair[1], pair[0])))
            if causes
            else (((OFF_BOARD, uncovered),) if uncovered else ())
        ),
        examples=tuple(sorted(examples.items())),
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


def explain_homeless(snapshot: Snapshot, entity_id: str, group_size: int) -> str:
    """Why this word is not a tile anywhere, in a sentence a curator can act on.

    The three causes need three different fixes, and the survey alone shows
    only that a word has no home. A word the lexicon lacks needs a different
    word or a different export. A word that is a synonym of another child's
    name is never a tile under sibling grouping, so the seed should use the
    word that stands for it. A word whose parent has too few kinds needs a
    bigger parent, not a different word.
    """
    categories = snapshot.direct_categories.get(entity_id, ())
    if not categories:
        return "not in the lexicon, so only the overlay knows this word"
    if snapshot.allowed_bands is not None and snapshot.bands.get(entity_id) not in snapshot.allowed_bands:
        band = snapshot.bands.get(entity_id) or "unscored"
        allowed = ", ".join(sorted(snapshot.allowed_bands))
        return (
            f"too uncommon for {snapshot.difficulty}: its frequency band is "
            f"{band} and only {allowed} can be tiles"
        )

    reasons: list[str] = []
    for category_id in categories:
        name = snapshot.lexical_names.get(category_id, category_id)
        if snapshot.grouping != SIBLINGS:
            size = len(snapshot.lexical_members.get(category_id, ()))
            reasons.append(f"in {name}, which has {size} members and needs {group_size}")
            continue

        stands_for = snapshot.representative.get(category_id)
        if stands_for is None:
            reasons.append(f"in {name}, which has no word carrying its own name")
        elif stands_for != entity_id:
            other = snapshot.entity_names.get(stands_for, stands_for)
            reasons.append(f"a synonym of {other}, which is the word that stands for {name}")
        else:
            parent_ids = snapshot.parents.get(category_id, ())
            if not parent_ids:
                reasons.append(f"{name} has no parent, so it is the top of its tree")
                continue
            kinds, best = max(
                (len(snapshot.lexical_members.get(p, ())), p) for p in parent_ids
            )
            parent_names = ", ".join(snapshot.lexical_names.get(p, p) for p in parent_ids)
            before = snapshot.kinds_before_filter.get(best)
            # Said only when the filter actually removed some: a parent that
            # was always small is WordNet's doing, and one that shrank is
            # rarity's, and the two are fixed in different places.
            note = (
                f" ({before} before the frequency filter)"
                if before is not None and before > kinds
                else ""
            )
            reasons.append(
                f"a kind of {parent_names}, which has {kinds} kinds{note} and needs {group_size}"
            )
    return "; ".join(reasons)


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
    "NO_SHARED_DOMAIN",
    "NO_VALID_QUADRUPLE",
    "Report",
    "Snapshot",
    "TOO_FEW_DISTINCT_HOMES",
    "TOO_FEW_HIDDEN_MEMBERS",
    "assess",
    "distinct_homes",
    "explain_homeless",
    "gain_of",
    "load",
    "survey",
]
