# Building a real snapshot

This is the procedure that produces a snapshot made of real content, as the
build actually stands now. An earlier version of this doc described a five then
seven root build at depth 3 with measured figures from a 3,229 entity snapshot;
that build no longer exists. The current build is 23 roots and produces roughly
12,000 entities. Where this doc gives a number, it is from the current build or
marked as not yet re-measured; do not trust a figure here you have not seen a
tool print.

The authoritative, runnable version of the root set and the exact commands is
`docs/rebuild-with-new-roots.sh`. This doc explains the steps and the reasons;
that script is what you run. If the two ever disagree, the script wins, because
it is executed and this is prose.

## Step 0: prerequisites

```bash
pip install -e ".[dev]"
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -e ".[snapshot]"
python -m wn download oewn:2024
```

The engine never imports `wn`, `wordfreq` or `sentence-transformers`. These
commands run offline after the one model download, and the resulting JSON is
committed, which keeps the runtime install small and makes two machines
importing the same files produce the same graph. The build needs Python 3.11+
(the engine uses `StrEnum`).

The embedding step fetches the 90 MB `sentence-transformers/all-MiniLM-L6-v2`
model the first time; it needs network access once. If `python -m wn download`
cannot reach its host, the OEWN 2024 edition is also downloadable directly from
the GitHub release `english-wordnet-2024.xml.gz` and loadable with `wn.add`.

## Step 1: the roots

A root is one synset. The export takes everything within a given number of
hyponym levels below it (the `--depth` flag; default 3, but the real build uses
6 for most roots and 8 for `fish`). Name a root by synset id (stable across
editions) or as `lemma.pos.NN`. The tool prints the synset it resolved and its
definition before writing anything, because sense numbers come from the lexicon
and differ between editions. Read that line: a first line naming a different
synset than intended means the root resolved to the wrong sense; stop and use
`--list`.

```bash
python tools/export_wordnet.py --root plant --pos n --list
```

### The current 32 roots

`content/seeds/lexicon-roots.json` is where a root is declared, and the only
place. Each entry names the synset id, the lemma that synset carries, the
depth, and the WordNet edition the ids were resolved against. The rebuild
script reads it instead of carrying its own copies, and
`tests/unit/test_lexicon_seeds.py` holds every committed export to it, so a
file whose subtree disagrees with its name fails on push. The tables below are
the reasons each root was chosen; the manifest is what a build obeys.

Three names are shorthands, and the manifest records what they really mean:
`fruit` is `edible fruit`, `instrument` is `musical instrument`, and `kitchen`
is `kitchen utensil`. Each is the subtree a puzzle wants rather than the one
the bare word names, and each was undeclared until the manifest existed, which
is the same shape as the `hand tool` bug with a happier accident behind it.

Sixteen base roots, seven added to close overlay coverage gaps, and nine more in
batch 2. All at depth 6 except `fish` at depth 8 (it was the only root truncated
at depth 6, losing 101 synsets).

Base (16): `bird`, `carnivore`, `fish`, `flavorer`, `flower`, `fruit`,
`garment`, `herb`, `insect`, `instrument`, `kitchen`, `planet`, `reptile`,
`sport`, `hand_tool`, `vehicle`.

`hand_tool` was called `tool` in this list and in its filename until
2026-10-10. The file held the `hand tool` subtree (`oewn-03494245-n`, 164
synsets) throughout; only the name was wrong. `wordnet-tool.lexicon.json` now
holds the real `tool` subtree, which batch 2 added. A root list that says
`tool` and means `hand tool` builds a different snapshot and nothing errors.

Added (7), with synset ids, because these ids are what the rebuild script uses:

| Root | Synset id | Depth | Why added |
|---|---|---|---|
| `mollusk` | `oewn-01943377-n` | 6 | clam, oyster, snail (animal senses) |
| `crustacean` | `oewn-01977414-n` | 6 | crab, lobster, shrimp (animal senses) |
| `cutting_implement` | `oewn-03159112-n` | 6 | cutlery, knife, scissors |
| `tableware` | `oewn-04389081-n` | 6 | cutlery, fork |
| `power_tool` | `oewn-04003842-n` | 6 | chainsaw, jigsaw |
| `color` | `oewn-04963771-n` | 6 | hazel, heather, coral, indigo (colour senses) |
| `drug_of_abuse` | `oewn-03253661-n` | 6 | cannabis, marijuana |

Batch 2 (9), which closed the last of the overlay gap:

| Root | Synset id | Depth | Why added |
|---|---|---|---|
| `dairy_product` | `oewn-07859751-n` | 6 | cheese |
| `tool` | `oewn-04459089-n` | 6 | comb, drill, hoe, mower, rake, ram |
| `rodent` | `oewn-02332053-n` | 6 | beaver, rodent |
| `nut` | `oewn-13157333-n` | 6 | coconut, walnut |
| `bulbous_plant` | `oewn-13155061-n` | 6 | iris, onion |
| `vine` | `oewn-13121436-n` | 6 | hop, hops |
| `angiospermous_tree` | `oewn-13130492-n` | 6 | holly, walnut, mate |
| `marsh_plant` | `oewn-13143123-n` | 6 | rush |
| `shrub` | `oewn-13133423-n` | 6 | broom, erica, hemp, jasmine |

`dairy_product` is passed before `shrub` so the merged `cheese` entity takes the
dairy gloss rather than tall mallow, the shrub. `heath` was in the candidate
list and is not here: both its targets sit inside `shrub` at depth 6, and
`heath` still appears as a usable visible category without being a root.

`mollusk` and `crustacean` replaced a superseded `shellfish` root, which held
the food senses (the flesh of a clam) rather than the animals the overlay
means. `shellfish` must not be in a build; the rebuild script deletes it
defensively. `mollusk` and `crustacean` are listed first in the build order so
a merged category such as `crab` takes the crustacean gloss rather than the
rowing stroke from `sport` (parents merge regardless of order; order only sets
the displayed gloss).

**`color` carries a known risk, and it is still open.** It makes `chromatic
color` a lexical parent, which lets game 1 form a visible colour group next to
the hidden group `word that is also a color`, giving the answer away. Build A
excludes `color` and `drug_of_abuse`; build B includes them. The generator gate
added in session 3 does **not** close it, because overlay categories are
taxonomy roots and there is no ancestor relation between `overlay.color` and
the lexical colour tree for a rule to find. See the handoff and decision log:
closing it needs an anchor recorded on the overlay category.

The overlay gap is closed: of 146 overlay members, 2 are unsupplied by build A
(`coral`, which wants the colour sense, and `coffee`, which wants a brewed
sense no root covers). Both of the remaining two are supplied by lexicons that
exist in the repository but that build A does not import.

## Step 2: export, prepare, frequency, embeddings

The sequence, per build, is: export each root, write the input lists, score
frequency, compute embeddings. `docs/rebuild-with-new-roots.sh` does all of it
for builds A and B; read it rather than retyping the commands. The non obvious
parts, which are where builds go wrong:

**Never glob the lexicon files.** Name each root explicitly. A glob over
`content/seeds/wordnet-*.lexicon.json` picks up the 8 synset test fixture
`wordnet-mini.lexicon.json` and any stray export, and silently builds the wrong
graph. This project has hit stray or duplicate files in that directory several
times; the script defines the root set as explicit lists for exactly this
reason.

**The prepare step must be re-run after any lexicon change.** `build_snapshot.py
--write-inputs` imports the exports into a throwaway in-memory graph and reads
the entities back, so `texts.txt` holds exactly the strings the build will later
look up in the embedding table. A `texts.txt` from an older set of exports
produces an embedding table the build rejects at its last step with a `KeyError`
naming a few missing words.

**Inputs depend on the exact root set, so A and B need merged inputs.** An
entity's embedded text is `word: definition` when some lexicon defines the word
and the bare `word` when none does. `coffee` is bare in build A and defined
(`coffee: a medium brown color`) in build B once `color` is present. Generating
one input list from the larger build therefore omits build A's bare words and
the build fails with a KeyError on them. The script writes inputs per build and
merges them with `LC_ALL=C sort -u` so one embedding export serves both.

**A clean checkout needs `--ignore-stale`.** `build_snapshot.py` refuses to
build when `frequency.json` or `embeddings.json` are older than a lexicon file.
A fresh `git clone` gives every file the same mtime, which trips this guard
spuriously. Pass `--ignore-stale` after confirming the seeds are actually
current. This guard is correct and worth keeping; it only misfires on a clone.

## Step 3: read the collision report

```bash
python tools/report_lemma_collisions.py \
    --lexicon content/seeds/wordnet-<root>.lexicon.json ... \
    --overlay content/seeds/overlay.curated.json \
    --out build/lemma-collisions.json
```

Name the same explicit root list. The report changes nothing; a person reads
it. Its "overlay words whose second meaning had a parent refused in the merge"
section is the one to check: on the current 23 roots it reports 0 overlay words
losing a parent, which matches the builder. (This report was rewritten to mirror
the builder's real merge behavior; an earlier version produced false alarms.
See the decision log.)

Order matters in one respect only: the first file to name a category sets its
gloss. Parents from a later file are unioned onto it, so no sense loses its
ancestry to file order. The build order in the rebuild script is chosen so the
displayed glosses read correctly, not to protect ancestry.

## Step 4: build

The build command is in the rebuild script. `--now` pins the timestamp stamped
on every record; the content hash covers it, so two builds of identical inputs
agree on their hash only when it is pinned. A release build passes it; a local
one need not. The overlay seed is imported automatically, last, after every
lexicon: without it there is no second axis, and a grouping game with no second
axis is four piles.

A real build passes `--embeddings content/seeds/embeddings.json`. `--dev-embeddings`
produces hash vectors that are fine for structural checks (does a board form)
but meaningless for anything using similarity (coverage, the temptation gate),
and it prints a warning that must be absent from a real build.

### What a good build looks like

- `entities` around 12,000 and `embeddings` exactly equal to it.
- `wordnet:oewn` category count equal to the sum of the exports' synsets minus
  the synsets shared between roots (which merge).
- `frequency: missing` well under `scored` (rare species and multiword terms
  have no corpus score).
- The overlay's active categories each with their active members.
- No development embeddings warning.

Exact counts for the current 23 root build are in `build/coverage-A.json` and
`build/coverage-B.json` (entities, usable tiles, lexical categories). Read those
rather than trusting a number memorized here.

## Afterwards: what to commit

Commit the lexicon files, `frequency.json`, and `embeddings.json`. These are the
seeds a build needs and without them the committed coverage results cannot be
reproduced (this exact gap, new roots generated but never committed, has bitten
the project before).

**Do not commit `content/graph.sqlite` to a normal build**, and `.gitignore`
now excludes `*.sqlite*` so it cannot happen by accident. The graph is
regenerable from the seeds above.

The one exception, and the reason this file's history is contradictory: once a
review ledger exists in the database, that database stops being purely derived.
The `ReviewDecision` ledger records what a curator decided on which day and is
the sole evidence behind every credited accept; a rebuild cannot reproduce it,
and the review system's anti-abuse property is that those days cannot be
manufactured. If and when the project runs real curation, the database (or a
dedicated export of that ledger) must be preserved outside the regenerable
build. Until then, the graph is a build output and stays out of git.

Never commit `build/`.
