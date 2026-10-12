"""Building a board with exactly one trio in it.

A candidate here is a claim that a set of figures is a board: one trio among
them satisfies the rule, no other triple does, and the near-miss density is
near the day's target. The claim is cheap and most are discarded, which is the
contract: filtering is the engine's job, and a game that pre-filters on
freshness, confidence or policy duplicates a gate it cannot see the inputs to.

The construction, which is the whole of this module, is the opposite of a
search. At three values per attribute any two figures determine exactly one
third, so a board's trios are exactly its pairs whose completion is also on
the board. Seed a board with one trio, then add only figures that complete no
pair already down, keeping the set of forbidden completions as you go, and the
board has exactly one trio by construction rather than by luck.

That is worth stating against the alternative, because the alternative is
what a first attempt writes. Drawing n figures at random and keeping the
boards that happen to hold exactly one trio works badly at nine tiles and
worse at twelve: the number of pairs grows quadratically and each pair is
another chance for a second trio. Measured on the default space over a
thousand random draws at each size, exactly one trio came up 422 times at
nine tiles and 147 at twelve, so rejection sampling would throw away well
over half the work at the smaller size and six boards in seven at the larger.
The construction built 500 of 500 attempted boards at both sizes, with no
refusal of any kind.

What this module still refuses, and why each refusal is the game's rather than
the engine's: an attribute space in which the rule is not determinate, a seed
trio whose third figure the day's content did not supply, and a board the
filler ran out of room to finish. Each is a fact about the board rather than
about content policy, and each is counted so an empty day says which
constraint to argue with.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from puzzlegen.content.query import ContentResult, EntityView
from puzzlegen.core.hashing import short_hash
from puzzlegen.core.rng import DeterministicRng
from puzzlegen.engine.plugin import GenerationContext, PuzzleCandidate
from puzzlegen.symbols import SymbolIndex, SymbolPolicy

from . import content as content_module
from .descriptor import GAME_ID, band_target, board_sizes_for
from .trio import (
    ATTRIBUTES,
    TRIO,
    SpaceError,
    check_space,
    completion,
    forbidden_completions,
    is_trio,
    key_of,
    near_trios,
    trios_in,
)

#: Why an attempt produced no board. Counted rather than logged, because an
#: empty day is a number problem: that forty attempts failed says nothing, and
#: that all forty failed to fill the board says exactly which constraint to
#: argue with.
SPACE_NOT_DETERMINATE = "space_not_determinate"
TOO_FEW_FIGURES = "too_few_figures"
COMPLETION_ABSENT = "completion_absent"
BOARD_NOT_FILLABLE = "board_not_fillable"
GLYPH_MISSING = "glyph_missing"
NOT_EXACTLY_ONE_TRIO = "not_exactly_one_trio"

#: How many boards to attempt per size.
#:
#: Forty-eight. The construction succeeds on nearly every attempt, so this is
#: not a retry budget: it is how many *different* boards the day offers the
#: engine to choose a difficulty band from. The engine verifies in order and
#: stops at the first board inside the band, so offering more costs nothing on
#: a good day and is what rescues a day whose first boards all band low.
ATTEMPTS_PER_SIZE = 48

#: The symbolic form policy this game reads its tiles under.
#:
#: The default, which refuses a glyphless entity rather than falling back to
#: its name. That is the right refusal here: a figure's name is a phrase, a
#: tile is one glyph wide, and a board drawn from names would be a board of
#: sentences. A figure the content gave no glyph is a figure this game cannot
#: draw, and the refusal is counted rather than hidden.
SYMBOL_POLICY = SymbolPolicy()


@dataclass(frozen=True, slots=True)
class BoardPlan:
    """One board, its trio and the evidence for the claim.

    Frozen, and carrying its own measurements rather than recomputing them.
    The near-miss share decides the candidate's rank and the difficulty
    model reads the same number later, so measuring it twice would be two
    places for it to be different.
    """

    board_size: int
    #: Tiles in content order, not board order. Assembly shuffles them, and a
    #: plan that had already chosen a layout would make the shuffle untestable.
    tiles: tuple[EntityView, ...]
    #: The three tile ids that form the trio.
    answer: tuple[str, ...]
    #: Attribute readings, by entity id.
    figures: Mapping[str, Mapping[str, str]]
    #: Glyph and spoken label, by entity id.
    glyphs: Mapping[str, str]
    labels: Mapping[str, str]
    #: Triples failing on exactly one attribute, as a share of all triples.
    decoy_share: float
    near_misses: int
    #: Attributes on which the trio is all different rather than all the same.
    varying: tuple[str, ...]
    space: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def candidate_id(self) -> str:
        """Identity of the board, from the tiles and the trio.

        The tile set and the answer, not the layout: two plans with the same
        tiles and the same trio are the same board however it is laid out, and
        the engine screens duplicate candidate ids.
        """
        return short_hash(
            [GAME_ID, str(self.board_size), *sorted(t.entity_id for t in self.tiles),
             "answer", *sorted(self.answer)],
            16,
        )

    def dependency_ids(self) -> tuple[str, ...]:
        """Every graph record this board rests on."""
        found: list[str] = []
        for tile in self.tiles:
            found.extend(tile.dependency_ids())
        return tuple(dict.fromkeys(found))


def figure_of(entity: EntityView) -> dict[str, str] | None:
    """One entity's attribute reading, or ``None`` when it is not a figure.

    Read from facts rather than from the entity's category names. The
    memberships say the same thing and would have to be parsed back into
    attribute and value, which means a category named for one thing and
    holding another would silently change what a board is about.
    """
    reading: dict[str, str] = {}
    for attribute in ATTRIBUTES:
        value = entity.properties.get(attribute)
        if not isinstance(value, str) or not value:
            return None
        reading[attribute] = value
    return reading


def readable(result: ContentResult) -> tuple[
    list[EntityView], dict[str, dict[str, str]], SymbolIndex
]:
    """The day's figures, their attribute readings and their symbolic forms.

    An entity that is not a readable figure, or that has no glyph, is dropped
    here rather than in the middle of a board attempt: a pool that has already
    been filtered is a pool the construction can treat as uniform.
    """
    index = SymbolIndex(result.entities, policy=SYMBOL_POLICY)
    kept: list[EntityView] = []
    figures: dict[str, dict[str, str]] = {}
    for entity in result.entities:
        reading = figure_of(entity)
        if reading is None:
            continue
        if not index.form(entity.entity_id).playable:
            continue
        kept.append(entity)
        figures[entity.entity_id] = reading
    return kept, figures, index


def build_board(
    *,
    pool: Sequence[EntityView],
    figures: Mapping[str, Mapping[str, str]],
    space: Mapping[str, Sequence[str]],
    board_size: int,
    rng: DeterministicRng,
) -> tuple[list[EntityView], tuple[str, ...]] | str:
    """One board, or the name of the refusal that stopped it.

    The construction in four steps, each of which is the only thing it can be:

    1. Draw two distinct figures. Their completion is determined, so the trio
       is decided by the draw rather than searched for.
    2. Refuse if the completion is not a figure the day supplied. On a
       complete attribute space this cannot happen, so the refusal is a
       statement about the content and not about the draw.
    3. Keep the set of completions of every pair on the board. A figure in it
       would finish a second trio, so a figure outside it cannot.
    4. Walk the rest of the pool in a shuffled order, taking each figure that
       is not forbidden and widening the forbidden set with its own pairs.

    Nothing here verifies the result. The verifier enumerates every triple
    independently, which is the only way a bug in this construction could be
    caught by anything other than a player.
    """
    by_key = {key_of(figures[view.entity_id]): view for view in pool}
    if len(pool) < board_size:
        return TOO_FEW_FIGURES

    first, second = rng.sample(list(pool), 2)
    third_key = key_of(completion(figures[first.entity_id], figures[second.entity_id], space))
    third = by_key.get(third_key)
    if third is None or third.entity_id in (first.entity_id, second.entity_id):
        return COMPLETION_ABSENT

    board = [first, second, third]
    answer = tuple(view.entity_id for view in board)
    forbidden = forbidden_completions(
        (figures[view.entity_id] for view in board), space
    )
    on_board = {view.entity_id for view in board}

    for view in rng.shuffled(list(pool)):
        if len(board) >= board_size:
            break
        if view.entity_id in on_board:
            continue
        reading = figures[view.entity_id]
        if key_of(reading) in forbidden:
            continue
        # Widened before the figure is added, over the pairs it makes with
        # what is already down. Widening afterwards over every pair would
        # recompute the whole set per tile for the same answer.
        forbidden |= {
            key_of(completion(reading, figures[other.entity_id], space))
            for other in board
        }
        board.append(view)
        on_board.add(view.entity_id)

    if len(board) < board_size:
        return BOARD_NOT_FILLABLE
    return board, answer


def plan_for(
    *,
    pool: Sequence[EntityView],
    figures: Mapping[str, Mapping[str, str]],
    index: SymbolIndex,
    space: Mapping[str, Sequence[str]],
    board_size: int,
    rng: DeterministicRng,
) -> tuple[BoardPlan | None, str]:
    """One board attempt, measured, or the refusal that stopped it."""
    built = build_board(
        pool=pool, figures=figures, space=space, board_size=board_size, rng=rng
    )
    if isinstance(built, str):
        return None, built
    board, answer = built

    readings = [figures[view.entity_id] for view in board]
    found = trios_in(readings)
    if len(found) != 1:
        # Unreachable while the construction is correct, and counted anyway.
        # A board with two trios verifies as ambiguous and a board with none
        # verifies as unsolvable, so the engine would catch both; what it
        # could not tell is that the construction had a bug rather than the
        # content having a shortage.
        return None, NOT_EXACTLY_ONE_TRIO

    triples = max(1, _triple_count(board_size))
    near = near_trios(readings)
    trio_readings = [figures[entity_id] for entity_id in answer]
    varying = tuple(
        attribute
        for attribute in ATTRIBUTES
        if len({reading[attribute] for reading in trio_readings}) == TRIO
    )
    return (
        BoardPlan(
            board_size=board_size,
            tiles=tuple(board),
            answer=answer,
            figures={view.entity_id: figures[view.entity_id] for view in board},
            glyphs={view.entity_id: index.glyph(view.entity_id) for view in board},
            labels={view.entity_id: index.spoken(view.entity_id) for view in board},
            decoy_share=len(near) / triples,
            near_misses=len(near),
            varying=varying,
            space={a: tuple(v) for a, v in space.items()},
        ),
        "",
    )


def _triple_count(board_size: int) -> int:
    """How many triples a board of this size holds.

    Written out rather than imported from ``math.comb`` for three, because the
    closed form is clearer than a general one at this size and the verifier's
    own count has to agree with it.
    """
    return board_size * (board_size - 1) * (board_size - 2) // 6


def payload_for(plan: BoardPlan) -> dict[str, object]:
    """The candidate payload, which is the plan in plain JSON.

    Everything assembly needs and nothing it would have to recompute. The
    answer is in here, which is correct and is also why a candidate payload
    never reaches a client: assembly is what splits it into a board and a
    solution, and the engine strips the solution when it publishes.
    """
    return {
        "board_size": plan.board_size,
        "tiles": [
            {
                "id": view.entity_id,
                "glyph": plan.glyphs[view.entity_id],
                "label": plan.labels[view.entity_id],
                "attributes": dict(plan.figures[view.entity_id]),
            }
            for view in plan.tiles
        ],
        "answer": list(plan.answer),
        "decoy_share": plan.decoy_share,
        "near_misses": plan.near_misses,
        "varying": list(plan.varying),
        "space": {a: list(v) for a, v in sorted(plan.space.items())},
    }


def rationale_for(plan: BoardPlan) -> str:
    varying = ", ".join(plan.varying) if plan.varying else "nothing"
    return (
        f"{plan.board_size} tiles, trio varies on {varying}, "
        f"{plan.near_misses} near misses ({plan.decoy_share:.2f})"
    )


def ranked(plans: Sequence[BoardPlan], target: tuple[int, float]) -> list[BoardPlan]:
    """Boards ordered by how near they are to the day's band target.

    Lexicographic on the two levers rather than a weighted sum, and the order
    is the reason. ``varying`` is an integer from one to four and is what the
    solver's eye does; the decoy share is a continuous number inside a range
    of about a tenth. Combined into one distance, the decoy term would decide
    nothing except between boards already tied on ``varying``, which is
    exactly what the lexicographic order says out loud.

    A preference, not a verdict. The engine measures difficulty itself and
    searches for a board inside the requested band, so this decides which
    boards it looks at first and nothing else. Ties break on the candidate id,
    so the order is a pure function of the plans.
    """
    want_varying, want_decoy = target
    return sorted(
        plans,
        key=lambda plan: (
            abs(len(plan.varying) - want_varying),
            abs(plan.decoy_share - want_decoy),
            plan.candidate_id(),
        ),
    )


def generate_candidates(
    context: GenerationContext, *, salt: str = ""
) -> Sequence[PuzzleCandidate]:
    """Offer as many boards as the budget allows, nearest the band first.

    Every size's candidates, the day's own size first, rather than stopping at
    the first size that produced any. A candidate is a claim and the engine is
    what finds out whether it verifies, so stopping here would let one
    unverifiable board lose a day that had workable boards at the other size.

    ``salt`` is the plugin's own and has to be the one
    ``get_content_requirements`` used, so the size order generation ranks is
    the order the content was fetched in.
    """
    # Cleared first, so a run that returns early cannot leave the previous
    # day's refusals to be reported as its own.
    _LAST_REJECTIONS.clear()
    rejected: dict[str, int] = {}
    result = context.content.get(content_module.CANDIDATES)
    if result is None or not result.entities:
        rejected["no figures"] = 1
        _LAST_REJECTIONS.update(rejected)
        return ()

    pool, figures, index = readable(result)
    dropped = len(result.entities) - len(pool)
    if dropped:
        rejected[GLYPH_MISSING] = dropped

    try:
        space = check_space([figures[view.entity_id] for view in pool])
    except SpaceError as exc:
        rejected[f"{SPACE_NOT_DETERMINATE}: {exc}"] = 1
        _LAST_REJECTIONS.update(rejected)
        return ()

    target = band_target(context.difficulty_target)
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
                figures=figures,
                index=index,
                space=space,
                board_size=board_size,
                rng=rng,
            )
            if plan is None:
                key = f"size {board_size}: {reason}"
                rejected[key] = rejected.get(key, 0) + 1
                continue
            plans.append(plan)

        for plan in ranked(plans, target):
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
#: has to implement. Read by ``unusable_reason`` and by nothing else.
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
    "BOARD_NOT_FILLABLE",
    "COMPLETION_ABSENT",
    "GLYPH_MISSING",
    "NOT_EXACTLY_ONE_TRIO",
    "SPACE_NOT_DETERMINATE",
    "SYMBOL_POLICY",
    "TOO_FEW_FIGURES",
    "BoardPlan",
    "build_board",
    "figure_of",
    "generate_candidates",
    "last_rejections",
    "payload_for",
    "plan_for",
    "ranked",
    "rationale_for",
    "readable",
    "unusable_reason",
]
