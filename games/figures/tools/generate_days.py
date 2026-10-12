#!/usr/bin/env python3
"""Generate real days against a snapshot and say what came out.

This is the tool that turns "the engine can make this game" into something a
reader can check. It drives the same pipeline production uses, with the
verifier configured as a generation gate, over a run of consecutive days, and
reports each one: the board, its trio, how many trios the enumeration found,
whether the enumeration was complete, the difficulty it measured and the band
that landed in.

Nothing is published and nothing is written to the graph: the engine's own
records go to an in-memory store and the snapshot is only read.

    python tools/generate_days.py --db build/graph-symbolic.sqlite --days 30
    python tools/generate_days.py --db build/graph-symbolic.sqlite --difficulty hard

This game has no content question of the kind games one and two have, so there
is no coverage tool beside this one. Its content is a declared attribute space
that the provider generates whole, so "can the content serve a board" has a
one-line answer -- eighty-one figures, every board size fits inside them -- and
the only question worth asking daily is the one here: does a board form, does
it hold exactly one trio, and does it land in the band it was asked for.

``--strict`` is the default rather than a flag, which is the opposite of game
two's tool. There, a board outside its band was worth reporting because
whether boards formed at all was the open question. Here boards always form,
so a board outside its band is the only interesting failure and refusing it is
what makes the run a test of the difficulty scale. ``--lenient`` turns it off.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from puzzlegen.content.policy import ContentPolicy, PolicyService
from puzzlegen.content.service import ContentService
from puzzlegen.core.types import DifficultyBand
from puzzlegen.engine.pipeline import GenerationPipeline
from puzzlegen.engine.registry import GameRegistry
from puzzlegen.engine.storage import EngineRepositories
from puzzlegen.engine.verifier import PuzzleVerifier
from puzzlegen.graph import (
    GraphRepositories,
    InMemoryDocumentStore,
    SqliteDocumentStore,
)
from puzzlegen_figures.descriptor import BOARD_SIZES, GAME_ID, board_size_for
from puzzlegen_figures.generate import last_rejections
from puzzlegen_figures.plugin import ShapeTrioGame

DEFAULT_DB = Path("build/graph-symbolic.sqlite")

#: The floor the platform applies to anything a game may use.
PLATFORM_CONFIDENCE = 0.7


@dataclass(frozen=True)
class DayResult:
    day: str
    #: The size the board was actually built at, read off the board rather
    #: than recomputed from the date: a day tries its drawn size first and the
    #: other after it, and reporting the draw as the result would claim sizes
    #: the content never served.
    board_size: int
    generated: bool
    #: The size the day drew, kept so the gap between asked and served stays
    #: visible in the JSON rather than being silently corrected away.
    drawn_size: int = 0
    trio: tuple[str, ...] = ()
    varying: tuple[str, ...] = ()
    near_misses: int | None = None
    trios: int | None = None
    completeness: str | None = None
    triples_examined: int | None = None
    difficulty: float | None = None
    measured_band: str | None = None
    on_target: bool | None = None
    tiles: tuple[str, ...] = ()
    reason: str = ""


class SnapshotError(RuntimeError):
    """The database holds no snapshot this run can use."""


def pick_snapshot(repos: GraphRepositories, label: str | None):
    """The named snapshot, or the newest sealed one.

    Generation refuses an unsealed snapshot, because a reproducibility claim
    made against content that can still change is false, so an unsealed one is
    never chosen by default.
    """
    if label is not None:
        meta = repos.snapshots.by_label(label)
        if meta is None:
            raise SnapshotError(f"no snapshot labelled {label!r}")
        if not meta.sealed:
            raise SnapshotError(f"snapshot {label!r} is not sealed")
        return meta
    sealed = repos.snapshots.sealed()
    if not sealed:
        raise SnapshotError("the database holds no sealed snapshot")
    return max(sealed, key=lambda meta: meta.created_at)


def describe_failure(outcome) -> str:
    """Why a day failed, latest stage first.

    The stages run in order (content, the game's own screening, the engine's,
    then verification), and the late ones are the ones worth reading.
    """
    trace = outcome.trace
    parts = [trace.failure_reason or "no reason recorded"]

    staged = [
        (count, f"{tally.stage}: {reason}")
        for tally in trace.candidates_rejected
        for reason, count in tally.reasons.items()
    ]
    if staged:
        parts.append(
            "; ".join(f"{label} ({n})" for n, label in sorted(staged, reverse=True)[:3])
        )

    game = last_rejections()
    if game:
        own = board_size_for(outcome.trace.day_key)
        prefix = f"size {own}:"
        own_first = sorted(
            game.items(), key=lambda kv: (not kv[0].startswith(prefix), -kv[1])
        )
        parts.append("game: " + ", ".join(f"{r} {n}" for r, n in own_first[:4]))

    content = Counter(trace.content_rejections)
    if content:
        parts.append(
            "content: " + ", ".join(f"{r} {n}" for r, n in content.most_common(3))
        )
    return "; ".join(parts)


#: Count words to the digit a run prints. Here rather than imported from the
#: provider, because this is a display choice in a diagnostic and the provider
#: is content; what it must not do is guess from the first letter, since "two"
#: and "three" share one.
_COUNTS = {"one": "1", "two": "2", "three": "3"}


def _count_digit(count: str) -> str:
    return _COUNTS.get(count, "?")


def result_of(day: str, outcome) -> DayResult:
    drawn = board_size_for(day)
    if not outcome.succeeded:
        return DayResult(
            day=day,
            board_size=drawn,
            generated=False,
            drawn_size=drawn,
            reason=describe_failure(outcome),
        )
    evaluation = outcome.evaluation
    verification = evaluation.verification if evaluation else None
    payload = outcome.puzzle.payload
    solution = outcome.puzzle.solution
    return DayResult(
        day=day,
        board_size=int(payload["board_size"]),
        generated=True,
        drawn_size=drawn,
        trio=tuple(solution["trio"]),
        varying=tuple(solution.get("varying", ())),
        near_misses=int(solution.get("near_misses", 0)),
        trios=getattr(verification, "solution_count", None),
        completeness=getattr(
            getattr(verification, "completeness", None), "name", None
        ),
        triples_examined=getattr(verification, "states_examined", None),
        difficulty=(
            round(evaluation.difficulty.score, 3)
            if evaluation and evaluation.difficulty
            else None
        ),
        measured_band=(
            evaluation.measured_band.name
            if evaluation and evaluation.measured_band
            else None
        ),
        on_target=None if evaluation is None else not evaluation.off_target,
        # Glyph, count and colour together. The glyph carries shape and
        # fill and nothing else, so a run abbreviated to the glyph alone
        # would show two different figures identically, and a run nobody can
        # read is a run nobody checks. The count is its digit rather than its
        # initial, because "two" and "three" share one.
        tiles=tuple(
            f"{tile['glyph']}{_count_digit(tile['count'])}{tile['colour'][0]}"
            for tile in payload["tiles"]
        ),
    )


def run(
    db: Path,
    *,
    start: dt.date,
    days: int,
    difficulty: DifficultyBand = DifficultyBand.MEDIUM,
    label: str | None = None,
    now: dt.datetime | None = None,
    strict: bool = True,
) -> list[DayResult]:
    now = now or dt.datetime.now(dt.timezone.utc)
    repos = GraphRepositories(SqliteDocumentStore(db))
    try:
        meta = pick_snapshot(repos, label)
        content = ContentService(
            repos,
            policy=PolicyService(
                ContentPolicy(name="platform", minimum_confidence=PLATFORM_CONFIDENCE)
            ),
            snapshot=meta,
            now=now,
        )
        registry = GameRegistry()
        registry.register(ShapeTrioGame())
        registry.activate(GAME_ID, day_key=start.isoformat())
        pipeline = GenerationPipeline(
            content=content,
            engine=EngineRepositories(InMemoryDocumentStore()),
            registry=registry,
            dependencies=repos.dependencies,
            snapshot=meta,
            now=now,
            verifier=PuzzleVerifier(require_on_target=strict),
        )

        results = []
        for offset in range(days):
            day = (start + dt.timedelta(days=offset)).isoformat()
            outcome = pipeline.generate(GAME_ID, day, difficulty_target=difficulty)
            results.append(result_of(day, outcome))
        return results
    finally:
        repos.close()


def by_size(results: list[DayResult]) -> dict[int, tuple[int, int]]:
    """Size to (boards served at it, days that drew it).

    Two axes, deliberately. "How many days drew twelve tiles" and "how many
    twelve-tile boards exist" are different questions, and conflating them is
    what would let a content set serving only nine report boards at both.
    """
    served = {size: 0 for size in BOARD_SIZES}
    drawn = {size: 0 for size in BOARD_SIZES}
    for result in results:
        drawn[result.drawn_size or result.board_size] += 1
        if result.generated:
            served[result.board_size] += 1
    return {size: (served[size], drawn[size]) for size in BOARD_SIZES}


def render(results: list[DayResult]) -> str:
    lines = []
    for r in results:
        if r.generated:
            unique = "one trio" if r.trios == 1 else f"{r.trios} trios"
            target = "" if r.on_target else " OFF TARGET"
            fell_back = (
                f" (drew {r.drawn_size})"
                if r.drawn_size and r.drawn_size != r.board_size
                else ""
            )
            lines.append(
                f"{r.day}  {r.board_size} tiles{fell_back}  BOARD  {unique}, "
                f"{r.completeness}, {r.triples_examined} triples  "
                f"difficulty {r.difficulty} ({r.measured_band}){target}"
            )
            lines.append(
                f"    trio varies on {', '.join(r.varying) or 'nothing'}; "
                f"{r.near_misses} near misses"
            )
            lines.append(f"    tiles: {' '.join(r.tiles)}")
        else:
            lines.append(f"{r.day}  {r.board_size} tiles  none   {r.reason}")

    made = sum(r.generated for r in results)
    lines.append("")
    lines.append(f"{made} of {len(results)} days generated a board")
    boards = {tuple(r.tiles) for r in results if r.generated}
    lines.append(f"{len(boards)} distinct boards")
    varying = Counter(len(r.varying) for r in results if r.generated)
    lines.append(
        "trio varied on: "
        + ", ".join(f"{n} attributes x{c}" for n, c in sorted(varying.items()))
    )
    lines.append("tiles  drew  served")
    for size, (built, total) in by_size(results).items():
        lines.append(f"{size:>5}  {total:>4}  {built:>6}")
    lines.append(
        "drew = days whose date chose that size; served = boards actually "
        "built at it."
    )
    failures = Counter(r.reason.split(";")[0] for r in results if not r.generated)
    if failures:
        lines.append("")
        lines.append("why days failed:")
        for reason, count in failures.most_common(5):
            lines.append(f"  {count:>3}  {reason}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--start", type=dt.date.fromisoformat, default=dt.date.today())
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument(
        "--difficulty",
        choices=[band.name.lower() for band in DifficultyBand],
        default="medium",
    )
    parser.add_argument(
        "--label", default=None, help="snapshot label; default the newest sealed"
    )
    parser.add_argument(
        "--lenient",
        action="store_true",
        help="accept boards outside the requested band instead of refusing them",
    )
    parser.add_argument(
        "--now",
        type=dt.datetime.fromisoformat,
        default=None,
        help=(
            "timezone aware ISO time the run treats as now. Content past its "
            "review date is refused as stale, so a snapshot built weeks ago "
            "needs the time it was built to be reproduced."
        ),
    )
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    if not args.db.exists():
        parser.error(f"no such database: {args.db}")
    if args.days < 1:
        parser.error("--days must be at least 1")
    if args.now is not None and args.now.tzinfo is None:
        parser.error("--now must be timezone aware")

    try:
        results = run(
            args.db,
            start=args.start,
            days=args.days,
            difficulty=DifficultyBand[args.difficulty.upper()],
            label=args.label,
            now=args.now,
            strict=not args.lenient,
        )
    except SnapshotError as error:
        print(str(error), file=sys.stderr)
        return 2

    print(render(results))
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps([asdict(r) for r in results], indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"json: {args.json}")
    return 0 if all(r.generated for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
