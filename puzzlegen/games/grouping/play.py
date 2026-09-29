"""Playing the board: grading, hints, scoring, rendering, sharing.

``grade_move`` is the module's centre of gravity and the one function here with
a hard constraint on it. It must be a pure function of the puzzle and the moves
before it, because the session layer replays the entire ledger on every
submission and raises ``DeterminismError`` if a regrade disagrees with a
recorded outcome. Nothing here reads a clock, a random source or anything
outside its arguments. The feasibility check that drives the axis switch
memoises inside one call, which is one replay pass; a cache living longer than
that would make the second replay of the same move disagree with the first.

The axis switch is derived rather than scheduled. After every correct move the
game asks whether the tiles still on the board can be partitioned taxonomically
at all, and when they cannot, the board stops asking for taxonomic groups and
asks for the hidden one instead. Existence only, first solution and stop: the
expensive direction is the negative answer, which is also the one that fires
the switch.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ...engine.plugin import (
    Hint,
    MoveJudgement,
    PresentationModel,
    Puzzle,
    Score,
    SessionTelemetry,
    ShareArtifact,
)
from ...engine.solvers import enumerate_partitions
from .descriptor import VISIBLE_GROUPS
from .verify import group_validator, memberships_of, universal_categories

#: Axis names carried in state.
TAXONOMIC = "taxonomic"
OVERLAY = "overlay"

#: States the enumerator may spend answering "can these tiles still be
#: grouped?". Far smaller than the verifier's budget because the question is
#: existence rather than enumeration, and it is asked on every correct move.
FEASIBILITY_BUDGET = 200_000

#: Points.
#:
#: Flat awards rather than a curve, because a score a player cannot predict is
#: a score they cannot feel they earned. The hidden group is worth more than a
#: visible one for the obvious reason: nobody told them it was there.
POINTS_PER_GROUP = 100
POINTS_FOR_HIDDEN = 250
POINTS_FOR_COMPLETION = 150
PENALTY_PER_MISTAKE = 25
PENALTY_PER_HINT = 40

#: How much of the completion award difficulty can add.
DIFFICULTY_BONUS = 100


def empty_state(puzzle: Puzzle) -> dict[str, Any]:
    """What a board looks like before the first move."""
    return {
        "axis": TAXONOMIC,
        "solved": [],
        "revealed": [],
        "mistakes": 0,
        "hidden_found": False,
        "complete": False,
    }


def _solved_tiles(state: Mapping[str, Any]) -> set[str]:
    return {tile for group in state.get("solved", ()) for tile in group}


def remaining_tiles(puzzle: Puzzle, state: Mapping[str, Any]) -> list[str]:
    placed = _solved_tiles(state)
    return sorted(
        tile["id"] for tile in puzzle.payload["tiles"] if tile["id"] not in placed
    )


def intended_groups(puzzle: Puzzle) -> list[tuple[str, frozenset[str]]]:
    return [
        (group["category"], frozenset(group["members"]))
        for group in puzzle.solution["groups"]
    ]


def hidden_group(puzzle: Puzzle) -> frozenset[str]:
    return frozenset(puzzle.solution["hidden"]["members"])


def still_partitionable(
    puzzle: Puzzle,
    tiles: Sequence[str],
    *,
    cache: dict[tuple[str, ...], bool] | None = None,
) -> bool:
    """Whether the tiles left can form taxonomic groups at all.

    Existence only. ``max_solutions=1`` stops at the first partition found,
    which makes the affirmative answer cheap; the negative answer costs the
    whole search, and the budget is sized for that because it is the answer
    that changes the game.

    ``cache`` is supplied by the caller and lives for one call, so two replays
    of the same ledger ask the enumerator the same questions in the same order
    and get the same answers.
    """
    key = tuple(tiles)
    if cache is not None and key in cache:
        return cache[key]

    group_size = int(puzzle.payload["group_size"])
    groups_left = len(tiles) // group_size
    if groups_left == 0:
        answer = False
    else:
        memberships = memberships_of(puzzle)
        universal = universal_categories(memberships)
        search = enumerate_partitions(
            list(tiles),
            group_size=group_size,
            group_count=groups_left,
            is_valid_group=group_validator(memberships, universal),
            max_solutions=1,
            state_budget=FEASIBILITY_BUDGET,
        )
        # An exhausted search that found nothing is a real no. A search that
        # ran out of budget found no partition either, and saying "yes, one
        # probably exists" on the strength of not having looked would switch
        # the axis on some replays and not others.
        answer = bool(search.solutions)
    if cache is not None:
        cache[key] = answer
    return answer


def _selection(payload: Mapping[str, Any]) -> list[str]:
    tiles = payload.get("tiles")
    if not isinstance(tiles, (list, tuple)):
        raise ValueError("a submission must carry a list of tiles")
    return sorted(str(tile) for tile in tiles)


def _closest_miss(
    selection: frozenset[str], unsolved: Sequence[tuple[str, frozenset[str]]]
) -> int:
    """How many tiles the nearest unsolved group is away."""
    if not unsolved:
        return len(selection)
    return min(len(selection - members) for _, members in unsolved)


def grade_move(
    puzzle: Puzzle, payload: Mapping[str, Any], state: Mapping[str, Any]
) -> MoveJudgement:
    """Rule on one submission, given the state the ledger replayed to."""
    cache: dict[tuple[str, ...], bool] = {}
    current = dict(state) if state else empty_state(puzzle)
    current.setdefault("axis", TAXONOMIC)
    current.setdefault("solved", [])
    current.setdefault("revealed", [])
    current.setdefault("mistakes", 0)
    current.setdefault("hidden_found", False)
    current.setdefault("complete", False)

    if current["complete"]:
        # ``solved`` describes this move, not the puzzle: the model refuses a
        # move that is both wrong and solving, and a move arriving after the
        # end solved nothing.
        return MoveJudgement(
            correct=False,
            complete=True,
            solved=False,
            state=current,
            note="This puzzle is already finished",
        )

    group_size = int(puzzle.payload["group_size"])
    selection = _selection(payload)
    on_board = {tile["id"] for tile in puzzle.payload["tiles"]}

    if len(selection) != group_size or len(set(selection)) != len(selection):
        return MoveJudgement(
            correct=False,
            state=current,
            note=f"Choose exactly {group_size} different tiles",
        )
    if not set(selection) <= on_board:
        return MoveJudgement(
            correct=False, state=current, note="That tile is not on the board"
        )

    chosen = frozenset(selection)

    if current["axis"] == OVERLAY:
        return _grade_overlay(puzzle, chosen, current)
    return _grade_taxonomic(puzzle, chosen, current, cache)


def _grade_taxonomic(
    puzzle: Puzzle,
    chosen: frozenset[str],
    current: dict[str, Any],
    cache: dict[tuple[str, ...], bool],
) -> MoveJudgement:
    placed = _solved_tiles(current)
    if chosen & placed:
        return MoveJudgement(
            correct=False,
            state=current,
            note="Some of those tiles are already in a group",
        )

    unsolved = [
        (name, members)
        for name, members in intended_groups(puzzle)
        if not (members & placed)
    ]
    match = next((name for name, members in unsolved if members == chosen), None)

    if match is None:
        away = _closest_miss(chosen, unsolved)
        current = {**current, "mistakes": int(current["mistakes"]) + 1}
        note = "One tile away" if away == 1 else "Not a group"
        return MoveJudgement(correct=False, state=current, note=note)

    solved = [*current["solved"], sorted(chosen)]
    revealed = [*current["revealed"], match]
    updated: dict[str, Any] = {
        **current,
        "solved": solved,
        "revealed": revealed,
    }

    left = remaining_tiles(puzzle, updated)
    if not still_partitionable(puzzle, left, cache=cache):
        # The first axis is finished. Everything the player has placed stays
        # placed; what changes is the question.
        updated["axis"] = OVERLAY
        return MoveJudgement(
            correct=True,
            state=updated,
            note=(
                f"Correct: {match}. No groups remain on that axis. "
                f"Find the {len(hidden_group(puzzle))} tiles that share "
                "something else"
            ),
        )

    return MoveJudgement(correct=True, state=updated, note=f"Correct: {match}")


def _grade_overlay(
    puzzle: Puzzle, chosen: frozenset[str], current: dict[str, Any]
) -> MoveJudgement:
    hidden = hidden_group(puzzle)
    if chosen != hidden:
        away = len(chosen - hidden)
        updated = {**current, "mistakes": int(current["mistakes"]) + 1}
        return MoveJudgement(
            correct=False,
            state=updated,
            note="One tile away" if away == 1 else "Not the hidden group",
        )

    category = puzzle.solution["hidden"]["category"]
    updated = {
        **current,
        "hidden_found": True,
        "complete": True,
        "revealed": [*current["revealed"], category],
    }
    return MoveJudgement(
        correct=True,
        complete=True,
        solved=True,
        state=updated,
        note=f"Correct: {category}",
    )


def get_hint(
    puzzle: Puzzle, state: Mapping[str, Any], hints_used: int
) -> Hint:
    """Help, in increasing order of how much it gives away.

    Never names a whole group. The first hint says what an unsolved group is
    about, the second names one tile in it, and the third names a second tile;
    a fourth would be the answer, so there is no fourth.
    """
    current = dict(state) if state else empty_state(puzzle)
    placed = _solved_tiles(current)

    if current.get("axis") == OVERLAY:
        hidden = sorted(hidden_group(puzzle))
        secondary = puzzle.solution["hidden"].get("secondary_category")
        if hints_used == 0 and secondary:
            return Hint(
                text=f"The remaining answer also has to do with {secondary}",
                cost=1,
            )
        if hints_used <= 1:
            return Hint(
                text="One of them is on the board's first row, at least",
                reveals=(hidden[0],),
                cost=1,
            )
        return Hint(
            text="Two of the tiles that belong together",
            reveals=tuple(hidden[:2]),
            cost=2,
            exhausted=True,
        )

    unsolved = [
        (name, sorted(members))
        for name, members in intended_groups(puzzle)
        if not (frozenset(members) & placed)
    ]
    if not unsolved:
        return Hint(text="Nothing left to hint at", cost=0, exhausted=True)

    name, members = unsolved[0]
    if hints_used == 0:
        return Hint(text=f"One group is about {name}", cost=1)
    if hints_used == 1:
        return Hint(
            text=f"This tile belongs to the {name} group",
            reveals=(members[0],),
            cost=1,
        )
    return Hint(
        text=f"Two tiles from the {name} group",
        reveals=tuple(members[:2]),
        cost=2,
        exhausted=True,
    )


def score(puzzle: Puzzle, telemetry: SessionTelemetry) -> Score:
    """Points, from measured signals only.

    Every number read here was computed by the engine from the ledger, so a
    score can be recomputed later from stored telemetry and a game cannot
    invent a flattering signal.
    """
    groups = max(0, telemetry.attempts - telemetry.mistakes)
    visible = min(groups, VISIBLE_GROUPS)
    breakdown = {
        "groups": visible * POINTS_PER_GROUP,
        "hidden": POINTS_FOR_HIDDEN if telemetry.completed else 0,
        "completion": POINTS_FOR_COMPLETION if telemetry.completed else 0,
        "difficulty": (
            int(DIFFICULTY_BONUS * telemetry.difficulty_score)
            if telemetry.completed
            else 0
        ),
        "mistakes": -telemetry.mistakes * PENALTY_PER_MISTAKE,
        "hints": -telemetry.hints_used * PENALTY_PER_HINT,
    }
    return Score(
        points=max(0, sum(breakdown.values())),
        breakdown=breakdown,
        detail=(
            f"{visible} groups"
            + (", hidden group found" if telemetry.completed else "")
        ),
    )


def render(
    puzzle: Puzzle, state: Mapping[str, Any], locale: str
) -> PresentationModel:
    """A data description of the board. Never markup, never the answer."""
    current = dict(state) if state else empty_state(puzzle)
    placed: dict[str, int] = {}
    for index, group in enumerate(current.get("solved", ())):
        for tile in group:
            placed[tile] = index

    elements = tuple(
        {
            "kind": "tile",
            "id": tile["id"],
            "label": tile["label"],
            "state": "solved" if tile["id"] in placed else "idle",
            "group": placed.get(tile["id"]),
        }
        for tile in puzzle.payload["tiles"]
    )

    return PresentationModel(
        elements=elements,
        layout="grid",
        labels={
            "instructions": puzzle.presentation.get("instructions", ""),
            # Named groups appear only once solved, so the labels a client
            # holds never run ahead of what the player has earned.
            **{
                f"group_{index}": name
                for index, name in enumerate(current.get("revealed", ()))
            },
        },
        state={
            "axis": current.get("axis", TAXONOMIC),
            "mistakes": current.get("mistakes", 0),
            "complete": current.get("complete", False),
            "groups_solved": len(current.get("solved", ())),
            "groups_total": VISIBLE_GROUPS,
            "columns": puzzle.payload.get("columns", VISIBLE_GROUPS),
        },
    )


def create_share_artifact(
    puzzle: Puzzle, telemetry: SessionTelemetry, day_key: str
) -> ShareArtifact:
    """The shape of the attempt, and nothing about the answer.

    Built from the move ledger with payloads already stripped by the engine,
    so it can say an attempt was wrong but never which tiles were in it. One
    row per submission, in the order they were made, which is what makes two
    people's grids comparable without either naming a word.

    The last correct submission of a completed game is the hidden group, which
    is why that row can be marked differently without the share ever being
    told which tiles it held.
    """
    submits = [
        event
        for event in telemetry.events
        if event.get("kind") in ("SUBMIT", "HINT")
    ]
    last_correct = max(
        (
            index
            for index, event in enumerate(submits)
            if event.get("kind") == "SUBMIT" and event.get("outcome") == "CORRECT"
        ),
        default=None,
    )

    rows: list[tuple[str, ...]] = []
    for index, event in enumerate(submits):
        if event.get("kind") == "HINT":
            rows.append(("hint",))
        elif event.get("outcome") == "CORRECT":
            hidden = telemetry.completed and index == last_correct
            rows.append(("hidden" if hidden else "solved",))
        else:
            rows.append(("miss",))

    if not rows:
        rows = [("miss",)] * max(0, telemetry.attempts)

    outcome = "solved" if telemetry.completed else "unsolved"
    headline = (
        f"Found the hidden group in {telemetry.attempts} guesses"
        if telemetry.completed
        else f"Gave up after {telemetry.attempts} guesses"
    )
    return ShareArtifact(
        game_id=puzzle.game_id,
        day_key=day_key,
        outcome=outcome,
        tokens=tuple(rows),
        headline=headline,
        detail={
            "mistakes": telemetry.mistakes,
            "hints": telemetry.hints_used,
            "group_size": puzzle.payload["group_size"],
        },
    )


__all__ = [
    "DIFFICULTY_BONUS",
    "FEASIBILITY_BUDGET",
    "OVERLAY",
    "PENALTY_PER_HINT",
    "PENALTY_PER_MISTAKE",
    "POINTS_FOR_COMPLETION",
    "POINTS_FOR_HIDDEN",
    "POINTS_PER_GROUP",
    "TAXONOMIC",
    "create_share_artifact",
    "empty_state",
    "get_hint",
    "grade_move",
    "hidden_group",
    "intended_groups",
    "remaining_tiles",
    "render",
    "score",
    "still_partitionable",
]
