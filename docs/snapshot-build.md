# Building the first real snapshot

Everything before this point has run against test fixtures, an 8-synset lexicon
and a 23-entity curated file. This is the procedure that produces a snapshot
made of real content.

Run it on a machine where `wn` and `wordfreq` can be installed. The engine
never imports either; these commands run offline and the resulting JSON is
committed, which is what keeps the runtime install small and what makes two
machines importing the same file produce the same graph.

```
pip install 'puzzlegen[snapshot]'
```

## Step 0 — choose the roots

Five lexical roots, one export each. Five rather than four because the grouping
board shows four visible groups per day and drawing four from a pool of four
means every board has the same domains in it. Depth 3 keeps each export in the
low thousands of synsets; deeper exports reach terms no player recognises and
inflate verification cost for nothing.

| Root | Why |
|---|---|
| `animal.n.01` | Dense, familiar, deeply nested. The reliable group source. |
| `plant.n.02` | Familiar nouns, shallow taxonomy, many overlay collisions (sage, plum, rust). |
| `tool.n.01` | Concrete artifacts; the source of most `thing with teeth` members. |
| `vehicle.n.01` | Small and clean, good for a visible group that is unambiguous. |
| `musical_instrument.n.01` | Distinct vocabulary, low overlap with the other four. |

## The seven commands

Five exports, then frequency, then embeddings.

```bash
# 1-5: the lexical exports
python tools/export_wordnet.py --root animal.n.01 --depth 3 \
    --out content/seeds/wordnet-animal.lexicon.json

python tools/export_wordnet.py --root plant.n.02 --depth 3 \
    --out content/seeds/wordnet-plant.lexicon.json

python tools/export_wordnet.py --root tool.n.01 --depth 3 \
    --out content/seeds/wordnet-tool.lexicon.json

python tools/export_wordnet.py --root vehicle.n.01 --depth 3 \
    --out content/seeds/wordnet-vehicle.lexicon.json

python tools/export_wordnet.py --root musical_instrument.n.01 --depth 3 \
    --out content/seeds/wordnet-instrument.lexicon.json

# prepare: the two input lists the next two commands consume
python tools/build_snapshot.py --write-inputs build/ \
    --lexicon content/seeds/wordnet-animal.lexicon.json \
    --lexicon content/seeds/wordnet-plant.lexicon.json \
    --lexicon content/seeds/wordnet-tool.lexicon.json \
    --lexicon content/seeds/wordnet-vehicle.lexicon.json \
    --lexicon content/seeds/wordnet-instrument.lexicon.json

# 6: corpus frequency, scored per lemma
python tools/export_frequency.py --terms build/terms.txt \
    --out content/seeds/frequency.json

# 7: sentence embeddings, computed over "name: definition"
python tools/export_embeddings.py --texts build/texts.txt \
    --out content/seeds/embeddings.json
```

The prepare step writes two files because the two commands want different
things. Frequency is scored per lemma; embeddings are computed over the text a
game would actually show, which includes the gloss, because two entities named
identically are told apart by their definition and not by their label. Feeding
one list to both produces an embedding table whose keys never match what
`attach_embeddings` asks for, and the build fails at the last step with a
`KeyError` listing five words.

## Before building: read the collision report

```bash
python tools/report_lemma_collisions.py \
    --lexicon content/seeds/wordnet-animal.lexicon.json \
    --lexicon content/seeds/wordnet-plant.lexicon.json \
    --lexicon content/seeds/wordnet-tool.lexicon.json \
    --lexicon content/seeds/wordnet-vehicle.lexicon.json \
    --lexicon content/seeds/wordnet-instrument.lexicon.json \
    --out build/lemma-collisions.json
```

Three sections matter, in this order:

1. **`overlay members the lexicon does not supply`.** Each one becomes an
   entity with no definition, no frequency band and no taxonomic parent, so it
   can only ever be a hidden-group member and never a visible-group one. A
   handful is fine. Tens means the overlay and the lexicon have drifted, and
   the fix is to change the overlay word, not to widen the export.
2. **`overlay members with more than one sense`.** These are the dangerous
   ones. Under lemma identity the senses merge, so `crane` carries the bird's
   parents and the machine's parents at once, and a board can legitimately
   place it in two visible groups. Read each one and decide: keep it because
   the collision is the point (`rust` as a color and as corrosion is a good
   puzzle), or replace it because the collision is noise.
3. **`lemmas exported under more than one root`.** Usually a sign two roots
   overlap more than intended. Harmless in small numbers.

Nothing in this report blocks a build. It is read by a person.

## Build

```bash
python tools/build_snapshot.py \
    --db content/graph.sqlite \
    --label 2026.09.1 \
    --now 2026-09-28T12:00:00+00:00 \
    --lexicon content/seeds/wordnet-animal.lexicon.json \
    --lexicon content/seeds/wordnet-plant.lexicon.json \
    --lexicon content/seeds/wordnet-tool.lexicon.json \
    --lexicon content/seeds/wordnet-vehicle.lexicon.json \
    --lexicon content/seeds/wordnet-instrument.lexicon.json \
    --frequency content/seeds/frequency.json \
    --embeddings content/seeds/embeddings.json
```

`--now` pins the timestamp stamped on every record built in this run. Every
governed record carries a creation time and the content hash covers it, so two
builds of identical inputs agree on their hash only if it is pinned. A release
build passes it; a local one does not need to.

The overlay seed is imported automatically, last, after every lexicon. It is
not a flag: without it there is no second axis, and a grouping game with no
second axis is four piles.

## What a good build looks like

- `categories` in the hundreds to low thousands, `entities` in the thousands.
- `frequency: missing` small relative to `scored`. A large missing count means
  the term list and the lexicon disagree, usually because the exports were
  re-run after the prepare step.
- `embeddings` equal to the entity count. Anything less is a table that does
  not cover the graph, and `attach_embeddings` would have raised rather than
  written a short one.
- No warning mentioning development embeddings. That warning means
  `--dev-embeddings` was used and the snapshot carries no semantic signal; it
  is a local convenience, never a published artifact.
- Fifteen active overlay categories, each with ten active members.

## Afterwards

Commit the five lexicon files, `frequency.json` and `embeddings.json`. Do not
commit `build/` or the sqlite file; they are derived and regenerable from what
is committed.
