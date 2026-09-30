#!/usr/bin/env python3
"""Propose overlay memberships, as candidates a curator must accept ten times.

"Salmon is also a colour" is an interpretation, not something a source said,
so every relationship this writes is JUDGED and lands at ``PENDING_REVIEW``.
Nothing here can reach a puzzle on its own, which is exactly why it is allowed
to suggest freely: the cost of a bad proposal is one rejection, not a wrong
answer in a published board.

It judges on two signals and records which fired. String matching alone misses
anything needing world knowledge; embeddings alone produce proposals a curator
cannot see the reason for. The manifest carries both, so a long queue can be
triaged by method rather than read row by row.

    python tools/propose_overlay.py --db content/graph.sqlite \\
        --batch overlay-2026-09-28 \\
        --manifest content/proposals/overlay-2026-09-28.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from overlay_coverage import Snapshot, gain_of
from overlay_coverage import load as load_coverage

from puzzlegen.content.review import ReviewRepository, derive_status
from puzzlegen.games.grouping.content import VISIBLE_GROUPING
from puzzlegen.core import ids
from puzzlegen.core.types import (
    FreshnessClass,
    ProvenanceClass,
    ReviewStatus,
    SourceKind,
)
from puzzlegen.graph import (
    Category,
    Entity,
    GraphRepositories,
    Provenance,
    Relationship,
    Source,
    SqliteDocumentStore,
)

#: Version of the proposing procedure itself. Bumped when the signals or the
#: scoring change, so a candidate can be traced to the rules that produced it
#: rather than to whichever checkout happened to run.
PROPOSER_VERSION = "1.0.0"

#: The reviewer name written into JUDGED provenance at proposal time.
#: ``Provenance`` refuses to construct JUDGED without one, so something has to
#: go here; naming the tool makes it obvious that nobody has looked yet. The
#: review service replaces it with a real name when the threshold is crossed.
PLACEHOLDER_REVIEWER = "tools/propose_overlay.py"

OVERLAY_TAXONOMY = "overlay"
MEMBERSHIP_PREDICATE = "is_a"

#: Below this many member vectors a centroid is noise rather than a direction.
DEFAULT_MIN_MEMBERS = 3
DEFAULT_MIN_SIMILARITY = 0.55
DEFAULT_MIN_CONFIDENCE = 0.6

#: Candidates the exact feasibility check runs against, best-first by the
#: cheap score. The check is a search per candidate; running it on every row
#: of a several-thousand-row queue would cost more than the queue is worth,
#: and the rows it would change the order of are all near the top anyway.
DEFAULT_COVERAGE_CHECK_LIMIT = 200

RANK_BY_CONFIDENCE = "confidence"
RANK_BY_COVERAGE = "coverage"
RANK_CHOICES = (RANK_BY_CONFIDENCE, RANK_BY_COVERAGE)

_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class Signal:
    name: str
    evidence: str
    strength: float


@dataclass(slots=True)
class Candidate:
    subject_ref: str
    entity_id: str
    entity_name: str
    category_id: str
    category_name: str
    confidence: float
    signals: list[Signal] = field(default_factory=list)
    #: Usable lexical categories this overlay category would newly reach if
    #: this candidate were accepted. Confidence says the word plausibly
    #: belongs; this says whether accepting it moves the thing that is
    #: actually blocking a board.
    new_homes: list[str] = field(default_factory=list)
    #: Group sizes whose feasibility flips. Only populated for candidates the
    #: exact check ran against, which is why ``coverage_checked`` is separate:
    #: an unchecked empty list and a checked empty list mean opposite things.
    unblocks: list[int] = field(default_factory=list)
    coverage_checked: bool = False
    previously_rejected: bool = False
    rejected_by: str | None = None
    rejected_at: str | None = None

    def signal_names(self) -> list[str]:
        return [s.name for s in self.signals]

    def as_json(self) -> dict:
        document = asdict(self)
        document["signals"] = [asdict(s) for s in self.signals]
        return document


def tokens(text: str | None) -> set[str]:
    return set(_WORD.findall(text.lower())) if text else set()


def _singular(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def lexical_signal(entity: Entity, category: Category) -> Signal | None:
    """Fires when the category's name appears in what the entity already says.

    Deliberately narrow. A looser match (any shared token with the gloss) turns
    the queue into noise, and a curator who stops reading the queue is worse
    than a signal that fires rarely.
    """
    wanted = {_singular(t) for t in tokens(category.canonical_name)}
    if not wanted:
        return None

    haystacks = {
        "definition": entity.definition or "",
        "aliases": " ".join(entity.aliases),
    }
    for where, text in haystacks.items():
        present = {_singular(t) for t in tokens(text)}
        if wanted <= present:
            return Signal(
                name="lexical",
                evidence=f"{category.canonical_name!r} appears in {where}: {text[:120]}",
                strength=1.0,
            )
    return None


def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise ValueError("vectors must share dimensions")
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def centroid(vectors: list[tuple[float, ...]]) -> tuple[float, ...]:
    if not vectors:
        raise ValueError("a centroid needs at least one vector")
    width = len(vectors[0])
    if any(len(v) != width for v in vectors):
        raise ValueError("vectors must share dimensions")
    return tuple(sum(v[i] for v in vectors) / len(vectors) for i in range(width))


def embedding_signal(
    vector: tuple[float, ...],
    members: tuple[float, ...],
    member_count: int,
    min_similarity: float,
) -> Signal | None:
    similarity = cosine(vector, members)
    if similarity < min_similarity:
        return None
    return Signal(
        name="embedding",
        evidence=(
            f"cosine {similarity:.3f} against the centroid of "
            f"{member_count} existing members"
        ),
        strength=similarity,
    )


def score(signals: list[Signal], min_similarity: float) -> float:
    """Confidence from the signals that fired.

    A floor of 0.5 for any single signal, a lexical hit worth a fixed step, and
    an embedding hit worth its distance above the threshold. Both firing is
    worth more than either, because the two are wrong in different ways.
    """
    if not signals:
        return 0.0
    by_name = {s.name: s for s in signals}
    value = 0.5
    if "lexical" in by_name:
        value += 0.2
    embedding = by_name.get("embedding")
    if embedding is not None:
        headroom = max(0.0, 1.0 - min_similarity) or 1.0
        value += 0.25 * min(1.0, (embedding.strength - min_similarity) / headroom)
    return round(min(0.95, value), 3)


def _model_vectors(
    repos: GraphRepositories, entity_ids: list[str]
) -> tuple[dict[str, tuple[float, ...]], str, str]:
    """Vectors for one model only, the one most of these entities share.

    Mixing two models' vectors in one centroid compares coordinates that mean
    different things, which produces a number rather than a similarity.
    """
    by_model: dict[tuple[str, str], dict[str, tuple[float, ...]]] = {}
    for entity_id in entity_ids:
        for record in repos.embeddings.for_entity(entity_id):
            key = (record.model_name, record.model_version)
            by_model.setdefault(key, {})[entity_id] = record.vector
    if not by_model:
        return {}, "", ""
    (name, version), vectors = max(by_model.items(), key=lambda kv: len(kv[1]))
    return vectors, name, version


def existing_members(repos: GraphRepositories, category_id: str) -> list[str]:
    return sorted(
        {
            rel.subject_id
            for rel in repos.relationships.by_object(
                category_id, MEMBERSHIP_PREDICATE
            )
            if rel.status is ReviewStatus.ACTIVE
        }
    )


def already_proposed(repos: GraphRepositories, category_id: str) -> set[str]:
    """Every subject already connected to this category, at any status.

    Any status, not just active: re-proposing something that is sitting in the
    queue would add a second identical row to the queue.
    """
    return {
        rel.subject_id
        for rel in repos.relationships.by_object(category_id, MEMBERSHIP_PREDICATE)
    }


def rank_candidates(
    candidates: list[Candidate],
    coverage: Snapshot | None,
    *,
    rank_by: str = RANK_BY_CONFIDENCE,
    check_limit: int = DEFAULT_COVERAGE_CHECK_LIMIT,
) -> list[Candidate]:
    """Annotate every candidate with what it would buy, then order the queue.

    Ordering is the whole intervention. Nothing here decides anything: the
    records are still JUDGED, still PENDING_REVIEW, still ten credited accepts
    on ten separate days, still a curator accepting one at a time. What
    changes is which rows that curator reads first, because a confidence sort
    ranks by "does this word plausibly belong" and the thing blocking a board
    is "does this word sit where the board needs one". The seed overlay was
    authored on plausibility alone and that is precisely why its 144 words
    scatter across near-leaf categories.
    """
    if coverage is None:
        return sorted(candidates, key=lambda c: (-c.confidence, c.subject_ref))

    for candidate in candidates:
        gain = gain_of(coverage, candidate.entity_id, candidate.category_id)
        candidate.new_homes = list(gain.new_homes)

    cheap_first = sorted(
        candidates,
        key=lambda c: (-len(c.new_homes), -c.confidence, c.subject_ref),
    )
    for candidate in cheap_first[: max(0, check_limit)]:
        exact = gain_of(
            coverage, candidate.entity_id, candidate.category_id, exact=True
        )
        candidate.unblocks = list(exact.unblocks)
        candidate.coverage_checked = True

    if rank_by == RANK_BY_COVERAGE:
        return sorted(
            candidates,
            key=lambda c: (
                -len(c.unblocks),
                -len(c.new_homes),
                -c.confidence,
                c.subject_ref,
            ),
        )
    return sorted(candidates, key=lambda c: (-c.confidence, c.subject_ref))


def propose(
    repos: GraphRepositories,
    reviews: ReviewRepository,
    *,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    min_members: int = DEFAULT_MIN_MEMBERS,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    pool_limit: int | None = None,
    include_rejected: bool = False,
    coverage: Snapshot | None = None,
    rank_by: str = RANK_BY_CONFIDENCE,
    coverage_check_limit: int = DEFAULT_COVERAGE_CHECK_LIMIT,
) -> list[Candidate]:
    """Score every active entity against every live overlay category."""
    categories = [
        c
        for c in repos.categories.live(OVERLAY_TAXONOMY)
        if c.status is ReviewStatus.ACTIVE
    ]
    pool = repos.entities.active(limit=pool_limit)
    candidates: list[Candidate] = []

    for category in sorted(categories, key=lambda c: c.id):
        taken = already_proposed(repos, category.id)
        members = existing_members(repos, category.id)
        member_vectors, _, _ = _model_vectors(repos, members)
        pool_vectors, _, _ = _model_vectors(repos, [e.id for e in pool])
        target = (
            centroid(list(member_vectors.values()))
            if len(member_vectors) >= min_members
            else None
        )

        for entity in sorted(pool, key=lambda e: e.id):
            if entity.id in taken:
                continue
            signals: list[Signal] = []
            lexical = lexical_signal(entity, category)
            if lexical is not None:
                signals.append(lexical)
            vector = pool_vectors.get(entity.id)
            if target is not None and vector is not None and len(vector) == len(target):
                fired = embedding_signal(
                    vector, target, len(member_vectors), min_similarity
                )
                if fired is not None:
                    signals.append(fired)
            if not signals:
                continue

            confidence = score(signals, min_similarity)
            if confidence < min_confidence:
                continue

            subject_ref = ids.for_relationship(
                entity.id, MEMBERSHIP_PREDICATE, category.id
            )
            outcome = derive_status(reviews.for_subject(subject_ref))
            rejected = outcome.status is ReviewStatus.REJECTED
            if rejected and not include_rejected:
                # Showing a curator the same rejected suggestion every week is
                # how a queue stops being read. The ledger remembers, so the
                # proposer can.
                continue

            candidates.append(
                Candidate(
                    subject_ref=subject_ref,
                    entity_id=entity.id,
                    entity_name=entity.canonical_name,
                    category_id=category.id,
                    category_name=category.canonical_name,
                    confidence=confidence,
                    signals=signals,
                    previously_rejected=rejected,
                    rejected_by=outcome.rejected_by,
                    rejected_at=(
                        outcome.rejected_at.isoformat() if outcome.rejected_at else None
                    ),
                )
            )

    return rank_candidates(
        candidates,
        coverage,
        rank_by=rank_by,
        check_limit=coverage_check_limit,
    )


def proposer_source(now: dt.datetime) -> Source:
    return Source(
        id=ids.for_source("overlay-proposer", PROPOSER_VERSION),
        name="overlay-proposer",
        kind=SourceKind.COMPUTED,
        version=PROPOSER_VERSION,
        retrieved_at=now,
    )


def write_candidates(
    repos: GraphRepositories,
    candidates: list[Candidate],
    *,
    batch: str,
    now: dt.datetime,
) -> int:
    """Write candidates as PENDING_REVIEW relationships. Never anything else."""
    source = proposer_source(now)
    written = 0
    with repos.transaction():
        repos.sources.put(source)
        for candidate in candidates:
            provenance = Provenance(
                source_id=source.id,
                provenance_class=ProvenanceClass.JUDGED,
                source_ref=f"{batch}/{candidate.entity_name}",
                retrieval_date=now,
                confidence=candidate.confidence,
                reviewer=PLACEHOLDER_REVIEWER,
                verification_method=(
                    "overlay proposer signals: "
                    + ",".join(candidate.signal_names())
                ),
            )
            repos.relationships.put(
                Relationship.build(
                    subject_id=candidate.entity_id,
                    predicate=MEMBERSHIP_PREDICATE,
                    object_id=candidate.category_id,
                    created_at=now,
                    status=ReviewStatus.PENDING_REVIEW,
                    freshness_class=FreshnessClass.STATIC,
                    confidence=candidate.confidence,
                    provenance=(provenance,),
                )
            )
            written += 1
    return written


def manifest_document(
    candidates: list[Candidate],
    *,
    batch: str,
    now: dt.datetime,
    parameters: dict,
) -> dict:
    by_signal: dict[str, int] = {}
    for candidate in candidates:
        for name in candidate.signal_names():
            by_signal[name] = by_signal.get(name, 0) + 1
    return {
        "batch": batch,
        "proposer_version": PROPOSER_VERSION,
        "generated_at": now.isoformat(),
        "parameters": parameters,
        "counts": {
            "candidates": len(candidates),
            "by_signal": by_signal,
            "both_signals": sum(1 for c in candidates if len(c.signals) > 1),
            "previously_rejected": sum(1 for c in candidates if c.previously_rejected),
            "coverage_checked": sum(1 for c in candidates if c.coverage_checked),
            "would_unblock_a_board": sum(1 for c in candidates if c.unblocks),
            "adds_a_new_home": sum(1 for c in candidates if c.new_homes),
        },
        "candidates": [c.as_json() for c in candidates],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--min-similarity", type=float, default=DEFAULT_MIN_SIMILARITY)
    parser.add_argument("--min-members", type=int, default=DEFAULT_MIN_MEMBERS)
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE)
    parser.add_argument("--pool-limit", type=int, default=None)
    parser.add_argument("--include-rejected", action="store_true")
    parser.add_argument(
        "--rank-by",
        choices=RANK_CHOICES,
        default=RANK_BY_CONFIDENCE,
        help=(
            "queue order. 'coverage' puts candidates that would unblock a "
            "board first; 'confidence' is the plausibility order."
        ),
    )
    parser.add_argument(
        "--coverage-check-limit",
        type=int,
        default=DEFAULT_COVERAGE_CHECK_LIMIT,
        help="candidates the exact feasibility check runs against",
    )
    parser.add_argument(
        "--no-coverage",
        action="store_true",
        help="skip coverage annotation entirely",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="write the manifest but no graph records",
    )
    args = parser.parse_args(argv)

    now = dt.datetime.now(dt.timezone.utc)
    repos = GraphRepositories(SqliteDocumentStore(args.db))
    try:
        reviews = repos.attach(ReviewRepository)

        parameters = {
            "min_similarity": args.min_similarity,
            "min_members": args.min_members,
            "min_confidence": args.min_confidence,
            "pool_limit": args.pool_limit,
            "include_rejected": args.include_rejected,
            "rank_by": args.rank_by,
            "coverage_check_limit": args.coverage_check_limit,
            "coverage": not args.no_coverage,
        }
        # The rule game 1 actually groups by, so the annotation predicts the
        # boards that get built rather than boards under a retired rule.
        coverage = (
            None
            if args.no_coverage
            else load_coverage(repos, grouping=str(VISIBLE_GROUPING))
        )
        candidates = propose(
            repos,
            reviews,
            min_similarity=args.min_similarity,
            min_members=args.min_members,
            min_confidence=args.min_confidence,
            pool_limit=args.pool_limit,
            include_rejected=args.include_rejected,
            coverage=coverage,
            rank_by=args.rank_by,
            coverage_check_limit=args.coverage_check_limit,
        )

        written = 0
        if not args.dry_run:
            written = write_candidates(repos, candidates, batch=args.batch, now=now)
    finally:
        # The sqlite connection outlives the command otherwise, and Python 3.13
        # and later report it as a ResourceWarning at collection time.
        repos.close()

    if args.manifest is not None:
        document = manifest_document(
            candidates, batch=args.batch, now=now, parameters=parameters
        )
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(
            json.dumps(document, indent=2, sort_keys=True), encoding="utf-8"
        )

    verb = "would write" if args.dry_run else "wrote"
    print(f"{len(candidates)} candidates, {verb} {written} pending relationships")
    if not args.no_coverage:
        unblocking = sum(1 for c in candidates if c.unblocks)
        homes = sum(1 for c in candidates if c.new_homes)
        print(
            f"{unblocking} would unblock a board, {homes} reach a lexical "
            f"category the hidden group does not, ranked by {args.rank_by}"
        )
    if args.manifest is not None:
        print(f"manifest: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
