"""Choosing a board with exactly one odd number out.

A candidate here is a claim that some numbers and one of them are a board: all
but that one share a property of the day's familiarity tier, and no *other*
tile is the odd one out under any property in the vocabulary. The claim is
cheap and most are discarded, which is the contract: filtering is the engine's
job, and a game that pre-filters on freshness, confidence or policy duplicates
a gate it cannot see the inputs to.

The construction is a draw rather than a search, like the figure game's, and
it is a draw with one check that cannot be designed away. Pick the property,
pick members of it, pick a number outside it, and the intended reading exists
by construction. What cannot be constructed is the *absence* of a second
reading: whether some other tile is the odd one out under some other property
is a fact about the whole board, and the only way to know it is to look. So
the gate is an enumeration, and it is the same enumeration the verifier runs.

That is a deliberate exception to the rule game three follows, where the
verifier re-derives independently what the generator computed. Here the
generator has nothing of its own to re-derive: the thing it needs to know is
exactly what the verifier computes, and a second implementation of it would be
two things to keep in step for no gain. What the verifier adds is that it runs
against the *assembled* board rather than the plan, which is where a mismatch
between what was chosen and what was laid out would show.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from puzzlegen.content.query import ContentResult, EntityView
from puzzlegen.core.hashing import short_hash
from puzzlegen.core.rng import DeterministicRng
from puzzlegen.engine.plugin import GenerationContext, PuzzleCandidate
from puzzlegen.symbols import SymbolIndex, SymbolPolicy

from . import content as content_module
from .descriptor import GAME_ID, board_sizes_for, tier_for
from .properties import (
    near_readings_of,
    obscurity_of,
    readings_of,
    tier_of,
    universal_in,
    vocabulary_of,
)

#: Why an attempt produced no board. Counted rather than logged, because an
#: empty day is a number problem: that forty attempts failed says nothing, and
#: that all forty failed for a second answer says exactly which constraint to
#: argue with.
NO_PROPERTY_AT_TIER = "no_property_at_tier"
TOO_FEW_MEMBERS = "too_few_members"
NO_OUTSIDER = "no_outsider"
SECOND_ANSWER = "second_answer"
NO_READING = "no_reading"
VALUE_MISSING = "value_missing"

#: How many boards to attempt per size.
#:
#: Sixty-four. More than the figure game's, because this construction can fail
#: and that one cannot: whether a draw leaves a second tile readable is a
#: property of the draw, and the thin properties leave little room. The
#: attempts are cheap -- each is one draw and one enumeration over at most
#: seven tiles and nine properties -- so the budget buys variety as well as
#: success.
ATTEMPTS_PER_SIZE = 64

#: The symbolic form policy this game reads its tiles under.
#:
#: The default. A number's name is its digits, which is one or two characters,
#: so the default policy admits it as its own glyph with no curation at all
#: and the ``spoken`` fact carries the words a reader says. That is the case
#: the subsystem's name-as-glyph fallback exists for.
SYMBOL_POLICY = SymbolPolicy()


@dataclass(frozen=True, slots=True)
class BoardPlan:
    """One board, its odd number and the evidence for the claim."""

    board_size: int
    #: Tiles in ascending value order, which is also board order. There is no
    #: shuffle in this game: see ``assemble``.
    tiles: tuple[EntityView, ...]
    #: The tile that is the odd one out.
    answer: str
    #: The property the other tiles share, which is the intended reading.
    property_name: str
    #: Every property that makes ``answer`` the odd one out. More than one is
    #: not ambiguity: it is one answer with several reasons, and it makes the
    #: board easier.
    reasons: tuple[str, ...]
    #: The vocabulary the uniqueness check scanned.
    vocabulary: tuple[str, ...]
    #: Properties every tile carries, which distinguish nothing.
    universal: tuple[str, ...]
    #: Properties true of all but exactly two tiles: what a solver discards.
    near: tuple[str, ...]
    #: Numeric values by tile id, for the spread the difficulty model reads.
    values: Mapping[str, int]
    memberships: Mapping[str, tuple[str, ...]]
    glyphs: Mapping[str, str]
    labels: Mapping[str, str]

    def candidate_id(self) -> str:
        """Identity of the board, from the tiles and the answer.

        Not the property: a board with the same tiles and the same odd one out
        is the same board however many properties pick it out, and offering it
        twice under two names would spend the engine's assembly budget on one
        puzzle.
        """
        return short_hash(
            [
                GAME_ID,
                str(self.board_size),
                *sorted(view.entity_id for view in self.tiles),
                "answer",
                self.answer,
            ],
            16,
        )

    def dependency_ids(self) -> tuple[str, ...]:
        found: list[str] = []
        for tile in self.tiles:
            found.extend(tile.dependency_ids())
        return tuple(dict.fromkeys(found))

    def spread(self) -> int:
        """The magnitude range the board covers."""
        if not self.values:
            return 0
        return max(self.values.values()) - min(self.values.values())


def value_of(entity: EntityView) -> int | None:
    """One entity's numeric value, or ``None`` when it carries none.

    Read from the fact rather than parsed from the name, for the reason the
    symbolic form subsystem gives: ``entity:7`` has a numeric id by accident
    of slugging, and the same number exported under a different name would
    parse to nothing.
    """
    raw = entity.properties.get(content_module.VALUE)
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    return None


def readable(
    result: ContentResult,
) -> tuple[list[EntityView], dict[str, int], dict[str, tuple[str, ...]], SymbolIndex]:
    """The day's numbers, their values, their properties and their forms.

    An entity with no value or no glyph is dropped here rather than in the
    middle of an attempt: a pool already filtered is a pool the construction
    can treat as uniform.
    """
    index = SymbolIndex(result.entities, policy=SYMBOL_POLICY)
    kept: list[EntityView] = []
    values: dict[str, int] = {}
    memberships: dict[str, tuple[str, ...]] = {}
    for entity in result.entities:
        value = value_of(entity)
        if value is None:
            continue
        if not index.form(entity.entity_id).playable:
            continue
        kept.append(entity)
        values[entity.entity_id] = value
        memberships[entity.entity_id] = tuple(entity.types)
    # Ascending, so the pool the generator draws from is in the order the
    # board will show. The service already returned it this way; sorting again
    # costs nothing and means a change there cannot silently reorder a board.
    kept.sort(key=lambda view: (values[view.entity_id], view.entity_id))
    return kept, values, memberships, index


def properties_at_tier(
    pool: Sequence[EntityView],
    memberships: Mapping[str, Sequence[str]],
    tier: int,
) -> tuple[str, ...]:
    """Every property of the whole pool that sits at ``tier``.

    Taken over the pool rather than over a board, because the day's property
    is chosen before the board is drawn: the question is which properties the
    window can build a board around at all.
    """
    whole = {view.entity_id: tuple(memberships[view.entity_id]) for view in pool}
    return tuple(
        name for name in vocabulary_of(whole) if tier_of(name) == tier
    )


def plan_for(
    *,
    pool: Sequence[EntityView],
    values: Mapping[str, int],
    memberships: Mapping[str, Sequence[str]],
    index: SymbolIndex,
    board_size: int,
    tier: int,
    rng: DeterministicRng,
) -> tuple[BoardPlan | None, str]:
    """One board attempt, or the name of the refusal that stopped it."""
    candidates = properties_at_tier(pool, memberships, tier)
    if not candidates:
        return None, NO_PROPERTY_AT_TIER

    name = rng.choice(list(candidates))
    members = [v for v in pool if name in memberships[v.entity_id]]
    outsiders = [v for v in pool if name not in memberships[v.entity_id]]
    if len(members) < board_size - 1:
        return None, TOO_FEW_MEMBERS
    if not outsiders:
        return None, NO_OUTSIDER

    chosen = rng.sample(list(members), board_size - 1)
    odd = rng.choice(list(outsiders))
    tiles = sorted(
        [*chosen, odd], key=lambda view: (values[view.entity_id], view.entity_id)
    )
    board = {view.entity_id: tuple(memberships[view.entity_id]) for view in tiles}

    vocabulary = vocabulary_of(board)
    found = readings_of(board, vocabulary)
    if odd.entity_id not in found:
        # Unreachable while the draw is correct: the chosen property is true
        # of every member and false of the outsider by construction. Counted
        # anyway, because the alternative to counting it is a board that
        # verifies as unsolvable and a run that cannot say why.
        return None, NO_READING
    if len(found) > 1:
        return None, SECOND_ANSWER

    return (
        BoardPlan(
            board_size=board_size,
            tiles=tuple(tiles),
            answer=odd.entity_id,
            property_name=name,
            reasons=found[odd.entity_id],
            vocabulary=vocabulary,
            universal=universal_in(board),
            near=near_readings_of(board, vocabulary),
            values={view.entity_id: values[view.entity_id] for view in tiles},
            memberships=board,
            glyphs={view.entity_id: index.glyph(view.entity_id) for view in tiles},
            labels={view.entity_id: index.spoken(view.entity_id) for view in tiles},
        ),
        "",
    )


def payload_for(plan: BoardPlan) -> dict[str, object]:
    """The candidate payload, which is the plan in plain JSON."""
    return {
        "board_size": plan.board_size,
        "tiles": [
            {
                "id": view.entity_id,
                "glyph": plan.glyphs[view.entity_id],
                "label": plan.labels[view.entity_id],
                "value": plan.values[view.entity_id],
            }
            for view in plan.tiles
        ],
        "answer": plan.answer,
        "property": plan.property_name,
        "reasons": list(plan.reasons),
        "vocabulary": list(plan.vocabulary),
        "universal": list(plan.universal),
        "near": list(plan.near),
        "memberships": {
            tile: list(names) for tile, names in sorted(plan.memberships.items())
        },
        "spread": plan.spread(),
    }


def rationale_for(plan: BoardPlan) -> str:
    return (
        f"{plan.board_size} tiles, all but one {plan.property_name} "
        f"(tier {tier_of(plan.property_name)}), {len(plan.reasons)} reasons, "
        f"{len(plan.near)} near readings, spread {plan.spread()}"
    )


def ranked(plans: Sequence[BoardPlan], tier: int) -> list[BoardPlan]:
    """Boards ordered by how near they are to the day's target.

    Three keys, in order. The property's tier first, since that is what the
    band asks for and what the solver meets. Then the number of reasons, fewest
    first: a board whose odd number is picked out by one property is the board
    the band intended, and one picked out by three is the same board made
    easier by its content. Then the candidate id, so the order is a pure
    function of the plans.

    A preference, not a verdict. The engine measures difficulty itself and
    searches for a board inside the requested band, so this decides which
    boards it looks at first and nothing else.
    """
    return sorted(
        plans,
        key=lambda plan: (
            abs(tier_of(plan.property_name) - tier),
            len(plan.reasons),
            plan.candidate_id(),
        ),
    )


def generate_candidates(
    context: GenerationContext, *, salt: str = ""
) -> Sequence[PuzzleCandidate]:
    """Offer as many boards as the budget allows, nearest the band first.

    Every size's candidates, the day's own size first. A candidate is a claim
    and the engine is what finds out whether it verifies, so stopping at the
    first size that produced any would let one unverifiable board lose a day
    that had workable boards one size down.
    """
    # Cleared first, so a run that returns early cannot leave the previous
    # day's refusals to be reported as its own.
    _LAST_REJECTIONS.clear()
    rejected: dict[str, int] = {}
    result = context.content.get(content_module.CANDIDATES)
    if result is None or not result.entities:
        rejected["no numbers"] = 1
        _LAST_REJECTIONS.update(rejected)
        return ()

    pool, values, memberships, index = readable(result)
    dropped = len(result.entities) - len(pool)
    if dropped:
        rejected[VALUE_MISSING] = dropped
    if not pool:
        _LAST_REJECTIONS.update(rejected)
        return ()

    tier = tier_for(context.difficulty_target)
    ordered: list[PuzzleCandidate] = []
    seen: set[str] = set()

    for board_size in board_sizes_for(context.day_key, salt):
        plans: list[BoardPlan] = []
        for attempt in range(ATTEMPTS_PER_SIZE):
            # A substream per attempt, so adding an attempt cannot move the
            # boards the earlier ones produced.
            rng = context.rng.derive(f"board:{board_size}:{attempt}")
            plan, reason = plan_for(
                pool=pool,
                values=values,
                memberships=memberships,
                index=index,
                board_size=board_size,
                tier=tier,
                rng=rng,
            )
            if plan is None:
                key = f"size {board_size}: {reason}"
                rejected[key] = rejected.get(key, 0) + 1
                continue
            plans.append(plan)

        for plan in ranked(plans, tier):
            candidate_id = plan.candidate_id()
            if candidate_id in seen:
                continue
            seen.add(candidate_id)
            ordered.append(
                PuzzleCandidate(
                    candidate_id=candidate_id,
                    payload=payload_for(plan),
                    fact_refs=plan.dependency_ids(),
                    rationale=rationale_for(plan),
                )
            )
        if len(ordered) >= context.candidate_budget:
            break

    _LAST_REJECTIONS.update(rejected)
    return tuple(ordered[: context.candidate_budget])


#: Why the last generation run found nothing. Module state, and deliberately
#: not part of any return value: the plugin protocol has no channel for it,
#: and inventing one would put a diagnostic in the contract every game then
#: has to implement.
_LAST_REJECTIONS: dict[str, int] = {}


def last_rejections() -> dict[str, int]:
    return dict(_LAST_REJECTIONS)


def unusable_reason() -> str:
    """The narrative a curator acts on, from the last run's refusals."""
    rejections = last_rejections()
    if not rejections:
        return ""
    ordered = sorted(rejections.items(), key=lambda kv: (-kv[1], kv[0]))
    return "; ".join(f"{reason} ({count})" for reason, count in ordered)


__all__ = [
    "ATTEMPTS_PER_SIZE",
    "NO_OUTSIDER",
    "NO_PROPERTY_AT_TIER",
    "NO_READING",
    "SECOND_ANSWER",
    "SYMBOL_POLICY",
    "TOO_FEW_MEMBERS",
    "VALUE_MISSING",
    "BoardPlan",
    "generate_candidates",
    "last_rejections",
    "payload_for",
    "plan_for",
    "properties_at_tier",
    "ranked",
    "rationale_for",
    "readable",
    "unusable_reason",
    "value_of",
]
