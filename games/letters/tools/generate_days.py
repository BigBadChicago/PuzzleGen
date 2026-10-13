#!/usr/bin/env python3
"""Generate real days against a snapshot and say what came out.

``tools/measure_letters_coverage.py`` answers whether a board is structurally
possible: can a category's name be placed on its own members. This answers the
question that follows: does a real board generate, verify as the only reading
of its tiles, and score inside the band it was asked for. It drives the same
pipeline production uses, with the verifier configured as a generation gate,
over a run of consecutive days, and reports each one.

Nothing is published and nothing is written: the engine's own records go to an
in-memory store and the snapshot is only read.

    python tools/generate_days.py --db build/graph-A2.sqlite --days 30
    python tools/generate_days.py --db build/graph-A2.sqlite --difficulty hard

Each day's board size comes from its date alone, and the day falls back
through the other sizes in order, so a run shows both what the dates asked for
and what the content could serve. With thirty categories at six tiles and
sixteen at twelve, the gap between those two numbers is the content question
this tool exists to keep visible.

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
from puzzlegen.engine.capabilities import (
    CapabilityObserver,
    check_profile,
    write_profile,
)
from puzzlegen.engine.certify import (
    Disagreement,
    default_registry,
    differential,
)
from puzzlegen.engine.pipeline import GenerationPipeline
from puzzlegen.engine.registry import GameRegistry
from puzzlegen.engine.storage import EngineRepositories
from puzzlegen.engine.verifier import PuzzleVerifier
from puzzlegen.graph import (
    GraphRepositories,
    InMemoryDocumentStore,
    SqliteDocumentStore,
)
from puzzlegen_letters.descriptor import BOARD_SIZES, GAME_ID, board_size_for
from puzzlegen_letters.generate import last_rejections
from puzzlegen_letters.plugin import MissingLettersGame

DEFAULT_DB = Path("build/graph-A2.sqlite")

#: The floor the platform applies to anything a game may use.
PLATFORM_CONFIDENCE = 0.7


@dataclass(frozen=True)
class DayResult:
    day: str
    #: The size the board was actually built at, read off the board rather
    #: than recomputed from the date: a day tries its drawn size first and
    #: every other size after it, and reporting the draw as the result would
    #: claim sizes the content never served.
    board_size: int
    generated: bool
    #: The size the day drew, kept so the gap between asked and served stays
    #: visible in the JSON rather than being silently corrected away.
    drawn_size: int = 0
    answer: str | None = None
    answer_length: int | None = None
    blanks: int | None = None
    marked_tiles: int | None = None
    manufactured_blanks: int | None = None
    readings: int | None = None
    completeness: str | None = None
    words_examined: int | None = None
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
    then verification), and the late ones are the ones worth reading: a
    content count of several thousand words dropped for frequency is true on
    every day and explains nothing about why this one failed.
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
        # The game keys its refusals by size ("size 6: name_too_long"). The
        # day's own size leads, because the fallback sizes failing is expected
        # and would otherwise drown it out under a plain most-common sort.
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
        answer=solution["answer"],
        answer_length=int(payload["answer_length"]),
        blanks=int(payload["blanks"]),
        marked_tiles=int(payload["marked_tiles"]),
        manufactured_blanks=len(solution.get("manufactured_blanks", ())),
        readings=getattr(verification, "solution_count", None),
        completeness=getattr(
            getattr(verification, "completeness", None), "name", None
        ),
        words_examined=getattr(verification, "states_examined", None),
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
        tiles=tuple(
            f"{tile['label']}[{tile['mark']}]" if tile.get("mark") else tile["label"]
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
    strict: bool = False,
    observer: CapabilityObserver | None = None,
    disagreements: list[Disagreement] | None = None,
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
        registry.register(MissingLettersGame())
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

        if observer is not None:
            observer.snapshot_id = meta.id

        results = []
        for offset in range(days):
            day = (start + dt.timedelta(days=offset)).isoformat()
            outcome = pipeline.generate(GAME_ID, day, difficulty_target=difficulty)
            if observer is not None:
                observer.observe_trace(outcome.trace)
            if disagreements is not None:
                # The engine's own check on the board the game just proposed.
                # A disagreement here is the finding the certifier exists for:
                # the generator and the verifier share an author, so only an
                # independent reading can catch them agreeing wrongly.
                found = differential(outcome, default_registry())
                if found is not None:
                    disagreements.append(found)
            results.append(result_of(day, outcome))
        return results
    finally:
        repos.close()


def by_size(results: list[DayResult]) -> dict[int, tuple[int, int]]:
    """Size to (boards served at it, days that drew it).

    Two axes, deliberately. "How many days drew twelve tiles" and "how many
    twelve-tile boards exist" are different questions, and conflating them is
    what would let a content set serving only six report boards at every size.
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
            unique = "one reading" if r.readings == 1 else f"{r.readings} readings"
            target = "" if r.on_target else " OFF TARGET"
            fell_back = (
                f" (drew {r.drawn_size})"
                if r.drawn_size and r.drawn_size != r.board_size
                else ""
            )
            lines.append(
                f"{r.day}  {r.board_size} tiles{fell_back}  BOARD  {unique}, "
                f"{r.completeness}, {r.words_examined} words  "
                f"difficulty {r.difficulty} ({r.measured_band}){target}"
            )
            lines.append(
                f"    answer: {r.answer} ({r.answer_length} letters, "
                f"{r.marked_tiles} marks, {r.blanks} blanks of which "
                f"{r.manufactured_blanks} manufactured)"
            )
            lines.append(f"    tiles: {', '.join(r.tiles)}")
        else:
            lines.append(f"{r.day}  {r.board_size} tiles  none   {r.reason}")

    made = sum(r.generated for r in results)
    lines.append("")
    lines.append(f"{made} of {len(results)} days generated a board")
    answers = Counter(r.answer for r in results if r.generated)
    if answers:
        lines.append(
            f"{len(answers)} distinct answers: "
            + ", ".join(f"{word} {n}" for word, n in answers.most_common(10))
        )
    lines.append("tiles  drew  served")
    for size, (built, total) in by_size(results).items():
        lines.append(f"{size:>5}  {total:>4}  {built:>6}")
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
    parser.add_argument(
        "--label", default=None, help="snapshot label; default the newest sealed"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="refuse boards outside the requested band",
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
    parser.add_argument(
        "--capability-profile",
        type=Path,
        default=None,
        help=(
            "write the observed capability profile here. The profile is "
            "measured from this run's own port usage, never declared, so a "
            "game that starts touching a new taxonomy shows up as a diff "
            "against the committed copy."
        ),
    )
    args = parser.parse_args(argv)

    if not args.db.exists():
        parser.error(f"no such database: {args.db}")
    if args.days < 1:
        parser.error("--days must be at least 1")
    if args.now is not None and args.now.tzinfo is None:
        parser.error("--now must be timezone aware")

    observer = CapabilityObserver(
        game_id=GAME_ID,
        game_version=MissingLettersGame().describe().game_version,
    )
    disagreements: list[Disagreement] = []
    try:
        results = run(
            args.db,
            start=args.start,
            days=args.days,
            difficulty=DifficultyBand[args.difficulty.upper()],
            label=args.label,
            now=args.now,
            strict=args.strict,
            observer=observer,
            disagreements=disagreements,
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
    if args.capability_profile is not None:
        # Written whether or not every day generated. A failed day still
        # consumed content, and a profile that only covered successes would
        # understate exactly the games worth watching.
        profile = observer.profile()
        write_profile(args.capability_profile, profile)
        print(f"capabilities: {args.capability_profile}")
        diff = check_profile(args.capability_profile, profile)
        if diff is not None and not diff.is_empty:
            for line in diff.describe():
                print(f"  capability change: {line}", file=sys.stderr)
    if disagreements:
        print(
            f"\n{len(disagreements)} day(s) where the engine's own check "
            "disagreed with the game's verifier:",
            file=sys.stderr,
        )
        for disagreement in disagreements:
            print(f"  {disagreement.describe()}", file=sys.stderr)
        return 1
    return 0 if all(r.generated for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
