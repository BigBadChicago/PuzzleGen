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

from puzzlegen.content.snapshots import ActivationPolicy, ImportReport, SnapshotBuilder
from puzzlegen.graph import GraphRepositories, SqliteDocumentStore
from puzzlegen.providers.curated import CuratedJSONProvider
from puzzlegen.providers.embeddings import DevHashEmbeddingProvider, TableEmbeddingProvider
from puzzlegen.providers.frequency import TableFrequencyProvider
from puzzlegen.providers.wordnet import WordNetLexiconProvider

#: Not a flag. See the module docstring.
OVERLAY_SEED = Path("content/seeds/overlay.curated.json")

LEXICAL_TAXONOMY = "wordnet"
OVERLAY_TAXONOMY = "overlay"


def input_lists(lexicons: list[Path], overlay: Path) -> tuple[list[str], list[str]]:
    """The two lists the export commands consume.

    They differ, and the difference matters. Frequency is scored per lemma;
    embeddings are computed over the text a game would actually show, which
    includes the gloss, because two entities named identically are told apart
    by their definition and not by their label. Feeding one list to both
    commands produces an embedding table whose keys never match what
    ``attach_embeddings`` asks for, and the build fails at the last step with a
    ``KeyError`` listing five words.
    """
    terms: dict[str, None] = {}
    definitions: dict[str, str] = {}

    for path in lexicons:
        document = json.loads(path.read_text(encoding="utf-8"))
        glosses = {s["id"]: s.get("definition", "") for s in document.get("synsets", ())}
        for sense in document.get("senses", ()):
            lemma = sense["lemma"]
            terms.setdefault(lemma, None)
            gloss = sense.get("definition") or glosses.get(sense["synset"], "")
            if gloss and lemma not in definitions:
                definitions[lemma] = gloss

    if overlay.exists():
        seed = json.loads(overlay.read_text(encoding="utf-8"))
        for entity in seed.get("entities", ()):
            terms.setdefault(entity["name"], None)

    ordered = sorted(terms)
    texts = [
        f"{term}: {definitions[term]}" if term in definitions else term
        for term in ordered
    ]
    return ordered, texts


def write_inputs(directory: Path, lexicons: list[Path], overlay: Path) -> tuple[Path, Path]:
    terms, texts = input_lists(lexicons, overlay)
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

    if args.write_inputs is not None:
        terms_path, texts_path = write_inputs(
            args.write_inputs, args.lexicon, args.overlay
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
