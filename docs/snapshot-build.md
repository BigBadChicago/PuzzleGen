# Building the first real snapshot

Everything before this point ran against test fixtures: an 8 synset lexicon and
a 23 entity curated file. This is the procedure that produces a snapshot made
of real content.

The figures below were measured by running every step against the Open English
WordNet 2024 edition (the same 12,912,118 byte file that `python -m wn download
oewn:2024` fetches). One step could not be run where the figures were measured,
command 7, because it downloads an embedding model. Everything else printed
exactly what is shown.

## Step 0: prerequisites

```bash
pip install -e ".[dev]"
pip install torch --index-url https://download.pytorch.org/whl/cpu   # no GPU
pip install -e ".[snapshot]"
python -m wn download oewn:2024
```

The engine never imports `wn`, `wordfreq` or `sentence-transformers`. These
commands run offline and the resulting JSON is committed, which keeps the
runtime install small and makes two machines importing the same file produce
the same graph.

## Step 1: choose the roots

A root is one synset, and the export takes everything within three hyponym
levels below it. Name it as `lemma.pos.NN`: the lemma, its part of speech, and
its position among that lemma's senses of that part of speech. The tool prints
the synset it resolved and its definition before it writes anything, because
sense numbers come from the lexicon and differ between lexicons and editions.
Read that line. `plant.n.01` is a factory and `plant.n.02` is the organism.

To see what a lemma could mean before choosing:

```bash
python tools/export_wordnet.py --root plant --pos n --list
```

| Root | Resolves to | Why |
|---|---|---|
| `animal.n.01` | `oewn-00015568-n`, a living organism characterized by voluntary movement | Dense, familiar, deeply nested. The reliable group source. |
| `plant.n.02` | `oewn-00017402-n`, (botany) a living organism lacking the power of locomotion | Familiar nouns, several overlay collisions (sage, mint). |
| `tool.n.01` | `oewn-04459089-n`, an implement used in the practice of a vocation | Concrete artifacts. |
| `vehicle.n.01` | `oewn-04531608-n`, a conveyance that transports people or objects | Small and clean, low polysemy. |
| `musical_instrument.n.01` | `oewn-03806455-n`, any of various devices that can be used to produce musical tones | Distinct vocabulary, little overlap with the others. |

Five roots rather than four, because four visible groups drawn from a pool of
four gives every board the same domains.

## Step 2: the seven commands

Five exports, then frequency, then embeddings, with a prepare step between the
exports and the last two that is not one of the seven.

```bash
# 1 to 5: the lexical exports
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

### What each one printed

| Command | Printed |
|---|---|
| 1 animal | `root: oewn-00015568-n  a living organism characterized by voluntary movement` then `wrote 282 synsets and 449 senses` |
| 2 plant | `root: oewn-00017402-n  (botany) a living organism lacking the power of locomotion` then `wrote 749 synsets and 1854 senses` |
| 3 tool | `root: oewn-04459089-n  an implement used in the practice of a vocation` then `wrote 259 synsets and 407 senses` |
| 4 vehicle | `root: oewn-04531608-n  a conveyance that transports people or objects` then `wrote 164 synsets and 287 senses` |
| 5 instrument | `root: oewn-03806455-n  any of various devices or contrivances that can be used to produce musical tones or sounds` then `wrote 111 synsets and 209 senses` |
| prepare | `wrote build/terms.txt` and `wrote build/texts.txt`, 3229 lines each |
| 6 frequency | `wrote 2379 scores to content/seeds/frequency.json`, 74 percent of the terms |
| 7 embeddings | `wrote 3229 vectors`, which must equal the line count of `texts.txt` |

A first line that names a different synset than the table means the root
resolved to the wrong sense. Stop and use `--list`.

The prepare step imports the exports into a throwaway in-memory graph and reads
the entities back, so `texts.txt` holds exactly the strings the build will
later look up in the embedding table. Re-run it after changing any lexicon: a
`texts.txt` from an older set of exports produces a table the build rejects at
its last step, with a `KeyError` naming five words.

Command 7 is the slowest and needs network access the first time, to fetch the
90 MB `sentence-transformers/all-MiniLM-L6-v2` model. It was not run where
these figures were measured, so its count is what the arithmetic says it must
be, not what was observed.

## Step 3: read the collision report

```bash
python tools/report_lemma_collisions.py \
    --lexicon content/seeds/wordnet-animal.lexicon.json \
    --lexicon content/seeds/wordnet-plant.lexicon.json \
    --lexicon content/seeds/wordnet-tool.lexicon.json \
    --lexicon content/seeds/wordnet-vehicle.lexicon.json \
    --lexicon content/seeds/wordnet-instrument.lexicon.json \
    --out build/lemma-collisions.json
```

Measured: 3102 lemmas, 97 with more than one sense (90 with two, 7 with
three), and 6 that appear under two roots (`borer`, `bugle`, `embryo`,
`rocket`, `sledge`, `viola`). Nothing in this report blocks a build. It is read
by a person.

### The finding that matters: the overlay barely meets the lexicon

Of the 144 overlay members, **only 10 appear in these five exports**: `chime`,
`comb`, `drum`, `engine`, `file`, `mint`, `rake`, `sage`, `saw` and `tap`. The
other 134 are absent, across every one of the fifteen categories. All 134 do
exist in WordNet as nouns; they live in parts of the hierarchy these five roots
do not reach (colors, weather, sounds, abstractions, acts, geological
formations).

What that means for the build:

- Those 134 become entities with no definition and no taxonomic parent. Their
  embeddings are computed over the bare word, not a gloss.
- They can act as hidden group members. They cannot also belong to a visible
  group, because they have no category in the lexical taxonomy.
- The design's central move, one word belonging to a visible group and to a
  hidden one, is available for only those 10 words.

This is not a build failure and the report does not treat it as one. It is a
content decision, and it is made before batch 2 sizes the game's content
requirements.

Adding roots to reach the missing words does not scale. Measured against the
real lexicon at depth 3, each extra root covers only a few of the 134:

| Extra root | Synsets exported | Overlay words it reaches |
|---|---|---|
| `artifact.n.01` | 2136 | 29 |
| `abstraction.n.06` | 1366 | 17 |
| `communication.n.02` | 830 | 15 |
| `attribute.n.02` | 1727 | 15 |
| `act.n.02` | 1394 | 11 |
| `event.n.01` | 671 | 11 |
| `geological_formation.n.01` | 232 | 8 |
| `substance.n.01` | 749 | 7 |

The best four together (`artifact`, `abstraction`, `substance`,
`geological_formation`, about 4,500 synsets, roughly three times the current
snapshot) reach 55 of the 134. The other 79 include nearly all of the color,
sound, scent and cold weather words, which sit deeper than three levels or
under roots not listed here.

`saw` is the only overlay member with more than one sense in these exports.
Both senses are tools, a hand tool and a power tool, so the merge is harmless.

## Step 4: build

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

### What it printed (with development embeddings)

```
records:
  categories       1541
  entities         3229
  embeddings       3229
  frequencies      2379
  relationships    3308
per provider:
  overlay-seed     categories=15, entities=144, relationships=150
  wordnet:oewn     categories=1565, entities=3206, relationships=3206
frequency : scored 2379, missing 850
activated : 8078
```

`wordnet:oewn` sums the five lexicons, and its category count equals the sum of
the five exports' synsets (282 + 749 + 259 + 164 + 111 = 1565). The graph holds
fewer categories than that plus the overlay's fifteen because synsets shared
between roots merge.

A real build passes `--embeddings content/seeds/embeddings.json` in place of
`--dev-embeddings`, and must not print the development embeddings warning.

## What a good build looks like

- `entities` in the low thousands and `embeddings` exactly equal to it.
- `frequency: missing` well under `scored`. It is 850 against 2379 here,
  because rare species names and multiword terms have no corpus score.
- `activated` a few thousand.
- Fifteen active overlay categories, each with ten active members.
- No warning mentioning development embeddings.

## Afterwards

Commit the five lexicon files, `frequency.json`, `embeddings.json` **and**
`content/graph.sqlite`. Do not commit `build/`.

The sqlite file was previously listed here as derived and regenerable, and
that stopped being true the moment review began. Two things live in that file
that no rebuild can reproduce: the `ReviewDecision` ledger, which is a record
of what a curator decided on which day and is the sole evidence behind every
credited accept, and the pinned build time a byte-identical rebuild needs.
Losing the file loses the ten-day accept history outright, and the review
system's whole anti-abuse property is that those ten days cannot be
manufactured. Regenerating the graph is cheap; regenerating ten days of
curation is impossible.
