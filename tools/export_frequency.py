#!/usr/bin/env python3
"""Export Zipf frequency scores for a term list.

Reads the terms the snapshot actually contains rather than a whole corpus, so
the exported table stays small and reviewable. The engine never imports
``wordfreq``; if that project is abandoned, only this script and the table
format change.

    python tools/export_frequency.py --terms content/seeds/terms.txt \
        --out content/seeds/frequency.en.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terms", required=True, type=Path)
    parser.add_argument("--lang", default="en")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    try:
        from wordfreq import __version__ as wordfreq_version
        from wordfreq import zipf_frequency
    except ImportError:
        print(
            "the wordfreq package is required: pip install 'puzzlegen[snapshot]'",
            file=sys.stderr,
        )
        return 2

    terms = [
        line.strip()
        for line in args.terms.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    scores = {term: zipf_frequency(term, args.lang) for term in sorted(set(terms))}
    scores = {term: value for term, value in scores.items() if value > 0}

    document = {
        "name": "wordfreq",
        "version": wordfreq_version,
        "lang": args.lang,
        "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "scores": scores,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {len(scores)} scores to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
