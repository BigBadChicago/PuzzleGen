"""Boards and content views, built by hand.

Synthetic rather than drawn from a snapshot, so a failing test points at the
game rather than at whichever word the curated file happened to hold. The one
test that runs against real content says so in its own name.

Two builders, because the game has two shapes worth constructing. ``group``
and ``result`` build what the content service would have returned, for the
generator. ``make_puzzle`` builds an assembled board directly, for
verification, difficulty and play, which each need a board of an exact shape
that generation would refuse to produce.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from puzzlegen.content.query import ContentResult, EntityView, GroupView, Operation
from puzzlegen.core.rng import DeterministicRng, derive_seed
from puzzlegen.core.types import DifficultyBand
from puzzlegen.engine.plugin import GenerationContext, Puzzle
from puzzlegen_letters import content as content_module
from puzzlegen_letters.descriptor import GAME_ID
from puzzlegen_letters.marks import letters_of

#: Members of ``fish`` from the requirements' own worked example, with enough
#: of them for the smallest board.
FISH = ("salmon", "trout", "herring", "bass", "halibut", "carp")


def entity(name: str, *categories: str) -> EntityView:
    """One member, in the categories named.

    ``types`` and ``type_ids`` are aligned the way the content service aligns
    them, because the generator reads the names and the dependency ledger
    reads the ids, and a helper that supplied only one of them would hide a
    confusion between the two.
    """
    return EntityView(
        entity_id=f"entity:{name}",
        name=name,
        types=tuple(categories),
        type_ids=tuple(f"category:{category}" for category in categories),
    )


def group(category: str, members: Sequence[str], *, also: Sequence[str] = ()) -> GroupView:
    """A category and its members, as ``FIND_GROUPS`` returns them.

    ``also`` puts every member in a second category too, which is how a board
    is given a second reading without changing its tiles.
    """
    return GroupView(
        members=tuple(entity(name, category, *also) for name in members),
        shared_category=category,
        shared_category_id=f"category:{category}",
    )


def result(groups: Sequence[GroupView]) -> ContentResult:
    return ContentResult(operation=Operation.FIND_GROUPS, groups=tuple(groups))


def context(
    *,
    day: str = "2026-10-01",
    band: DifficultyBand = DifficultyBand.EASY,
    content: Mapping[int, Sequence[GroupView]] | None = None,
    budget: int = 64,
) -> GenerationContext:
    """A generation context carrying one result per board size."""
    bundle = {
        content_module.sized(content_module.CANDIDATES, size): result(groups)
        for size, groups in (content or {}).items()
    }
    return GenerationContext(
        day_key=day,
        game_version="1.0.0",
        difficulty_target=band,
        locale="en",
        rng=DeterministicRng(derive_seed(day, GAME_ID)).derive("candidates"),
        content=bundle,
        candidate_budget=budget,
    )


def make_puzzle(
    category: str,
    marks: Mapping[str, str],
    unmarked: Sequence[str] = (),
    *,
    blanks: int | None = None,
    memberships: Mapping[str, Sequence[str]] | None = None,
    vocabulary: Sequence[str] = (),
    forced: Sequence[str] = (),
    manufactured: Sequence[str] = (),
    answer_length: int | None = None,
) -> Puzzle:
    """An assembled board of exactly the shape asked for.

    ``marks`` maps a tile's own word to the letter it carries, so a test reads
    as the board does. Everything the board must agree about -- the blank
    count, the answer length, the memberships -- is derived unless the test
    overrides it, which is how a test can build a board that disagrees with
    itself on purpose.
    """
    letters = letters_of(category)
    labels = [*marks, *unmarked]
    blanks = len(letters) - len(marks) if blanks is None else blanks
    tiles = [
        {
            "id": f"entity:{label}",
            "label": label,
            "mark": marks.get(label),
        }
        for label in labels
    ]
    memberships = (
        {f"entity:{label}": [category] for label in labels}
        if memberships is None
        else {f"entity:{label}": list(names) for label, names in memberships.items()}
    )
    return Puzzle(
        game_id=GAME_ID,
        payload={
            "game_id": GAME_ID,
            "board_size": len(labels),
            "columns": max(1, len(labels) // 2),
            "tiles": tiles,
            "blanks": blanks,
            "answer_length": len(letters) if answer_length is None else answer_length,
            "marked_tiles": len(marks),
        },
        solution={
            "category": category,
            "category_id": f"category:{category}",
            "answer": category,
            "letters": letters,
            "marks": {f"entity:{label}": letter for label, letter in marks.items()},
            "forced_blanks": list(forced),
            "manufactured_blanks": list(manufactured),
            "memberships": memberships,
            "vocabulary": list(vocabulary),
        },
        fact_refs=tuple(f"entity:{label}" for label in labels),
        presentation={"instructions": "spell it"},
        candidate_id=f"{GAME_ID}:category:{category}",
    )


def fish_board(**overrides) -> Puzzle:
    """The requirements' worked example, legal: two blanks rather than one.

    ``fish`` has four letters, so two marks and two blanks. The example in the
    requirements marks three and blanks one, which that document calls out as
    below the floor and illegal, so the board here is the legal version of it.
    """
    marks = overrides.pop("marks", {"herring": "h", "bass": "s"})
    unmarked = overrides.pop(
        "unmarked", [name for name in FISH if name not in marks]
    )
    return make_puzzle("fish", marks, unmarked, **overrides)


__all__ = [
    "FISH",
    "context",
    "entity",
    "fish_board",
    "group",
    "make_puzzle",
    "result",
]
