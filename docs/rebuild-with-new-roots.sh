#!/usr/bin/env bash
# Run from the repo root in the Codespace. Needs network for the embedding model.
# Builds two snapshots from one embedding export so A and B are comparable:
#   A: sixteen current roots + shellfish, cutting_implement, tableware, power_tool
#   B: A + color + drug_of_abuse
# Kept as two builds because color and drug_of_abuse added the most lemma
# collisions in the report, and the effect of each group should be readable alone.
set -euo pipefail

SEEDS=content/seeds
BASE="bird carnivore fish flavorer flower fruit garment herb insect instrument kitchen planet reptile sport tool vehicle"
GROUP_A="shellfish cutting_implement tableware power_tool"
GROUP_B="color drug_of_abuse"

export_root() { # id depth name
  python tools/export_wordnet.py --root "$1" --depth "$2" --out "$SEEDS/wordnet-$3.lexicon.json"
}

# fish at depth 8: the only root that was truncated at depth 6 (101 synsets lost).
export_root oewn-02514684-n 8 fish
export_root oewn-07799186-n 6 shellfish
export_root oewn-03159112-n 6 cutting_implement
export_root oewn-04389081-n 6 tableware
export_root oewn-04003842-n 6 power_tool
export_root oewn-04963771-n 6 color
export_root oewn-03253661-n 6 drug_of_abuse

# Named explicitly, never globbed: a glob picks up the mini test fixture.
flags() { for r in "$@"; do printf -- "--lexicon %s/wordnet-%s.lexicon.json " "$SEEDS" "$r"; done; }

mkdir -p build/inputs-all
# One input list over the superset, so one embedding export serves both builds.
python tools/build_snapshot.py --write-inputs build/inputs-all/ \
  $(flags $BASE $GROUP_A $GROUP_B) --overlay "$SEEDS/overlay.curated.json"

python tools/export_frequency.py --terms build/inputs-all/terms.txt --out build/frequency.all.json
python tools/export_embeddings.py --texts build/inputs-all/texts.txt --out build/embeddings.all.json

build() { # label roots...
  local label=$1; shift
  rm -f "build/graph-$label.sqlite"*
  python tools/build_snapshot.py --db "build/graph-$label.sqlite" --label "2026-10-03-$label" \
    --now 2026-10-03T00:00:00+00:00 \
    --frequency build/frequency.all.json --embeddings build/embeddings.all.json \
    --overlay "$SEEDS/overlay.curated.json" $(flags "$@")
  python tools/measure_coverage.py --db "build/graph-$label.sqlite" --difficulty medium \
    --json "build/coverage-$label.json"
}

build A $BASE $GROUP_A
build B $BASE $GROUP_A $GROUP_B

echo "done: build/coverage-A.json and build/coverage-B.json"
