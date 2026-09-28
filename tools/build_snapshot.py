#!/usr/bin/env python3
"""Build a snapshot: import, enrich, activate, seal.

``SnapshotBuilder`` has existed since phase 2 and nothing outside tests has
ever driven it. This is the driver.

The overlay seed is a hard-coded step rather than another provider named on the
command line. The overlay is not optional content: without it there is no
second axis, and a grouping game with no second axis is four piles. Making it a
flag would let a build silently produce a snapshot that no game can use.

Two modes:

    # write the input lists the export commands need
    python tools/build_snapshot.py --write-inputs build/ \\
        --lexicon content/seeds/wordnet-animal.lexicon.json

    # build
    python tools/build_snapshot.py --db content/graph.sqlite \\
        --label 2026.09.1 \\
        --lexicon content/seeds/wordnet-animal.lexicon.json \\
        --frequency content/seeds/frequency.json \\
        --embeddings content/seeds/embeddings.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from puzzlegen.content.snapshots import (
    ActivationPolicy,
    ImportReport,
    SnapshotBuilder,
    embedding_text,
)
from puzzlegen.graph import GraphRepositories, InMemoryDocumentStore, SqliteDocumentStore
from puzzlegen.providers.curated import CuratedJSONProvider
from puzzlegen.providers.embeddings import DevHashEmbeddingProvider, TableEmbeddingProvider
from puzzlegen.providers.frequency import TableFrequencyProvider
from puzzlegen.providers.wordnet import WordNetLexiconProvider

#: Not a flag. See the module docstring.
OVERLAY_SEED = Path("content/seeds/overlay.curated.json")

LEXICAL_TAXONOMY = "wordnet"
OVERLAY_TAXONOMY = "overlay"


def input_lists(
    lexicons: list[Path], overlay: Path, now: dt.datetime
) -> tuple[list[str], list[str]]:
    """The two lists the export commands consume.

    Built by importing into a throwaway in-memory graph and reading back the
    entities, rather than by re-reading the export files. The merge decides
    which sense's gloss an entity ends up with, and only the merge knows: a
    lemma exported under two roots keeps one definition, and guessing it from
    the file order picks the wrong one about one time in a hundred. Those are
    exactly the texts ``attach_embeddings`` later asks the table for, and a
    miss there aborts the build at its last step.

    The two lists differ, and the difference matters. Frequency is scored per
    lemma; embeddings are computed over the text a game would show, which
    includes the gloss, because two entities named identically are told apart
    by their definition and not by their label.
    """
    repos = GraphRepositories(InMemoryDocumentStore())
    try:
        builder = SnapshotBuilder(repos, now=now)
        for provider, options in providers(lexicons, overlay, now):
            builder.import_provider(provider, **options)
        entities = sorted(repos.entities.iter_all(), key=lambda e: e.canonical_name)
        terms = [e.canonical_name for e in entities]
        texts = [embedding_text(e) for e in entities]
    finally:
        repos.close()
    return terms, texts


def write_inputs(
    directory: Path, lexicons: list[Path], overlay: Path, now: dt.datetime
) -> tuple[Path, Path]:
    terms, texts = input_lists(lexicons, overlay, now)
    directory.mkdir(parents=True, exist_ok=True)
    terms_path = directory / "terms.txt"
    texts_path = directory / "texts.txt"
    terms_path.write_text("\n".join(terms) + "\n", encoding="utf-8")
    texts_path.write_text("\n".join(texts) + "\n", encoding="utf-8")
    return terms_path, texts_path


def providers(lexicons: list[Path], overlay: Path, now: dt.datetime) -> list[tuple]:
    """Lexicon imports first, then the overlay.

    Order is not cosmetic. The overlay asserts membership for lemmas the
    lexicon also supplies, and the merge keeps the earlier record's definition;
    importing the overlay first would leave every shared entity glossless until
    something overwrote it.
    """
    chosen: list[tuple] = [
        (
            WordNetLexiconProvider(path, taxonomy=LEXICAL_TAXONOMY, now=now),
            {"taxonomy": LEXICAL_TAXONOMY, "entity_identity": "lemma"},
        )
        for path in lexicons
    ]
    chosen.append(
        (
            CuratedJSONProvider(overlay, name="overlay-seed", now=now),
            {"taxonomy": OVERLAY_TAXONOMY, "entity_identity": "lemma"},
        )
    )
    return chosen


def render(report: ImportReport, meta) -> str:
    lines = [
        f"snapshot  : {meta.label}  ({meta.id})",
        f"hash      : {meta.content_hash}",
        f"sealed    : {meta.sealed}",
        "",
        "records:",
    ]
    for collection, count in sorted(meta.record_counts.items()):
        lines.append(f"  {collection:<16} {count}")
    lines.append("")
    lines.append("per provider:")
    for name, counts in sorted(report.provider_counts.items()):
        rendered = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        lines.append(f"  {name:<16} {rendered}")
    lines.append("")
    lines.append(
        f"frequency : scored {report.frequency_scored}, "
        f"missing {report.frequency_missing}"
    )
    lines.append(f"embeddings: {report.embeddings_written}")
    lines.append(f"activated : {report.total_activated()}")
    if report.warnings:
        lines.append("")
        lines.append("warnings:")
        for warning in report.warnings:
            lines.append(f"  {warning}")
    return "\n".join(lines)


def stale_inputs(
    derived: list[Path], sources: list[Path]
) -> list[tuple[Path, Path]]:
    """Derived files older than something they were derived from.

    ``frequency.json`` and ``embeddings.json`` are computed from a term list
    that is itself computed from the lexicons. Re-export a lexicon and forget
    the other two, and the build imports everything, runs for minutes, and dies
    at its last step with a ``KeyError`` naming five plant names, which says
    nothing about which command to re-run. Modification times are crude but
    they catch exactly this, which is the mistake people actually make.
    """
    stale: list[tuple[Path, Path]] = []
    for path in derived:
        if not path.exists():
            continue
        for source in sources:
            if source.exists() and source.stat().st_mtime > path.stat().st_mtime:
                stale.append((path, source))
                break
    return stale


def existing_content(repos) -> dict[str, int]:
    """What a build target already holds.

    A build imports into whatever store it is pointed at, and the merge
    resolves each shared lemma against the record already there. So building
    new lexicons into the database from a previous build does not rebuild it:
    it merges the new content into the old, and for a lemma with several senses
    the old database's definition can win over the one the export files would
    have produced. Every text the embedding table was keyed on then misses, and
    the build dies at its last step naming five plant names.

    Rebuilding into a fresh file is almost always what was meant, so the
    default is to refuse.
    """
    counts: dict[str, int] = {}
    for name, repo in (
        ("snapshots", repos.snapshots),
        ("entities", repos.entities),
        ("categories", repos.categories),
        ("relationships", repos.relationships),
    ):
        found = repo.count()
        if found:
            counts[name] = found
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lexicon", action="append", required=True, type=Path)
    parser.add_argument("--overlay", type=Path, default=OVERLAY_SEED)
    parser.add_argument("--write-inputs", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--label", default=None)
    parser.add_argument("--frequency", type=Path, default=None)
    parser.add_argument("--embeddings", type=Path, default=None)
    parser.add_argument(
        "--dev-embeddings",
        action="store_true",
        help="use deterministic pseudo-vectors; the snapshot is not publishable",
    )
    parser.add_argument("--minimum-confidence", type=float, default=0.75)
    parser.add_argument(
        "--reuse-db",
        action="store_true",
        help="import into a database that already holds content, merging into it",
    )
    parser.add_argument(
        "--ignore-stale",
        action="store_true",
        help="build even though a frequency or embedding file predates a lexicon",
    )
    parser.add_argument(
        "--now",
        default=None,
        help=(
            "ISO timestamp stamped on every record built in this run. "
            "Every governed record carries a creation time and the content "
            "hash covers it, so two builds of identical inputs agree only if "
            "this is pinned. Reproducible builds must pass it."
        ),
    )
    args = parser.parse_args(argv)

    missing = [p for p in (*args.lexicon, args.overlay) if not p.exists()]
    if missing:
        print(f"input not found: {missing[0]}", file=sys.stderr)
        return 2

    derived = [p for p in (args.frequency, args.embeddings) if p is not None]
    stale = stale_inputs(derived, [*args.lexicon, args.overlay])
    if stale and not args.ignore_stale:
        for path, source in stale:
            print(f"{path} is older than {source}", file=sys.stderr)
        print(
            "re-run --write-inputs, then export_frequency.py and "
            "export_embeddings.py, before building. --ignore-stale overrides.",
            file=sys.stderr,
        )
        return 2

    if args.write_inputs is not None:
        terms_path, texts_path = write_inputs(
            args.write_inputs,
            args.lexicon,
            args.overlay,
            dt.datetime.fromisoformat(args.now) if args.now else dt.datetime.now(dt.timezone.utc),
        )
        print(f"wrote {terms_path}\nwrote {texts_path}")
        return 0

    if args.db is None or args.label is None:
        print("--db and --label are required to build", file=sys.stderr)
        return 2

    now = dt.datetime.fromisoformat(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        print("--now must be timezone-aware", file=sys.stderr)
        return 2
    frequency = (
        TableFrequencyProvider.from_file(args.frequency) if args.frequency else None
    )
    if args.embeddings:
        embeddings = TableEmbeddingProvider.from_file(args.embeddings)
    elif args.dev_embeddings:
        # Stamped with the build's time, not the wall clock, or a pinned
        # build still writes a different computed_at into every vector.
        embeddings = DevHashEmbeddingProvider(now=now)
    else:
        embeddings = None

    repos = GraphRepositories(SqliteDocumentStore(args.db))
    try:
        held = existing_content(repos)
        if held and not args.reuse_db:
            summary = ", ".join(f"{k}={v}" for k, v in sorted(held.items()))
            print(f"{args.db} already holds content: {summary}", file=sys.stderr)
            print(
                "a build merges into what is there, so an older sense can win "
                "over the one the export files produce. Delete the file to "
                "rebuild, or pass --reuse-db to merge deliberately.",
                file=sys.stderr,
            )
            return 2

        meta, report = SnapshotBuilder(repos, now=now).build(
            args.label,
            providers(args.lexicon, args.overlay, now),
            frequency=frequency,
            embeddings=embeddings,
            policy=ActivationPolicy(minimum_confidence=args.minimum_confidence),
        )
    finally:
        repos.close()
    print(render(report, meta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
