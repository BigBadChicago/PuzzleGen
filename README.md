# PuzzleGen

A daily puzzle game engine over a shared, governed knowledge graph. One
governed body of facts, many independent game lenses, one engine between
them.

Start here:

- `docs/architecture.md` — full decision record, every phase.
- `docs/handoff.md` — current status and what to do next.
- `docs/platform/` — the platform documents: vision, requirements, the
  cartridge model, the developer guide, certification, policy, security,
  operations and roadmap.

## Layout

Two kinds of thing, in two kinds of distribution.

```
puzzlegen/          the platform: core, graph, providers, content, engine
tools/              platform tools (snapshot building, export, review)
content/seeds/      platform content (lexicon exports and their fixtures)
games/<name>/       one game, as its own installable distribution
scripts/            repository scripts, including the full test run
```

A game is its own distribution with its own `pyproject.toml`, package, tests,
tools and content. It advertises itself through the `puzzlegen.games` entry
point group, so installing it adds the game and uninstalling it removes the
game, with no edit anywhere in the platform. The platform holds no list of
games and, by an enforced invariant, names none of them.

## Development

```
pip install -e ".[dev]"
for game in games/*/; do pip install -e "$game"; done
scripts/test-all.sh
```

Several suites run, not one: the platform's, which must pass with no game
installed, and each game's. A single run over all of them would hide the first
property, which is the one that makes the module boundary real.

Five games are in the tree. Two are lexical and three are not, which is the
point of the three: a platform whose every game reads a lexicon has
demonstrated that one kind of content works, not that content is replaceable.

| directory | game | content |
|---|---|---|
| `games/grouping` | four visible groups and a fifth nobody mentions | WordNet plus a curated overlay |
| `games/letters` | spell the category from its own members' letters | a curated taxonomy |
| `games/figures` | one trio of simple figures among figures that nearly are | a generated attribute space |
| `games/numbers` | a row of numbers and the one that does not belong | generated integers |
| `games/digits` | a code of digits and the clues that entail it | generated digits |

The platform registers its test fixtures as a pytest plugin
(`puzzlegen.testing`), so a game's tests get a graph to build content in with
no path manipulation and no copied conftest.

Snapshot-building tools (`tools/export_*.py`) require the extra dependencies
in `pip install -e ".[snapshot]"`. The engine itself never imports them. A
coverage measurement needs neither: it is structural, so a build with
`--dev-embeddings` measures it exactly.

The three non-lexical games need none of it at all. Their content is three
declarations on a command line, so `docs/build-symbolic-snapshot.sh` builds
the snapshot they read in seconds with no model, no network and no lexicon,
and reproduces it byte for byte.

## Builds run in GitHub Actions

Builds are dispatched, not run in a working session. Each workflow runs one
task of `scripts/ci.sh` and ends its log with a short summary between
`===== SUMMARY =====` markers, also shown on the run page, so the result is a
dozen lines rather than a build log.

| workflow | runs on | does |
|---|---|---|
| `tests` | push to main, PRs, dispatch | platform alone, then every suite, on 3.11 and 3.13 |
| `coverage artifacts` | content changes, dispatch | rebuilds A2 and fails if the committed coverage files differ; dispatched on a branch with `commit` set, it commits them instead |
| `snapshot` | dispatch only | A2 with real vectors, then days; snapshot, vectors and days uploaded as an artifact |
| `symbolic snapshot` | symbolic game or platform changes, dispatch | rebuilds the symbolic snapshot and the three committed day runs, and fails if any differs |

The same tasks run locally: `scripts/ci.sh tests`, `scripts/ci.sh coverage`,
`DAYS=5 scripts/ci.sh snapshot`, `scripts/ci.sh symbolic`.
