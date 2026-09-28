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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--texts", required=True, type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out", required=True, type=Path)
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

    document = {
        "model_name": args.model,
        # The commit or release identifying these weights. Recorded because
        # the same model name can serve different weights over time, and a
        # manifest that cannot name its weights cannot claim reproducibility.
        "model_version": getattr(model, "model_card_version", None) or "unversioned",
        "similarity_metric": "cosine",
        "computed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "vectors": {
            text: [round(float(x), 6) for x in vector]
            for text, vector in zip(texts, vectors)
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {len(texts)} vectors to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
