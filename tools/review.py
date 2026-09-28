#!/usr/bin/env python3
"""Review overlay candidates: see the queue, see the evidence, accept, reject.

This tool decides nothing. It appends decisions to the ledger through
``ReviewService`` and prints what the ledger derives. The status writing lives
in the engine because that is where it can be tested and where an invariant
test can prove no tool does it.

The reviewer is recorded, not authenticated. The engine cannot verify who
anybody is, and pretending otherwise would be security theatre inside a
library. What actually enforces single-curator control is that this runs on the
curator's machine against their repository and every decision is a committed
row.

    python tools/review.py queue --db content/graph.sqlite
    python tools/review.py show relationship:... --db content/graph.sqlite \\
        --manifest content/proposals/overlay-2026-09-28.json
    python tools/review.py accept relationship:... --db content/graph.sqlite \\
        --reviewer ada --batch overlay-2026-09-28
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from puzzlegen.content.review import (
    ACCEPT_THRESHOLD,
    ReviewDecisionKind,
    ReviewOutcome,
    ReviewRepository,
    ReviewService,
)
from puzzlegen.core import ids
from puzzlegen.core.errors import ConflictError, ContentError
from puzzlegen.graph import GraphRepositories, SqliteDocumentStore

REVIEWER_ENV = "PUZZLEGEN_REVIEWER"


class SystemClock:
    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc)


def open_service(db: Path, *, threshold: int = ACCEPT_THRESHOLD) -> ReviewService:
    repos = GraphRepositories(SqliteDocumentStore(db))
    return ReviewService(
        repos, repos.attach(ReviewRepository), threshold=threshold, clock=SystemClock()
    )


def load_manifest(path: Path | None) -> dict:
    """Candidate evidence, keyed by subject ref.

    Optional on purpose: the graph knows what was proposed, but only the
    manifest knows why, and a curator who cannot see why should not be asked
    to accept.
    """
    if path is None:
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    return {c["subject_ref"]: c for c in document.get("candidates", [])}


def describe(service: ReviewService, subject_ref: str, evidence: dict) -> str:
    outcome = service.outcome_for(subject_ref)
    lines = [
        subject_ref,
        f"  derived status : {outcome.status}",
        f"  credited       : {outcome.credited_accepts}/{outcome.threshold}"
        f" ({outcome.remaining} to go)",
    ]
    if outcome.credited_batches:
        lines.append(f"  batches        : {', '.join(outcome.credited_batches)}")
    if outcome.uncredited_accepts:
        lines.append(
            f"  uncredited     : {outcome.uncredited_accepts}"
            " (same batch or same day)"
        )
    if outcome.rejected_by:
        lines.append(f"  rejected by    : {outcome.rejected_by} at {outcome.rejected_at}")

    candidate = evidence.get(subject_ref)
    if candidate:
        lines.append(
            f"  proposal       : {candidate['entity_name']} -> "
            f"{candidate['category_name']} at {candidate['confidence']}"
        )
        for signal in candidate.get("signals", []):
            lines.append(f"    {signal['name']}: {signal['evidence']}")
        if candidate.get("previously_rejected"):
            lines.append(
                f"    previously rejected by {candidate.get('rejected_by')}"
                f" at {candidate.get('rejected_at')}"
            )
    return "\n".join(lines)


def cmd_queue(args: argparse.Namespace) -> int:
    service = open_service(args.db, threshold=args.threshold)
    evidence = load_manifest(args.manifest)
    refs = service.queue(args.limit)
    if not refs:
        print("queue is empty")
        return 0
    for ref in refs:
        print(describe(service, ref, evidence))
        print()
    print(f"{len(refs)} open items")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    service = open_service(args.db, threshold=args.threshold)
    print(describe(service, args.subject_ref, load_manifest(args.manifest)))
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    service = open_service(args.db, threshold=args.threshold)
    decisions = service.ledger(args.subject_ref)
    if not decisions:
        print("no decisions recorded")
        return 0
    for decision in decisions:
        note = f" — {decision.note}" if decision.note else ""
        print(
            f"{decision.at.isoformat()}  {decision.decision:<6}  "
            f"{decision.reviewer:<20} {decision.proposal_batch}{note}"
        )
    return 0


def _report(outcome: ReviewOutcome, *, credited_before: int) -> None:
    print(
        f"{outcome.subject_ref}: {outcome.status} "
        f"({outcome.credited_accepts}/{outcome.threshold} credited, "
        f"{outcome.remaining} to go)"
    )
    if outcome.credited_accepts == credited_before:
        # Recorded, kept, and not counted. Saying so is what stops a curator
        # clicking nine more times and wondering why nothing moves.
        print("  not credited: same proposal batch or same day as the last one")


def _decide(args: argparse.Namespace, decision: ReviewDecisionKind) -> int:
    reviewer = args.reviewer or os.environ.get(REVIEWER_ENV)
    if not reviewer:
        print(
            f"a reviewer is required: pass --reviewer or set {REVIEWER_ENV}",
            file=sys.stderr,
        )
        return 2

    batch = args.batch
    if batch is None and args.manifest is not None:
        batch = json.loads(args.manifest.read_text(encoding="utf-8")).get("batch")
    if not batch:
        # Credit depends on the batch, so guessing one would quietly change
        # what the ledger means.
        print("a proposal batch is required: pass --batch", file=sys.stderr)
        return 2

    service = open_service(args.db, threshold=args.threshold)
    failures = 0
    for subject_ref in args.subject_refs:
        credited_before = service.outcome_for(subject_ref).credited_accepts
        try:
            ids.require(subject_ref, ids.kind_of(subject_ref))
            outcome = service.record(
                subject_ref,
                reviewer=reviewer,
                decision=decision,
                proposal_batch=batch,
                note=args.note,
            )
        except ConflictError:
            print(
                f"{subject_ref}: already decided by {reviewer} in batch {batch}",
                file=sys.stderr,
            )
            failures += 1
        except (ContentError, ValueError) as error:
            print(f"{subject_ref}: {error}", file=sys.stderr)
            failures += 1
        else:
            _report(outcome, credited_before=credited_before)
    return 1 if failures else 0


def cmd_accept(args: argparse.Namespace) -> int:
    return _decide(args, ReviewDecisionKind.ACCEPT)


def cmd_reject(args: argparse.Namespace) -> int:
    return _decide(args, ReviewDecisionKind.REJECT)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--threshold", type=int, default=ACCEPT_THRESHOLD)
    sub = parser.add_subparsers(dest="command", required=True)

    queue = sub.add_parser("queue", help="open items, nearest the threshold first")
    queue.add_argument("--limit", type=int, default=None)
    queue.set_defaults(func=cmd_queue)

    show = sub.add_parser("show", help="one item with its evidence and ledger state")
    show.add_argument("subject_ref")
    show.set_defaults(func=cmd_show)

    history = sub.add_parser("history", help="every decision recorded for one item")
    history.add_argument("subject_ref")
    history.set_defaults(func=cmd_history)

    for name, func, helptext in (
        ("accept", cmd_accept, "record an accept"),
        ("reject", cmd_reject, "record a reject; one is enough"),
    ):
        command = sub.add_parser(name, help=helptext)
        command.add_argument("subject_refs", nargs="+")
        command.add_argument("--reviewer", default=None)
        command.add_argument("--batch", default=None)
        command.add_argument("--note", default=None)
        command.set_defaults(func=func)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
