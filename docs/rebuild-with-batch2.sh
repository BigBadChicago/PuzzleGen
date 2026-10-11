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
# now the "tool" subtree. Hand tool kept its own file under its own name. These
# are names only: what each one means is in the manifest read below.
A="mollusk crustacean bird carnivore fish flavorer flower fruit garment herb insect instrument kitchen planet reptile sport hand_tool vehicle cutting_implement tableware power_tool"
BATCH2="dairy_product tool rodent nut bulbous_plant vine angiospermous_tree marsh_plant shrub"

# Exports a root, unless its lexicon is already committed. The seeds are in
# the repository, and re-exporting needs the WordNet data installed, which a CI
# runner has no reason to carry: this script has to be runnable both by someone
# adding a root and by a job that only rebuilds and measures. Set
# PUZZLEGEN_REEXPORT=1 to export regardless.
# Which subtree each root name means comes from the manifest, not from this
# file. The pairs were duplicated across two scripts and a document, and the
# copy that said "tool" where it meant "hand tool" is the one that cost a week.
# tests/unit/test_lexicon_seeds.py holds every committed export to the same
# manifest, so a disagreement fails on push instead of building a different
# snapshot in silence.
MANIFEST=$SEEDS/lexicon-roots.json
LEXICON=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["lexicon"])' "$MANIFEST")
export_root() { # name
  local out="$SEEDS/wordnet-$1.lexicon.json"
  if [ -s "$out" ] && [ -z "${PUZZLEGEN_REEXPORT:-}" ]; then
    echo "have $out"
    return 0
  fi
  local synset depth
  read -r synset depth < <(python3 -c 'import json,sys
root = json.load(open(sys.argv[1]))["roots"][sys.argv[2]]
print(root["synset"], root["depth"])' "$MANIFEST" "$1")
  python3 tools/export_wordnet.py --lexicon "$LEXICON" \
    --root "$synset" --depth "$depth" --out "$out"
}

# Batch 2. dairy_product is exported and listed before shrub so the merged
# "cheese" entity takes the dairy gloss rather than tall mallow, the shrub.
for root in $BATCH2; do export_root "$root"; done

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
