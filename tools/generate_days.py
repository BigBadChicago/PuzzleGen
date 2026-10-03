#!/usr/bin/env python3
"""Generate real days against a snapshot and say what came out.

``measure_coverage.py`` answers whether a board is structurally possible. This
answers the question the handoff actually set for the end of phase 7: does a
real board generate, verify as unique, and score. It drives the same pipeline
production uses, with the verifier configured as a generation gate, over a run
of consecutive days, and reports each one.

Nothing is published and nothing is written: the engine's own records go to an
in-memory store and the snapshot is only read.

    python tools/generate_days.py --db content/graph.sqlite --days 30

Each day's group size comes from its date alone, so a run shows how many of the
five sizes the content can currently serve. That is the number that decides
whether a daily product has a board every day or one day in five.

By default a board that lands outside the requested difficulty band is still
reported, with its measured band, because whether boards form at all is the
first question. ``--strict`` refuses them as the production runner does.
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
from puzzlegen.games.grouping.descriptor import GAME_ID, GROUP_SIZES, group_size_for
from puzzlegen.games.grouping.generate import last_rejections
from puzzlegen.games.grouping.plugin import GroupingGame
from puzzlegen.graph import GraphRepositories, InMemoryDocumentStore, SqliteDocumentStore

DEFAULT_DB = Path("content/graph.sqlite")

#: The floor the platform applies to anything a game may use.
PLATFORM_CONFIDENCE = 0.7


@dataclass(frozen=True)
class DayResult:
    day: str
    #: The size the board was actually built at. Not the size the day drew:
    #: a day tries its drawn size first and every other size after it, and
    #: reporting the draw here let five days claim sizes 5 through 9 while
    #: every one of them examined the same 5005 states.
    group_size: int
    generated: bool
    #: The size the day drew, kept so the gap between asked and served stays
    #: visible in the JSON rather than being silently corrected away.
    drawn_size: int = 0
    solutions: int | None = None
    completeness: str | None = None
    states: int | None = None
    difficulty: float | None = None
    measured_band: str | None = None
    on_target: bool | None = None
    hidden: str | None = None
    visible: tuple[str, ...] = ()
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

    The stages run in order (content, the game's own assembly, the engine's
    screening, verification), and the late ones are the ones worth reading: a
    content count of several thousand words dropped for frequency is true on
    every day and explains nothing about why this one failed. Showing only the
    commonest reasons overall put that number first and hid a verification
    refusal of "126 distinct solutions" behind it.
    """
    trace = outcome.trace
    parts = [trace.failure_reason or "no reason recorded"]

    staged = [
        (count, f"{tally.stage}: {reason}")
        for tally in trace.candidates_rejected
        for reason, count in tally.reasons.items()
    ]
    if staged:
        parts.append("; ".join(f"{label} ({n})" for n, label in sorted(staged, reverse=True)[:3]))

    game = last_rejections()
    if game:
        # The game keys its refusals by size ("size 5: not_disjoint"). A day
        # tries its own size first and the others as fallback, and the day's
        # own size is the one worth reading: the fallbacks failing is expected
        # and drowns it out under a plain most-common sort. So the day's size
        # leads, then the rest by count.
        own = group_size_for(outcome.trace.day_key)
        prefix = f"size {own}:"
        own_first = sorted(
            game.items(),
            key=lambda kv: (not kv[0].startswith(prefix), -kv[1]),
        )
        parts.append("game: " + ", ".join(f"{r} {n}" for r, n in own_first[:4]))

    content = Counter(trace.content_rejections)
    if content:
        parts.append("content: " + ", ".join(f"{r} {n}" for r, n in content.most_common(3)))
    return "; ".join(parts)


def served_size(outcome, drawn: int) -> int:
    """The size the board was built at, read from the board itself.

    The engine already carries it; recomputing the draw instead is the same
    recompute-rather-than-read mistake the architecture warns about, and it is
    what made a diagnostic disagree with the engine it was measuring.
    """
    payload = getattr(getattr(outcome, "puzzle", None), "payload", None)
    if payload is None:
        return drawn
    return int(payload.get("group_size", drawn))


def result_of(day: str, outcome) -> DayResult:
    drawn = group_size_for(day)
    if not outcome.succeeded:
        # Nothing was built, so no size was served; the draw is the only honest
        # number and the failure is attributable to it.
        return DayResult(
            day=day,
            group_size=drawn,
            generated=False,
            drawn_size=drawn,
            reason=describe_failure(outcome),
        )
    evaluation = outcome.evaluation
    verification = evaluation.verification if evaluation else None
    solution = outcome.puzzle.solution
    return DayResult(
        day=day,
        group_size=served_size(outcome, drawn),
        generated=True,
        drawn_size=drawn,
        solutions=getattr(verification, "solution_count", None),
        completeness=getattr(getattr(verification, "completeness", None), "name", None),
        states=getattr(verification, "states_examined", None),
        difficulty=round(evaluation.difficulty.score, 3) if evaluation and evaluation.difficulty else None,
        measured_band=evaluation.measured_band.name if evaluation and evaluation.measured_band else None,
        on_target=None if evaluation is None else not evaluation.off_target,
        hidden=solution["hidden"]["category"],
        visible=tuple(group["category"] for group in solution["groups"]),
    )


def run(
    db: Path,
    *,
    start: dt.date,
    days: int,
    difficulty: DifficultyBand = DifficultyBand.MEDIUM,
    label: str | None = None,
    now: dt.datetime | None = None,
    strict: bool = False,
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
        registry.register(GroupingGame())
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

    Two axes, deliberately. "How many days drew size 9" and "how many size 9
    boards exist" are different questions, and conflating them is what let a
    snapshot serving no board above size 6 report boards at every size.
    """
    served = {size: 0 for size in GROUP_SIZES}
    drawn = {size: 0 for size in GROUP_SIZES}
    for result in results:
        drawn[result.drawn_size or result.group_size] += 1
        if result.generated:
            served[result.group_size] += 1
    return {size: (served[size], drawn[size]) for size in GROUP_SIZES}


def render(results: list[DayResult]) -> str:
    lines = []
    for r in results:
        if r.generated:
            unique = "unique" if r.solutions == 1 else f"{r.solutions} solutions"
            target = "" if r.on_target else " OFF TARGET"
            # The draw is shown only when it differs, so a run that served
            # every day at its own size reads exactly as it did before and a
            # run leaning on the fallback cannot be mistaken for one.
            fell_back = (
                f" (drew {r.drawn_size})"
                if r.drawn_size and r.drawn_size != r.group_size
                else ""
            )
            lines.append(
                f"{r.day}  size {r.group_size}{fell_back}  BOARD  {unique}, "
                f"{r.completeness}, {r.states} states  "
                f"difficulty {r.difficulty} ({r.measured_band}){target}"
            )
            lines.append(f"    hidden: {r.hidden}")
            lines.append(f"    groups: {', '.join(r.visible)}")
        else:
            lines.append(f"{r.day}  size {r.group_size}  none   {r.reason}")

    made = sum(r.generated for r in results)
    lines.append("")
    lines.append(f"{made} of {len(results)} days generated a board")
    lines.append("size  drew  served")
    for size, (built, total) in by_size(results).items():
        lines.append(f"{size:>4}  {total:>4}  {built:>6}")
    lines.append(
        "drew = days whose date chose that size; served = boards actually "
        "built at it. A size with draws and no boards is one the content "
        "cannot currently supply."
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
    parser.add_argument("--label", default=None, help="snapshot label; default the newest sealed")
    parser.add_argument("--strict", action="store_true", help="refuse boards outside the requested band")
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
            strict=args.strict,
        )
    except SnapshotError as error:
        print(str(error), file=sys.stderr)
        return 2

    print(render(results))
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps([asdict(r) for r in results], indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"json: {args.json}")
    return 0 if all(r.generated for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
