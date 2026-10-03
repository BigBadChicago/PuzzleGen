#!/usr/bin/env bash
# Run from the repo root in the Codespace. Needs network for the embedding model.
#
# Builds two snapshots so the roots can be judged in two groups:
#   A: current roots plus mollusk, crustacean, cutting_implement, tableware, power_tool
#   B: A plus color and drug_of_abuse
# color and drug_of_abuse added the most lemma collisions, and color may let a
# visible colour group give away the hidden one, so their effect is read alone.
set -euo pipefail

SEEDS=content/seeds
BASE="bird carnivore fish flavorer flower fruit garment herb insect instrument kitchen planet reptile sport tool vehicle"
# Listed first so a merged category such as crab takes the crustacean gloss
# instead of the rowing stroke from sport. Parents merge either way, so order
# only decides the displayed definition.
FIRST="mollusk crustacean"
GROUP_A="cutting_implement tableware power_tool"
GROUP_B="color drug_of_abuse"

# Superseded: it holds the food senses, not the animals. Removed so a stray glob
# or a copied command line cannot pull it into a build.
rm -f "$SEEDS/wordnet-shellfish.lexicon.json"

export_root() { # id depth name
  python tools/export_wordnet.py --root "$1" --depth "$2" --out "$SEEDS/wordnet-$3.lexicon.json"
}

# fish at depth 8: the only root truncated at depth 6 (101 synsets lost).
export_root oewn-02514684-n 8 fish
export_root oewn-01943377-n 6 mollusk
export_root oewn-01977414-n 6 crustacean
export_root oewn-03159112-n 6 cutting_implement
export_root oewn-04389081-n 6 tableware
export_root oewn-04003842-n 6 power_tool
export_root oewn-04963771-n 6 color
export_root oewn-03253661-n 6 drug_of_abuse

# Named explicitly, never globbed: a glob picks up the mini test fixture.
flags() { for r in "$@"; do printf -- "--lexicon %s/wordnet-%s.lexicon.json " "$SEEDS" "$r"; done; }

# Inputs are written per build, then merged. An entity's embedded text depends on
# whether any lexicon defines the word: coffee is "coffee: a medium brown color"
# when color is in the build and the bare word "coffee" when it is not. One list
# from the largest build therefore misses build A's bare overlay words, which is
# the KeyError the first run hit for cannabis, coffee, coral, hazel and heather.
mkdir -p build/inputs-A build/inputs-B build/inputs-all
python tools/build_snapshot.py --write-inputs build/inputs-A/ \
  $(flags $FIRST $BASE $GROUP_A) --overlay "$SEEDS/overlay.curated.json"
python tools/build_snapshot.py --write-inputs build/inputs-B/ \
  $(flags $FIRST $BASE $GROUP_A $GROUP_B) --overlay "$SEEDS/overlay.curated.json"

# C locale so the merged order is identical on every machine.
for kind in terms texts; do
  LC_ALL=C sort -u "build/inputs-A/$kind.txt" "build/inputs-B/$kind.txt" > "build/inputs-all/$kind.txt"
done
echo "merged inputs: $(wc -l < build/inputs-all/texts.txt) texts, $(wc -l < build/inputs-all/terms.txt) terms"

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

build A $FIRST $BASE $GROUP_A
build B $FIRST $BASE $GROUP_A $GROUP_B

echo "done: build/coverage-A.json and build/coverage-B.json"
