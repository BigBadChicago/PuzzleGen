#!/usr/bin/env bash
# Reproduces build A2: build A plus the second batch of roots. Run from the repo
# root. Supersedes docs/rebuild-with-new-roots.sh, whose A and B builds this
# repeats; keep that file for the A/B comparison that decided to ship A.
#
# Two ways to get embeddings, and the choice does not affect coverage:
#   --dev-embeddings   no network, hash vectors, carries no semantic signal
#   --embeddings FILE  real vectors from tools/export_embeddings.py (needs the
#                      sentence-transformers model, so needs network)
# Verified 2026-10-10: build A with --dev-embeddings and a freshly exported
# frequency table reproduces build/coverage-A.json byte for byte. Coverage is a
# structural measure and applies no embedding gate, so a dev build measures it
# exactly; a published snapshot still needs real vectors, because the day's
# content queries do apply similarity gates.
set -euo pipefail

SEEDS=content/seeds
# Game one owns the overlay, so it lives in that game's distribution.
OVERLAY=games/grouping/content/overlay.curated.json
# wordnet-tool.lexicon.json was the "hand tool" subtree until 2026-10-10 and is
# now the "tool" subtree (oewn-04459089-n). Hand tool kept its own file under
# its own name. A script that still says tool where it means hand tool builds a
# different snapshot and no error says so.
A="mollusk crustacean bird carnivore fish flavorer flower fruit garment herb insect instrument kitchen planet reptile sport hand_tool vehicle cutting_implement tableware power_tool"
BATCH2="dairy_product tool rodent nut bulbous_plant vine angiospermous_tree marsh_plant shrub"

# Exports a root, unless its lexicon is already committed. The seeds are in
# the repository, and re-exporting needs the WordNet data installed, which a CI
# runner has no reason to carry: this script has to be runnable both by someone
# adding a root and by a job that only rebuilds and measures. Set
# PUZZLEGEN_REEXPORT=1 to export regardless.
export_root() { # synset-id depth name
  local out="$SEEDS/wordnet-$3.lexicon.json"
  if [ -s "$out" ] && [ -z "${PUZZLEGEN_REEXPORT:-}" ]; then
    echo "have $out"
    return 0
  fi
  python3 tools/export_wordnet.py --root "$1" --depth "$2" --out "$out"
}

# Batch 2. dairy_product is exported and listed before shrub so the merged
# "cheese" entity takes the dairy gloss rather than tall mallow, the shrub.
export_root oewn-07859751-n 6 dairy_product
export_root oewn-04459089-n 6 tool
export_root oewn-02332053-n 6 rodent
export_root oewn-13157333-n 6 nut
export_root oewn-13155061-n 6 bulbous_plant
export_root oewn-13121436-n 6 vine
export_root oewn-13130492-n 6 angiospermous_tree
export_root oewn-13143123-n 6 marsh_plant
export_root oewn-13133423-n 6 shrub

# Named explicitly, never globbed: a glob picks up the mini test fixture.
flags() { for r in "$@"; do printf -- "--lexicon %s/wordnet-%s.lexicon.json " "$SEEDS" "$r"; done; }

mkdir -p build/inputs-A2
python3 tools/build_snapshot.py --write-inputs build/inputs-A2/ $(flags $A $BATCH2) --overlay "$OVERLAY"
python3 tools/export_frequency.py --terms build/inputs-A2/terms.txt --out build/frequency.A2.json

# What merges, before building: read overlay_missing and the refused parents.
python3 tools/report_lemma_collisions.py $(flags $A $BATCH2) --overlay "$OVERLAY" --out build/lemma-collisions-A2.json

# PUZZLEGEN_REAL_EMBEDDINGS=1 builds the snapshot days are generated from;
# without it the build is the coverage build, which needs no model.
EMBEDDINGS=--dev-embeddings
if [ -n "${PUZZLEGEN_REAL_EMBEDDINGS:-}" ]; then
  python3 tools/export_embeddings.py --texts build/inputs-A2/texts.txt --out build/embeddings.A2.json
  EMBEDDINGS="--embeddings build/embeddings.A2.json"
fi

rm -f build/graph-A2.sqlite*
python3 tools/build_snapshot.py --db build/graph-A2.sqlite --label 2026-10-10-A2 --now 2026-10-10T00:00:00+00:00 --frequency build/frequency.A2.json $EMBEDDINGS --overlay "$OVERLAY" $(flags $A $BATCH2)
python3 games/grouping/tools/measure_coverage.py --db build/graph-A2.sqlite --difficulty medium --json build/coverage-A2.json

echo "done: build/coverage-A2.json"
