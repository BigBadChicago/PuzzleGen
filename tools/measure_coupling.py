"""Measure cross-board information flow over a run of days.

One board per installed game per day, then every ordered pair, by exact
enumeration. The question it answers is the one nobody had an answer to:
whether the games a day already serves tell a player anything about each
other.

A report, not a gate. Nothing in the project has yet declared what a daily
set's coupling should be, and a gate against an undeclared target would be
asserting a product decision. Pass ``--require-independent`` to turn the same
run into the gate once that decision exists: every ordered pair must then
provably leak nothing, and a game that cannot report its solution space fails
rather than passing as unmeasured.

Games are discovered through the entry point group, never listed here, so
adding one needs no edit.

    python3 tools/measure_coupling.py --db build/graph-symbolic.sqlite \\
        --start 2026-10-01 --days 30 --now 2026-10-10T00:00:00+00:00 \\
        --json build/symbolic-coupling-30.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from puzzlegen.content.policy import ContentPolicy, PolicyService
from puzzlegen.core.errors import ConflictError
from puzzlegen.content.service import ContentService
from puzzlegen.core.types import DifficultyBand
from puzzlegen.engine.coupling import (
    CouplingReport,
    check_day,
    independent_targets,
    spaces_for_day,
)
from puzzlegen.engine.pipeline import GenerationPipeline
from puzzlegen.engine.registry import GameRegistry
from puzzlegen.engine.storage import EngineRepositories
from puzzlegen.engine.verifier import PuzzleVerifier
from puzzlegen.graph.memory_store import InMemoryDocumentStore
from puzzlegen.graph.repositories import GraphRepositories
from puzzlegen.graph.sqlite_store import SqliteDocumentStore

class SnapshotError(RuntimeError):
    """The database holds no snapshot this run can generate against."""


#: The confidence floor the platform generates at. Matches each game's own day
#: tool, so a board measured here is the board that tool would serve.
PLATFORM_CONFIDENCE = 0.6


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


def run(
    db: Path,
    *,
    start: dt.date,
    days: int,
    difficulty: DifficultyBand = DifficultyBand.MEDIUM,
    label: str | None = None,
    now: dt.datetime | None = None,
    require_independent: bool = False,
    only: tuple[str, ...] = (),
) -> tuple[list[CouplingReport], tuple[str, ...]]:
    now = now or dt.datetime.now(dt.timezone.utc)
    repos = GraphRepositories(SqliteDocumentStore(db))
    try:
        meta = pick_snapshot(repos, label)
        content = ContentService(
            repos,
            policy=PolicyService(
                ContentPolicy(
                    name="platform", minimum_confidence=PLATFORM_CONFIDENCE
                )
            ),
            snapshot=meta,
            now=now,
        )
        registry = GameRegistry()
        discovered = registry.discover()
        if not discovered:
            raise SnapshotError(
                "no game advertises the puzzlegen.games entry point group, so "
                "there are no boards to measure"
            )
        wanted = [g for g in discovered if not only or g in only]
        unknown = sorted(set(only) - set(discovered))
        if unknown:
            raise SnapshotError(f"no such installed game(s): {unknown}")

        # A day has a bounded number of active slots and more games than slots
        # can be installed, so activation can refuse. The games that do not
        # fit are named in the report rather than dropped: a coupling figure
        # over a subset of a day's boards is a figure about that subset, and
        # saying which boards were left out is the difference between a
        # measurement and a claim.
        game_ids: list[str] = []
        not_activated: list[str] = []
        for game_id in wanted:
            try:
                registry.activate(game_id, day_key=start.isoformat())
            except ConflictError:
                not_activated.append(game_id)
            else:
                game_ids.append(game_id)
        if not game_ids:
            raise SnapshotError(
                "no game could be activated for this day, so there are no "
                "boards to measure"
            )
        pipeline = GenerationPipeline(
            content=content,
            engine=EngineRepositories(InMemoryDocumentStore()),
            registry=registry,
            dependencies=repos.dependencies,
            snapshot=meta,
            now=now,
            # Off target is accepted here on purpose. This measures coupling,
            # and a day that served a board outside its band still served it;
            # refusing it would measure fewer pairs than a player sees.
            verifier=PuzzleVerifier(require_on_target=False),
        )

        reports: list[CouplingReport] = []
        for offset in range(days):
            day = (start + dt.timedelta(days=offset)).isoformat()
            boards = {}
            for game_id in game_ids:
                outcome = pipeline.generate(
                    game_id, day, difficulty_target=difficulty
                )
                if outcome.puzzle is not None:
                    boards[game_id] = (
                        outcome.puzzle.payload,
                        outcome.puzzle.solution,
                    )
            spaces = spaces_for_day(boards)
            targets = (
                independent_targets(spaces) if require_independent else ()
            )
            reports.append(check_day(day, spaces, targets))
        return reports, tuple(not_activated)
    finally:
        repos.close()


def as_documents(reports: list[CouplingReport]) -> list[dict]:
    return [
        {
            "day": report.day_key,
            "boards": sorted(
                {m.source_game_id for m in report.measurements}
                | {m.target_game_id for m in report.measurements}
            ),
            "measurements": [
                {
                    "from": m.source_game_id,
                    "to": m.target_game_id,
                    "leak": m.leak,
                    "before": m.space_before,
                    "after": m.space_after,
                    "facts_applied": m.facts_applied,
                    "detail": m.detail,
                }
                for m in report.measurements
            ],
            "violations": [
                {
                    "from": target.source_game_id,
                    "to": target.target_game_id,
                    "declared": [target.minimum, target.maximum],
                    "measured": measurement.leak,
                }
                for target, measurement in report.violations
            ],
        }
        for report in reports
    ]


def as_document(
    reports: list[CouplingReport], not_activated: tuple[str, ...] = ()
) -> dict:
    """The artifact. Carries what was not measured as well as what was."""
    return {
        "record": "coupling_report",
        "days": as_documents(reports),
        "not_measured": list(not_activated),
    }


def render(
    reports: list[CouplingReport], not_activated: tuple[str, ...] = ()
) -> str:
    measured = [m for r in reports for m in r.measurements]
    known = [m for m in measured if m.known]
    unknown = [m for m in measured if not m.known]
    leaking = [m for m in known if not m.independent]
    lines = [
        f"days          : {len(reports)}",
        f"pairs measured: {len(measured)}",
        f"known         : {len(known)}",
        f"unknown       : {len(unknown)}",
        f"non-zero leak : {len(leaking)}",
    ]
    if known:
        lines.append(
            f"largest leak  : {max(m.leak for m in known):.4f}"
        )
    for measurement in leaking[:20]:
        lines.append(f"  LEAK {measurement.describe()}")
    for measurement in unknown[:20]:
        lines.append(f"  UNKNOWN {measurement.describe()}")
    for game_id in not_activated:
        lines.append(
            f"  NOT MEASURED {game_id}: the day's active slots were full, so "
            "this game's boards are not in the figures above"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument(
        "--start", type=dt.date.fromisoformat, default=dt.date.today()
    )
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument(
        "--difficulty",
        choices=[band.name.lower() for band in DifficultyBand],
        default="medium",
    )
    parser.add_argument("--label", default=None)
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
        "--game",
        action="append",
        default=None,
        metavar="GAME_ID",
        help=(
            "restrict to these installed games, repeatable. Without it every "
            "installed game is measured, up to the day's active slot limit."
        ),
    )
    parser.add_argument(
        "--require-independent",
        action="store_true",
        help=(
            "fail unless every ordered pair provably leaks nothing. A game "
            "that cannot report its solution space fails rather than passing "
            "as unmeasured."
        ),
    )
    args = parser.parse_args(argv)

    if not args.db.exists():
        parser.error(f"no such database: {args.db}")
    if args.days < 1:
        parser.error("--days must be at least 1")
    if args.now is not None and args.now.tzinfo is None:
        parser.error("--now must be timezone aware")

    try:
        reports, not_activated = run(
            args.db,
            start=args.start,
            days=args.days,
            difficulty=DifficultyBand[args.difficulty.upper()],
            label=args.label,
            now=args.now,
            require_independent=args.require_independent,
            only=tuple(args.game or ()),
        )
    except SnapshotError as error:
        print(str(error), file=sys.stderr)
        return 2

    print(render(reports, not_activated))
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                as_document(reports, not_activated), indent=2, sort_keys=True
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"json: {args.json}")

    failed = [report for report in reports if not report.passes]
    if failed:
        print(
            f"\n{len(failed)} day(s) violate a declared coupling target:",
            file=sys.stderr,
        )
        for report in failed:
            for line in report.describe():
                if line.startswith("VIOLATION"):
                    print(f"  {report.day_key} {line}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
