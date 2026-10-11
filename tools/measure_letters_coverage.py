#!/usr/bin/env python3
"""Which categories could be a missing_letters board, and what stops the rest.

Game 2 asks the solver to spell a category's own name from letters marked on
its members, one letter per tile, with some letters supplied by nobody. So a
category is playable only if its name's letters can be distributed over
distinct members that actually contain them. That is a content question, and
this answers it before any generator exists, the way
``games/grouping/tools/measure_coverage.py`` did for game 1.

A throwaway probe over build A suggested the binding constraint is not the
letter matching but the name's length against the board: a name of ``L``
letters with ``b`` blanks needs ``L - b`` marks on distinct tiles, and at
least one tile carries no mark, so the board needs ``L - b + 1`` tiles. This
tool measures that properly, per board size and blank count, and names what
each refusal was so the answer is actionable rather than a single number.

    python tools/measure_letters_coverage.py
    python tools/measure_letters_coverage.py --db content/graph.sqlite --json out.json
    python tools/measure_letters_coverage.py --difficulty medium --blanks 2 3

Exit status is 0 when at least one board size and blank count has a playable
category, and 1 otherwise, so a build's answer is a command rather than a
table to read.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from puzzlegen.content.service import DIFFICULTY_TO_FREQUENCY, MEMBERSHIP_PREDICATE
from puzzlegen.core.types import DifficultyBand, ReviewStatus
from puzzlegen.graph import GraphRepositories, SqliteDocumentStore
from puzzlegen.surface import SurfaceForm, SurfaceIndex, SurfacePolicy, form_for

#: The taxonomy whose categories are candidate targets. Overlay categories are
#: not candidates: their names run 14 to 25 letters, which no ordinary board
#: size can hold, and measuring them would only restate that.
LEXICAL_TAXONOMY = "wordnet"

DEFAULT_DB = Path("content/graph.sqlite")

#: Board sizes to report. Game 1's sizes are a different question (four groups
#: of N); here the board is one flat set of tiles, so these are tile counts.
BOARD_SIZES = (6, 8, 10, 12)

#: Blank counts to report. Two is the floor the requirements set, and the
#: upper end is where a board starts giving away more than it withholds.
BLANK_COUNTS = (2, 3, 4)

#: Categories to name per cell. A curator reads a shortlist; two hundred rows
#: is a file nobody opens.
SHORTLIST = 12


#: The name's letters outnumber what a board of this size can carry marks for.
#: This is the constraint the probe found binding, and it is a naming problem,
#: not a membership one: no amount of extra members fixes it.
NAME_TOO_LONG = "name_too_long"
#: Fewer eligible members than the board has tiles. A content gap: more
#: frequency-passing members in this category would fix it.
TOO_FEW_MEMBERS = "too_few_members"
#: Enough members and a short enough name, but the letters cannot be placed
#: one per tile on members that contain them. The letter matching itself.
LETTERS_UNPLACEABLE = "letters_unplaceable"
#: The name has no letters at all, or so few that the blank count would spell
#: the whole thing.
NAME_UNUSABLE = "name_unusable"


@dataclass(frozen=True, slots=True)
class Finding:
    """One category measured at one board size and blank count."""

    category_id: str
    name: str
    letters: str
    members_eligible: int
    marks_needed: int
    playable: bool
    reason: str = ""
    #: Members that can carry a mark, when a placement was found. Named so a
    #: curator can see the board the measurement claims exists. There may be
    #: more of these than ``marks_needed``: the data can support more letters
    #: than the blank count asks for, and the generator then leaves a tile
    #: deliberately unmarked to hit the target, which is the manufactured
    #: blank the requirements settle on.
    tiles: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "category_id": self.category_id,
            "name": self.name,
            "letters": self.letters,
            "members_eligible": self.members_eligible,
            "marks_needed": self.marks_needed,
            "playable": self.playable,
            "reason": self.reason,
            "tiles": list(self.tiles),
        }


@dataclass(frozen=True, slots=True)
class Candidate:
    """A category and the member forms that could be its tiles."""

    category_id: str
    form: SurfaceForm
    members: tuple[SurfaceForm, ...]


def load(
    repos: GraphRepositories,
    *,
    difficulty: str = "any",
    policy: SurfacePolicy | None = None,
) -> tuple[list[Candidate], SurfaceIndex]:
    """Every active lexical category with its frequency-passing members.

    The surface index is returned alongside, because it is the record of what
    the letters were: a measurement whose letters cannot be re-read is not
    reproducible, and the index's fingerprint is what ties a number here to
    the rules that produced it.
    """
    allowed = None
    if difficulty != "any":
        allowed = frozenset(
            band.name for band in DIFFICULTY_TO_FREQUENCY[DifficultyBand(difficulty.upper())]
        )

    entities = {e.id: e for e in repos.entities.active()}

    def eligible(entity_id: str) -> bool:
        entity = entities.get(entity_id)
        if entity is None:
            return False
        if allowed is None:
            return True
        band = entity.frequency_band
        # An unscored word is never a tile, which is the game's own filter.
        return band is not None and band.name in allowed

    index = SurfaceIndex(policy=policy)
    candidates: list[Candidate] = []
    for category in repos.categories.live(LEXICAL_TAXONOMY):
        if category.status is not ReviewStatus.ACTIVE:
            continue
        member_ids = sorted(
            {
                rel.subject_id
                for rel in repos.relationships.by_object(
                    category.id, MEMBERSHIP_PREDICATE
                )
                if rel.status is ReviewStatus.ACTIVE and eligible(rel.subject_id)
            }
        )
        views = [_view(entities[member_id]) for member_id in member_ids]
        index.add(views)
        candidates.append(
            Candidate(
                category_id=category.id,
                form=form_for(_category_view(category), index.policy),
                members=tuple(index.form(member_id) for member_id in member_ids),
            )
        )
    return candidates, index


def _view(entity):
    """An entity as the surface subsystem reads it.

    The subsystem takes an ``EntityView`` because that is what a game has;
    this tool reads graph records directly, so it builds the smallest view
    the letters need rather than assembling a whole content result.
    """
    from puzzlegen.content.query import EntityView

    return EntityView(
        entity_id=entity.id,
        name=entity.canonical_name,
        aliases=tuple(getattr(entity, "aliases", ()) or ()),
    )


def _category_view(category):
    from puzzlegen.content.query import EntityView

    return EntityView(entity_id=category.id, name=category.canonical_name)


def assess(candidate: Candidate, board_size: int, blanks: int) -> Finding:
    """Whether this category can be a board of ``board_size`` with ``blanks``."""
    letters = candidate.form.letters
    marks = len(letters) - blanks
    base = dict(
        category_id=candidate.category_id,
        name=candidate.form.display,
        letters=letters,
        members_eligible=len(candidate.members),
        marks_needed=marks,
    )
    if marks < 1:
        return Finding(**base, playable=False, reason=NAME_UNUSABLE)
    # At least one tile carries no mark, which is a settled rule and the
    # reason the board needs one more tile than it has marks.
    if marks + 1 > board_size:
        return Finding(**base, playable=False, reason=NAME_TOO_LONG)
    if len(candidate.members) < board_size:
        return Finding(**base, playable=False, reason=TOO_FEW_MEMBERS)

    placement = place_marks(letters, blanks, candidate.members)
    if placement is None:
        return Finding(**base, playable=False, reason=LETTERS_UNPLACEABLE)
    return Finding(**base, playable=True, tiles=placement)


def place_marks(
    letters: str, blanks: int, members: tuple[SurfaceForm, ...]
) -> tuple[str, ...] | None:
    """Distinct members that can carry ``len(letters) - blanks`` of them.

    Which letters go unsupplied is the generator's choice, so the measurement
    may not fix it: a name whose one ``q`` no member contains is still
    playable if ``q`` is one of the blanks. So this matches as many letter
    occurrences as it can to distinct members and asks whether at most
    ``blanks`` were left over, rather than deciding the blanks up front.

    The matching is Kuhn's augmenting search, which is exact: a greedy pass
    would refuse a board that exists whenever an early letter takes the only
    tile a later letter could have used.
    """
    # One slot per letter occurrence, not per distinct letter: a name needing
    # two "s" needs two tiles, and keying the matching by letter would let the
    # second occurrence silently reuse the first one's tile.
    slots = sorted(Counter(letters).elements())
    holders = {
        letter: [m.entity_id for m in members if letter in m.letters]
        for letter in set(slots)
    }
    owner: dict[str, int] = {}

    def assign(slot: int, seen: set[str]) -> bool:
        for entity_id in holders[slots[slot]]:
            if entity_id in seen:
                continue
            seen.add(entity_id)
            if entity_id not in owner or assign(owner[entity_id], seen):
                owner[entity_id] = slot
                return True
        return False

    unmatched = 0
    for slot in range(len(slots)):
        if not assign(slot, set()):
            unmatched += 1
    if unmatched > blanks:
        return None
    return tuple(sorted(owner))


def survey(
    candidates: list[Candidate],
    board_sizes: tuple[int, ...],
    blank_counts: tuple[int, ...],
) -> dict[tuple[int, int], list[Finding]]:
    return {
        (board_size, blanks): [
            assess(candidate, board_size, blanks) for candidate in candidates
        ]
        for board_size in board_sizes
        for blanks in blank_counts
    }


def summarise(
    findings: dict[tuple[int, int], list[Finding]], index: SurfaceIndex
) -> dict:
    cells = []
    for (board_size, blanks), rows in sorted(findings.items()):
        playable = [row for row in rows if row.playable]
        reasons = Counter(row.reason for row in rows if not row.playable)
        cells.append(
            {
                "board_size": board_size,
                "blanks": blanks,
                "playable": len(playable),
                "examined": len(rows),
                "refused": dict(sorted(reasons.items())),
                "shortlist": [
                    {
                        "name": row.name,
                        "letters": row.letters,
                        "marks": row.marks_needed,
                        "members": row.members_eligible,
                    }
                    # Shortest name first: the shortest playable names are the
                    # ones a solver could plausibly be asked to spell, and the
                    # probe's finding was that only obscure short names qualify.
                    for row in sorted(playable, key=lambda r: (len(r.letters), r.name))[
                        :SHORTLIST
                    ]
                ],
            }
        )
    return {
        "surface_policy": index.policy.fingerprint(),
        "surface_forms": len(index),
        "cells": cells,
    }


def render(summary: dict) -> str:
    lines = [
        "### missing_letters coverage",
        "",
        f"- surface forms: {summary['surface_forms']}",
        f"- surface policy: {summary['surface_policy'][:12]}",
        "",
        "| tiles | blanks | playable | too long | too few members "
        "| unplaceable | name unusable |",
        "|---|---|---|---|---|---|---|",
    ]
    for cell in summary["cells"]:
        refused = cell["refused"]
        lines.append(
            "| {tiles} | {blanks} | {playable}/{examined} | {long} | {few} "
            "| {un} | {short} |".format(
                tiles=cell["board_size"],
                blanks=cell["blanks"],
                playable=cell["playable"],
                examined=cell["examined"],
                long=refused.get(NAME_TOO_LONG, 0),
                few=refused.get(TOO_FEW_MEMBERS, 0),
                un=refused.get(LETTERS_UNPLACEABLE, 0),
                short=refused.get(NAME_UNUSABLE, 0),
            )
        )
    for cell in summary["cells"]:
        if not cell["shortlist"]:
            continue
        lines.extend(
            [
                "",
                f"**{cell['board_size']} tiles, {cell['blanks']} blanks**: "
                + ", ".join(
                    f"{row['name']} ({row['marks']} marks, {row['members']} members)"
                    for row in cell["shortlist"]
                ),
            ]
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--difficulty",
        default="any",
        choices=("any", "easy", "medium", "hard", "expert"),
        help="the frequency filter a board of that difficulty would apply",
    )
    parser.add_argument("--tiles", type=int, nargs="+", default=list(BOARD_SIZES))
    parser.add_argument("--blanks", type=int, nargs="+", default=list(BLANK_COUNTS))
    parser.add_argument("--json", type=Path, help="write the full findings here")
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"no graph at {args.db}", file=sys.stderr)
        return 2
    if any(blanks < 2 for blanks in args.blanks):
        # The requirements set two as the floor, so measuring below it would
        # report boards the game will not generate.
        print("the blank floor is 2", file=sys.stderr)
        return 2

    repos = GraphRepositories(SqliteDocumentStore(args.db))
    candidates, index = load(repos, difficulty=args.difficulty)
    findings = survey(candidates, tuple(args.tiles), tuple(args.blanks))
    summary = summarise(findings, index)
    print(render(summary))

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "summary": summary,
                    "findings": {
                        f"{board_size}x{blanks}": [row.as_dict() for row in rows]
                        for (board_size, blanks), rows in sorted(findings.items())
                    },
                },
                indent=2,
            )
        )
    return 0 if any(cell["playable"] for cell in summary["cells"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
