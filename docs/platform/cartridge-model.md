# The cartridge model

The one idea the platform is organised around. A game is a cartridge: a
self contained distribution that the machine runs, that supplies the game and
nothing else, and that can be pulled out without the machine noticing.

This document states what a cartridge is, what it may and may not do, and the
whole of its lifecycle from written to removed. The isolation question, which is
the hardest part, is decided in `docs/external-games.md`; this document says
where that decision sits in the lifecycle rather than restating it.

## What makes it a cartridge rather than a plugin

Three properties, and the third is the one that is usually missing from systems
that call themselves pluggable.

1. **It arrives as its own distribution.** Its own `pyproject.toml`, package,
   tests, tools and content, under `games/<name>/`. It is installed and
   uninstalled with the ordinary packaging tools.
2. **It announces itself.** It declares the `puzzlegen.games` entry point group,
   so installing it adds the game and removing it removes the game.
3. **The platform cannot name it.** The platform holds no list of games and
   mentions no game's name anywhere in its own source. This is enforced by a
   test, not by convention, which is what makes the first two properties real
   rather than aspirational. Without it, "pluggable" degrades into a registry
   somebody has to edit.

The consequence worth stating: the platform's correctness cannot depend on
which cartridges exist. The platform's own test suite must pass with no
cartridge installed at all, and `scripts/test-all.sh` runs it that way first
precisely so that this property cannot rot.

## What a cartridge is made of

A cartridge supplies a lens and nothing else. Concretely, it implements the
`GamePlugin` protocol (`puzzlegen/engine/plugin.py`):

| it supplies | it means |
|---|---|
| `describe()` | its static identity and every contract it promises |
| `get_content_requirements()` | which slice of the graph it needs |
| `generate_candidates()` | candidate boards, from content plus a seeded generator |
| `assemble()` | a candidate turned into a concrete board |
| `verify()` | its own proof that a board satisfies its contract |
| `measure_difficulty()` | where a board sits on its own scale |
| `score()` | a finished session turned into a number |
| `grade_move()` | one move judged |
| `get_hint()` | a hint, derived from its witness where it has one |
| `render()` | a presentation model, not pixels |
| `create_share_artifact()` | share content, in its declared vocabulary |
| `witness()` | optional, protocol 1.2.0, where the game has a real elimination |
| `refine_content_requirements()` | optional, protocol 1.3.0, a later round of content questions informed by the earlier ones |
| `verify_part()` | optional, protocol 1.3.0, one stage of a composite board proved, given the stages already solved |

What it does not supply, and must not try to: storage, identity, scheduling,
session management, the accessibility layer's wording, the share leak check, the
graph, the snapshot, or the random number generator. Each of those is a platform
service it receives or that acts on its output.

## What a cartridge may not do

- **It may not own knowledge, by default.** Content comes from the graph through
  a declared requirement. A cartridge carrying its own corpus is a cartridge
  whose content nobody governs, which is why a shipped content file is the
  exception and carries four conditions rather than being the ordinary path. See
  `content-policy.md`.
- **It may not keep anything.** It has no storage, no player identifier and no
  device access, so nothing it produces persists on a player's device, and
  removing it erases its content. What removal never touches is the player's
  play history, which the platform owns.
- **It may not define a new generator kind.** It picks a declaration from what
  the platform's generators produce. A game needing a content shape no generator
  makes needs a platform change, and that step is deliberately governed.
- **It may not read a clock, the network, the filesystem, the environment, or
  any source of randomness other than the seeded generator it was handed.** Each
  of those would make a day's board depend on something the manifest does not
  record, which breaks the one promise the manifest exists to make.
- **It may not reach past its ports.** It is handed a content port and nothing
  else. Walking attributes from the port to anything behind it is a violation
  even where the current in-process boundary cannot stop it.
- **It may not convey information by colour alone, or require a pointer.** See
  `accessibility-standard.md`.

## Lifecycle

### 1. Written

The author develops against an installed platform and a built snapshot. The
platform registers its test fixtures as a pytest plugin (`puzzlegen.testing`),
so the author's tests get a graph to build content in with no path manipulation
and no copied conftest. See `game-developer-guide.md`.

### 2. Declared

The author writes a content declaration: a few reviewable lines naming the slice
of the graph the game draws on, reproducible by construction. A declaration is
the default, because it can be reviewed in a minute and rebuilt from the graph,
where a file can only be trusted.

Where no declaration can express the content, the author may instead ship a
curated content file, under the four conditions in `content-policy.md`. The
platform's own letters game is the worked example of why the exception exists:
membership, not letter matching, was the binding constraint, and at medium
frequency one WordNet category in 7,050 had six or more eligible members,
because a category's members are its hyponyms and a frequency filter cuts across
that structure rather than along it.

### 3. Submitted

The author submits the distribution with a committed thirty day run against a
named snapshot. See `publishing-and-certification.md`.

### 4. Certified

The platform regenerates the thirty day run and compares it byte for byte.
Admission is tested by regeneration rather than by inspection: a reviewer
reading code cannot tell whether a game is deterministic, and a rebuild can.
Contract conformance, the accessibility gate and the share vocabulary are
checked at the same time.

### 5. Listed

The cartridge appears in the catalogue. Listing is a platform decision, not an
automatic consequence of passing certification.

### 6. Activated

The operator activates the game against a day key, which is what makes the
platform generate days for it. Activation is bounded by `DEFAULT_MAX_ACTIVE`
and recorded as an `ActivationEvent`, so "which games ran on 2026-10-16" has an
answer afterwards.

### 7. Held

A player adds the cartridge to one of their slots. The slot is the player's
side of the cap: it bounds one person's daily attention, and it is a different
number from the operator's generation budget.

### 8. Played

The client requests the day, the player plays, and the move ledger is graded
server side under the scoring version in force. Grading never happens on the
client, because grading on the client means shipping the answer to the client.

### 9. Removed

The player frees the slot. Their history, streak and best scores for that game
survive, and re-adding it restores them. Removal is not deletion.

### 10. Deactivated or withdrawn

The operator stops generating days for the game, which leaves every played day
intact and readable. A withdrawal for cause additionally retracts affected days,
and retraction restores the streaks of players who were punished by a board that
was wrong.

## Where isolation sits

Steps 4 through 8 are where a hostile or careless cartridge would do damage, and
the honest position is this: today the boundary is enforced by invariants rather
than by isolation, the invariants have known evasions, and the policy that makes
this acceptable is that every listed cartridge is signed and reviewed. The
subprocess and WASM transports are specified and staged behind a named trigger,
which is the arrival of an author the project does not want to review. Somebody
decides that the trigger has arrived; it is not a threshold that gets crossed
quietly. The full analysis, with measurements, is in `docs/external-games.md`.

## The analogy, and where it breaks

The cartridge analogy is load bearing in one direction and misleading in
another. It is accurate about coupling: the machine supplies power and a screen,
the cartridge supplies a game, and neither reads the other's internals.

It is wrong about trust. A console cartridge is a physical object that a
manufacturer made under licence; a PuzzleGen cartridge is code that runs in the
platform's own process. Nothing about the analogy makes a cartridge safe, and
reasoning from it is exactly how a plugin system ends up with no sandbox. The
word describes the shape of the boundary, not its strength.
