#!/usr/bin/env python3
"""Generate real days against a snapshot and say what came out.

Drives the same pipeline production uses, with the verifier configured as a
generation gate, over a run of consecutive days, and reports each one: the
code, how many clue rows and stated facts the board shows, how many codes
survived every clue, whether the enumeration was complete, the difficulty it
measured and the band that landed in.

Nothing is published and nothing is written to the graph: the engine's own
records go to an in-memory store and the snapshot is only read.

    python tools/generate_days.py --db build/graph-symbolic.sqlite --days 30
    python tools/generate_days.py --db build/graph-symbolic.sqlite --difficulty hard

The number to read is the survivor count: it is 1 on every board, by the
construction and then again by an enumeration of the whole space, and anything
else is the contract broken. The clue count is the second: it is what the
difficulty model reads and what the band's lever moves, so a run whose easy
days show as many clue rows as its hard ones is a run where the lever stopped
working.

``--strict`` is the default. A board outside its band is the only interesting
failure here, because boards themselves nearly always form, and refusing them
is what makes a run a test of the difficulty scale. ``--lenient`` turns it
off.
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
from puzzlegen_digits.descriptor import CODE_LENGTHS, GAME_ID, code_length_for
from puzzlegen_digits.generate import last_rejections
from puzzlegen_digits.plugin import DigitLockGame

DEFAULT_DB = Path("build/graph-symbolic.sqlite")

#: The floor the platform applies to anything a game may use.
PLATFORM_CONFIDENCE = 0.7


@dataclass(frozen=True)
class DayResult:
    day: str
    #: The length the board was actually built at, read off the board rather
    #: than recomputed from the date: a day tries its drawn length first and
    #: the other after it, and reporting the draw as the result would claim
    #: lengths the construction never served.
    code_length: int
    generated: bool
    #: The length the day drew, kept so the gap between asked and served
    #: stays visible in the JSON rather than being silently corrected away.
    drawn_length: int = 0
    code: str = ""
    clues: int | None = None
    facts: int | None = None
    before_last: int | None = None
    survivors: int | None = None
    completeness: str | None = None
    codes_examined: int | None = None
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
        own = code_length_for(outcome.trace.day_key)
        prefix = f"length {own}:"
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


def result_of(day: str, outcome) -> DayResult:
    drawn = code_length_for(day)
    if not outcome.succeeded:
        return DayResult(
            day=day,
            code_length=drawn,
            generated=False,
            drawn_length=drawn,
            reason=describe_failure(outcome),
        )
    evaluation = outcome.evaluation
    verification = evaluation.verification if evaluation else None
    payload = outcome.puzzle.payload
    solution = outcome.puzzle.solution
    return DayResult(
        day=day,
        code_length=int(payload["code_length"]),
        generated=True,
        drawn_length=drawn,
        code="".join(str(digit) for digit in solution["code"]),
        clues=int(payload.get("clue_count", 0)),
        facts=int(payload.get("fact_count", 0)),
        before_last=int(solution.get("before_last", 0)),
        survivors=getattr(verification, "solution_count", None),
        completeness=getattr(
            getattr(verification, "completeness", None), "name", None
        ),
        codes_examined=getattr(verification, "states_examined", None),
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
        # The clue rows as a solver reads them: the guess, then its two
        # counts. A run abbreviated further would not be checkable by hand,
        # and checking one by hand is the point of printing them.
        tiles=tuple(
            "".join(str(digit) for digit in row["guess"])
            + f" {row['exact']}/{row['misplaced']}"
            for row in payload["clues"]
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
        registry.register(DigitLockGame())
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
    """Code length to (boards served at it, days that drew it).

    Two axes, deliberately. "How many days drew four digits" and "how many
    four-digit boards exist" are different questions, and conflating them is
    what would hide a length the construction cannot serve.
    """
    served = {length: 0 for length in CODE_LENGTHS}
    drawn = {length: 0 for length in CODE_LENGTHS}
    for result in results:
        drawn[result.drawn_length or result.code_length] += 1
        if result.generated:
            served[result.code_length] += 1
    return {length: (served[length], drawn[length]) for length in CODE_LENGTHS}


def render(results: list[DayResult]) -> str:
    lines = []
    for r in results:
        if r.generated:
            unique = (
                "one code" if r.survivors == 1 else f"{r.survivors} codes survive"
            )
            target = "" if r.on_target else " OFF TARGET"
            fell_back = (
                f" (drew {r.drawn_length})"
                if r.drawn_length and r.drawn_length != r.code_length
                else ""
            )
            lines.append(
                f"{r.day}  {r.code_length} digits{fell_back}  BOARD  {unique}, "
                f"{r.completeness}, {r.codes_examined} codes  "
                f"difficulty {r.difficulty} ({r.measured_band}){target}"
            )
            lines.append(
                f"    code {r.code}; {r.clues} clue rows, {r.facts} stated "
                f"facts, {r.before_last} alive before the last clue"
            )
            lines.append(f"    clues: {' | '.join(r.tiles)}")
        else:
            lines.append(f"{r.day}  {r.code_length} digits  none   {r.reason}")

    made = sum(r.generated for r in results)
    lines.append("")
    lines.append(f"{made} of {len(results)} days generated a board")
    boards = {r.code for r in results if r.generated}
    lines.append(f"{len(boards)} distinct codes")
    clues = Counter(r.clues for r in results if r.generated)
    lines.append(
        "clue rows: " + ", ".join(f"{n} x{c}" for n, c in sorted(clues.items()))
    )
    alive = {r.survivors for r in results if r.generated}
    lines.append(f"survivors per board: {sorted(alive)}")
    lines.append("digits  drew  served")
    for length, (built, total) in by_size(results).items():
        lines.append(f"{length:>6}  {total:>4}  {built:>6}")
    lines.append(
        "drew = days whose date chose that length; served = boards actually "
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
