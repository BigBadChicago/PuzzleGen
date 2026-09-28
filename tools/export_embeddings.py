#!/usr/bin/env python3
"""Export real sentence embeddings for the texts a snapshot will embed.

Runs once at snapshot build time so the engine never loads a model. Freezing
vectors into the snapshot is what makes a model upgrade a deliberate snapshot
rebuild rather than a silent change to puzzles that were already published.

    python tools/export_embeddings.py --texts content/seeds/texts.txt \
        --out content/seeds/embeddings.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def render(header: dict, rows: dict[str, list[float]]) -> str:
    """One line per vector, sorted, with no indentation inside a row.

    Ordinary ``json.dumps(..., indent=2)`` puts every one of 384 components on
    its own line, which costs about 6.4 kB per vector: a 15,000 entity snapshot
    writes a 94 MB file, near GitHub's per-file limit, and produces a diff
    nobody can read. This writes the same JSON document, still sorted so two
    machines produce identical bytes, at roughly 2.9 kB per vector, and a
    changed vector shows as one changed line rather than 384.
    """
    lines = ['{']
    for key in sorted(header):
        lines.append(f"  {json.dumps(key)}: {json.dumps(header[key])},")
    lines.append('  "vectors": {')
    keys = sorted(rows)
    for index, text in enumerate(keys):
        comma = "" if index == len(keys) - 1 else ","
        row = json.dumps(rows[text], separators=(",", ":"))
        lines.append(f"    {json.dumps(text)}: {row}{comma}")
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--texts", required=True, type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--decimals",
        type=int,
        default=4,
        help=(
            "components kept per vector. Four is below the noise floor of a "
            "cosine comparison between normalised vectors and a third of the "
            "bytes of six."
        ),
    )
    args = parser.parse_args(argv)

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print(
            "sentence-transformers is required: pip install 'puzzlegen[snapshot]'",
            file=sys.stderr,
        )
        return 2

    texts = [
        line.strip()
        for line in args.texts.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    model = SentenceTransformer(args.model)
    vectors = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)

    header = {
        "model_name": args.model,
        # The commit or release identifying these weights. Recorded because
        # the same model name can serve different weights over time, and a
        # manifest that cannot name its weights cannot claim reproducibility.
        "model_version": getattr(model, "model_card_version", None) or "unversioned",
        "similarity_metric": "cosine",
        "computed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    rows = {
        text: [round(float(x), args.decimals) for x in vector]
        for text, vector in zip(texts, vectors)
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(header, rows), encoding="utf-8")
    size = args.out.stat().st_size
    print(f"wrote {len(texts)} vectors to {args.out} ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
