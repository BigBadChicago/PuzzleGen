#!/usr/bin/env bash
# Builds the symbolic snapshot: digits, numbers and simple figures, and no
# words at all. Run from the repository root.
#
# Nothing here needs a network, a model or a lexicon, which is the point of it
# being its own script rather than a flag on docs/rebuild-with-batch2.sh. That
# build imports WordNet exports, scores them against a frequency table and
# attaches embedding vectors, and it is slow and heavy for good reasons that
# none of this content has: a figure has no corpus frequency, and the vector of
# a triangle would be a vector of its English name.
#
# Every record is a function of the three declarations below, so the snapshot's
# content hash is too. Two runs of this script on any machine produce the same
# snapshot, which is what makes a committed day run auditable: regenerate the
# snapshot, regenerate the days, compare.
#
# Verified 2026-10-12: rebuilt from scratch, 181 entities and 1,758 facts, and
# games/figures/tools/generate_days.py reproduces build/shape-trio-days-30.json
# byte for byte.
set -euo pipefail

DB=build/graph-symbolic.sqlite
LABEL=symbolic.1
# Pinned, and it has to be. Every governed record carries a creation time and
# the content hash covers it, so two builds agree only if this agrees.
NOW=2026-10-10T00:00:00+00:00

# The three declarations. Each is kind:declaration:taxonomy.
#
# Digits and numbers overlap on purpose: nought to nine are the same ten
# entities in both, with a membership in each taxonomy. That is the shared
# graph doing on numbers exactly what it does on words, and it is why the
# entity count is 181 rather than 191.
DIGITS=numbers:0-9:digits
NUMBERS=numbers:0-99:numbers
FIGURES=figures:default:figures

mkdir -p build
rm -f "$DB"

python3 tools/build_snapshot.py \
  --db "$DB" --label "$LABEL" --now "$NOW" \
  --generated "$DIGITS" --generated "$NUMBERS" --generated "$FIGURES"

echo
echo "snapshot: $DB"
echo "Day runs against it:"
echo "  python3 games/figures/tools/generate_days.py --db $DB --start 2026-10-01 --days 30 --now $NOW"
