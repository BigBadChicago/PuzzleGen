# PuzzleGen: Architecture and Decision Record

This file is the single source of truth for every architectural decision made
on this project, across every conversation. It is read at the start of every
future conversation, before any new work begins. It is appended to at the end
of every phase; it is never rewritten from scratch.

Reading order for any conversation in this project, highest priority first:
1. What the person says in the current conversation.
2. Files attached to the current conversation.
3. This project's own file context (memory).
4. Files pulled from the connected GitHub repository.

A repository file is the lowest-priority source because it can be stale
relative to what the person and the current conversation already know. It is
a reference of last resort.

## How to use this document

If you are a new conversation starting work on this project: read
`docs/handoff.md` first, not this file. The handoff document tells you
whether manual steps are needed before you can safely proceed, and it points
back here for the full decision history once you're clear to start. This file
is the reference; the handoff document is the gate.

## Project identity

- Project name: PuzzleGen (chosen after this project's working name, briefly
  the unnamed "shared knowledge graph daily puzzle engine", was replaced).
- Distinct from, and unrelated to, DAILYKIT and PokerFall, two other projects
  in this person's portfolio.
- Central architectural principle: one governed body of facts, many
  independent game lenses, one engine between them. A game is a lens, not an
  owner of knowledge.
- Second invariant: the engine must prove a generated puzzle is valid before
  it can become a published puzzle. Solvability alone is never sufficient.
- Third invariant: the shared graph is production infrastructure with its own
  quality, freshness, provenance, review and dependency lifecycle.

## Toolchain and language

- Python 3.11+ for the engine. Rationale: WordNet, wordfreq, embeddings, and
  exhaustive/constraint solvers all live in mature Python libraries; the
  browser shell consumes published manifests as static JSON and needs no
  engine port.
- TypeScript only in the browser shell. The manifest, and a JSON Schema
  generated from the engine's pydantic models, is the contract between them.
- pydantic v2 for every public data model. Frozen models for anything that
  contributes to a manifest hash.
- Modular monolith, not microservices. Explicit module boundaries now, so
  later extraction into services is possible without being required.
- pytest plus hypothesis for testing. Every storage-dependent test runs
  against both the in-memory and the SQLite backend via a parametrized
  fixture, so the two backends cannot silently diverge.

## Phase 1: Core primitives and the knowledge graph

**Normalisation.** The graph is normalised. `Entity` carries identity and
lifecycle only. Every assertion is a `Fact` (entity to literal) or a
`Relationship` (entity to entity), each with its own provenance, freshness
class and review status. The inline `types`/`properties` shape used in early
design sketches is a read model (`EntityView`) the content service assembles
on read, never the storage shape. Inline storage would give one provenance
record to a bundle of assertions sourced and refreshed differently.

**One lifecycle for every governed record.** `ReviewStatus` is shared across
entities, facts, relationships and categories. `USABLE_STATUS` is defined once
as `{ACTIVE}` so no gate can privately disagree about what "usable" means.

**Storage.** Nine typed repositories sit over one `DocumentStore` protocol,
implemented once in memory and once in SQLite. Porting to a new backend is
one new class. Two invariants hold on every backend: results are always
ordered by document id; absence from `get` is `None`, never an exception.
Querying an undeclared index field raises, so a missing index is a loud
development-time failure rather than a slow production scan. The `Query`
type offers only equality and set membership; range and text search would
need identical reimplementation per backend and are deliberately excluded.

**Deterministic randomness.** `DeterministicRng` is an HMAC-SHA256 counter
stream, seeded via `derive_seed(day_key, game_id, salt)`. Substreams are
derived by key (`rng.derive(label)`), not by consuming the parent's counter,
so adding a call site anywhere cannot change an unrelated part's output.
`randbelow` uses rejection sampling to avoid systematic modulo bias.

**Identifiers.** Ids are minted deterministically from a natural key
(`ids.for_entity`, `ids.for_fact`, etc.), readable when the key is a clean
slug (`entity:tiger`) and hashed otherwise. Provider-native ids are recorded
only in provenance, never used as primary keys, which is what makes a
provider replaceable without rewriting the graph. Fact ids include the value;
a corrected value mints a new fact rather than mutating one in place, so
dependency tracking shows exactly which puzzles used a superseded assertion.

**Categories may have multiple parents.** Real taxonomies fork (WordNet
routinely places one synset under two hypernyms). Two costs that normally
come with multi-parent trees are designed out rather than paid:
- Cycles: `parent_ids` is fixed at construction, and `CategoryRepository.put`
  refuses a category naming a parent that does not already exist in the
  store. A cycle would require a category to predate its own ancestor, which
  is structurally impossible. This one existence check is the entire
  cycle-prevention mechanism; no traversal-based checker exists anywhere.
- Ambiguous depth: `depth` remains a single integer (the longest path to a
  root), computed once from already-final parents via `Category.build`.
  `min_depth` records the shortest path alongside it, read by nothing yet,
  reserved for a future forked-ancestry game.
- A category needing an additional parent later is retired and replaced
  (mirrors how a deprecated `Source` names its successor), never edited in
  place.

**Model-level governance invariants**, enforced at construction and therefore
unbypassable by any later layer: an ACTIVE record must carry provenance; an
ACTIVE non-static record must carry `verified_at` and `next_review_at`; an
ACTIVE JUDGED record must name a reviewer; record confidence may never exceed
its best provenance confidence; COMPUTED provenance must record
`derived_from`.

## Phase 2: Providers and the content service

**Import, not live query.** The `ContentProvider` protocol is built around
`load()` pulling a whole bundle into a snapshot, never around querying during
generation or play. `fetch_entity` exists only for re-verification sweeps and
is never called from the generation path. A puzzle that consulted a live
source could never be regenerated once that source changed.

**Three-step content lifecycle: import, activate, seal.**
`Normalizer` refuses construction with `import_status=ACTIVE`, so import
structurally cannot publish content. `SnapshotBuilder.activate()` applies a
stated `ActivationPolicy` and is the only route to ACTIVE status, in
dependency order (categories parents-first, then entities, then facts and
relationships only when their subject/endpoints are already active). Sealing
computes a content hash and freezes the snapshot (`verify_snapshot` refuses a
snapshot whose recomputed hash no longer matches).

**JUDGED assertions are never auto-approved.** The normalizer routes them to
`PENDING_REVIEW` regardless of confidence; the default `ActivationPolicy`
excludes the class. Automation can confirm a source said something; it cannot
confirm an interpretation is correct.

**Category membership is a relationship, not a field.** An entity's declared
categories become real `is_a` relationships with their own provenance,
freshness class and review status, auditable and retractable independently.

**Entity identity is a per-import choice: `lemma` or `sense`.** Lemma merges
every provider's assertions about "tiger" into one entity (what makes facts
reusable across games and providers). Sense keeps polysemous words apart
(what a vocabulary game and a WordNet import need). Curated content uses
lemma; WordNet imports use sense.

**Category ids include the taxonomy.** A WordNet synset named "feline" and a
curated category named "feline" have different parents and depths; collapsing
them would silently merge two unrelated hierarchies.

**Merging across providers accumulates provenance and takes the weakest
status.** Two sources independently asserting the same entity is the good
case, not a conflict — their provenance accumulates. If either copy needs
review, the merged record does too.

**Heavy dependencies stay out of the engine entirely.** `wn`, `wordfreq` and
`sentence-transformers` are never imported by `puzzlegen/`. Three scripts
under `tools/` (`export_wordnet.py`, `export_frequency.py`,
`export_embeddings.py`) run once, offline, to produce versioned JSON
artifacts the engine reads. Proven by an AST-based invariant test
(`tests/invariants/test_architecture.py`), parametrized over each package
name, so this cannot regress silently.

**Embeddings are computed once and frozen into the snapshot.**
`DevHashEmbeddingProvider` is the deterministic, dependency-free development
stand-in; it labels itself `dev-hash` in snapshot metadata, and the import
report raises an explicit warning, so a development snapshot can never be
mistaken for one with real embedding signal. `TableEmbeddingProvider` serves
vectors a real model already produced, offline, at snapshot build time.

**Frequency bands are denormalised onto entities; raw scores are not.** Gates
read the band on every candidate. The raw Zipf score, source name, version
and retrieval date live in a separate `FrequencyRecord`, so replacing the
dataset never rewrites entity provenance. Multi-word terms score at their
rarest component; a phrase with any unscored word gets no score at all.

**The snapshot content hash excludes dependency records.** Dependencies are
written after publication; including them would change the snapshot hash
every time a puzzle was published against it.

## Phase 3: Query, similarity, policy, and the plugin boundary

**One `ContentQuery` type for every semantic operation**, not one type per
operation. The constraints overlap heavily; splitting them would multiply the
surface a plugin author must learn without adding expressive power.

**`EntityView` includes inherited types, not just direct membership.** A
tiger is a feline, a mammal, and an animal; ancestors also belong in the
dependency list, because retiring "mammal" must invalidate every puzzle that
grouped its descendants.

**Views expose no provenance, source id, provider key, confidence, or
status**, proven by an invariant test. A game that could read those could
infer which provider supplied a fact, defeating the point of the abstraction.
Record ids are present and must be; the port's ledger (below) is what keeps
that trustworthy rather than exploitable.

**`ContentPort` is the only capability a game ever holds.** It keeps a ledger
of every record id it has issued (`issued_ids()`), and `verify_references`
returns any id a plugin reports that it was never shown. This turns "plugins
must report fact_refs" from a documented obligation into an enforced one.
Query and total-result budgets (`PortBudget`) are enforced at the port, so one
game's accidental infinite loop cannot take down the day's generation for
every other game.

**Policy narrowing is asymmetric by construction.** Every field in
`ContentPolicy.narrow` either unions denials or takes the tighter bound, so a
game policy can refuse what the platform allows but can never admit what the
platform blocked. A category denial blocks its entire subtree; an allow list
blocks uncategorised records rather than silently admitting them.

**Similarity strategies are named in every result and resolved by string**
(`wu_palmer`, `leacock_chodorow`, `resnik`), never held as object references
by a game. Leacock-Chodorow and Resnik are normalised onto [0, 1] against
their own ceiling for the taxonomy in play. Lowest common ancestors return a
sorted tuple, plural, because forked ancestry can produce several equally
deep common ancestors, and picking one arbitrarily would break determinism.

**Group statistics report minimum and spread, not just mean.** A group with
three tight members and one outlier has a healthy mean and is still a bad
puzzle. `outliers()` measures relative to the group's own mean, since what
counts as distant depends on how tight the group already is.

**Every gate runs even when the query should already guarantee the result.**
A precise query is a performance property; the gate is the correctness proof.
This is defence in depth, stated as a requirement, and tested directly.

**`find_intersecting_groups` makes deliberate overlap constructible**, via
`secondary_predicate` or `secondary_category`. This is the operation the
grouping reference game needs for its hidden fifth-group / decoy-set design.

**Architecture invariants are AST-enforced and self-tested.** Four checks
run over every module: games import no storage, providers, network,
filesystem or environment; games import only their capability surface;
dependencies point downward through a fixed layer order (core, graph,
providers, content, engine, games, ops, cli); no layer above content holds a
repository over *graph* records specifically (the engine legitimately owns
repositories over its own puzzle/manifest/trace records — the rule is about
which repositories, not repositories at all). Four further tests plant known
violations and assert the checker catches them, proving the suite is not
vacuous.

## Phase 4: Generation pipeline and engine storage

**Generation stops at a draft; publication demands proof.**
`GenerationPipeline.publish()` requires a `VerificationResult` and a
`DifficultyMeasurement` as arguments. There is no code path that publishes
without them.

**Every plugin boundary type is a plain frozen dataclass of JSON-
representable values** (`GameDescriptor`, `PuzzleCandidate`, `Puzzle`,
`VerificationResult`, etc.), because these are also the wire format for the
subprocess RPC transport landing in phase 9. Discovering a non-serialisable
type after two games are written would be expensive.

**The solution lives on the puzzle record, never on the manifest.**
Publishing a manifest cannot leak an answer even by accident.

**`PuzzleManifest.public_view()` exists because the dependency ledger itself
can betray an answer.** Found by test, not by inspection: a manifest with no
solution field still let `fact_refs` reveal which entity was the odd one out
in a 3-felines-1-seabird puzzle, because the ref list was visibly lopsided.
`public_view()` omits `fact_refs` and every content hash; anything shown to a
player must go through it.

**Fabricated dependencies are caught before assembly**, using the port's
ledger from phase 3. Assembly may narrow a candidate's references but never
widen them; a widened set is rejected as `MISSING_FACT_REFS`.

**Plugin exceptions are caught and counted, never propagated.** A crashing
third-party game fails its own puzzle; it does not take down the other games
scheduled that day. `MAX_CANDIDATES` and `MAX_ASSEMBLY_ATTEMPTS` bound the
work spent on any one game per run.

**Activation is a dated event log, not a mutable flag**
(`GameRegistry.activate`/`deactivate`, backed by `ActivationEvent`).
"Which games ran on 2026-09-26" must give the same answer a year later.
Deactivating a game today never changes what `active_on()` reports for a
past day, and never touches its published history.

**Deactivation removes a game from future scheduling only.** Published
manifests stay published and playable; a deactivation that erased history
would make a day irreproducible.

**Manifest immutability is enforced in `ManifestRepository.put`**, not by
convention. An identical republish is idempotent; a changed one raises.
Correcting a published day means publishing a new puzzle and marking the old
one invalidated, leaving both visible.

**The repository-boundary invariant was corrected mid-phase.** The original
rule ("only the content layer holds repositories") was wrong: the engine
legitimately owns `PuzzleRepository`, `ManifestRepository`, and
`TraceRepository` for its own output records. The real rule, now encoded, is
that no layer above content may hold a repository over *graph* records
specifically, since every content-service gate sits between those records and
a puzzle. Games hold no repository of any kind, checked separately.

**A trace is written on every generation run, successful or failed**, with
structured rejection counts by stage, the full seed recipe, the content
requests the game declared, and port usage.
`TraceRepository.rejection_totals()` aggregates across runs, because the
useful question is "what keeps failing", not "why did this one run fail".

**`PLUGIN_PROTOCOL_VERSION` is `"1.0.0"`** (corrected from an invalid `"1.0"`
that the registry's own semver parser could not read; caught by the first
pipeline test).

## Phase 5: Verification, uniqueness, and difficulty

**Difficulty thresholds are declared per game**, not globally
(`DifficultyThresholds` on `GameDescriptor`). A branching factor of 4 is
trivial in one game and punishing in another; a single global scale would be
comparable across games and wrong for all of them. The descriptor validates
cutoff count against band count and requires bands declared in ascending
order, since cutoffs are read positionally. A score exactly on a cutoff lands
in the easier band.

**Verification is a generation gate, not a post-processing step.** A
candidate that fails to verify is discarded and the next is tried within the
same generation attempt, inside `GenerationPipeline.generate()` via an
injected `PuzzleVerifier`.

**`publish_outcome()` publishes the exact evaluation the pipeline already
accepted.** Re-verifying at publish time would open a window where a puzzle
verified once and was published on the strength of a second, different run.

**An incomplete enumeration satisfies no uniqueness contract, ever.** A
partial search can prove existence but never uniqueness — the unexamined
branch is precisely where a second solution would hide. This holds for every
contract, `EXACTLY_N` included.

**Two contracts need evidence a bare solution count cannot carry.**
`UNIQUE_UP_TO_TOLERANCE` must report a `collapsed_solutions` metric, so the
engine can distinguish "collapsed to one" from "never had more than one" (a
game that never actually applied its tolerance rule cannot claim credit for
it). `UNIQUE_MINIMAL_PATH` must report `longer_alternatives` and fails when
it is zero, since a shortest-path puzzle with no longer alternative asks
nothing. `VerificationResult` carries a generic `metrics` map, so future
contracts need no type change.

**The verifier checks that the puzzle's declared answer is among what the
solver actually found.** A puzzle that verifies, is unique, and marks the
correct player wrong is the single most dangerous failure mode; one hash
comparison catches it (skipped only when the solver explicitly marks its
solution list truncated).

**Self-contradictory verification results are protocol errors, not content
rejections** (`uniqueness.consistency_problem`): a result claiming complete
enumeration having examined zero states, or claiming truncation while
listing every solution, is a bug in the game's verifier.

**Three reusable exhaustive solver shapes live in the engine**
(`puzzlegen.engine.solvers`), so no game reimplements search and gets
completeness reporting subtly wrong:
- `enumerate_partitions`: anchors each group on its lowest remaining index,
  which is what collapses group-orderings of the same partition into one
  count. Without this, "exactly one solution" is never provable for a
  grouping puzzle. Supports a partition-level validity check in addition to a
  group-level one, which is where combination-level fairness (all groups
  individually valid, the combination not) gets caught.
- `enumerate_paths`: breadth-first, so shortest paths are found first;
  reports `minimal_length`, `minimal_count`, and `longer_alternatives`
  directly, which is exactly what `UNIQUE_MINIMAL_PATH` needs.
- `count_perfect_matchings`: constrained backtracking with most-constrained-
  row-first ordering, not permutation generation — twelve items visits a few
  thousand states rather than 479 million, which is the entire reason a
  definition-matching game can be proved rather than sampled.
- All three return a `SearchResult` with an honest `exhausted` flag; a budget
  overrun reports `SOUND_INCOMPLETE` and a lower bound, never a count dressed
  up as exact.

**`DailyRunner` isolates failures per game and can retry at neighbouring
difficulty bands** rather than publishing off-target while claiming
on-target, or publishing nothing at all.

**`DifficultyCalibration` records what a game's model actually produces**,
because per-game thresholds can be wrong per game; `unused_bands()` is how a
curator later learns a band nobody lands in is misconfigured rather than
genuinely rare.

## Session identity (decided ahead of phase 6, for phase 6 to implement)

Three tiers, layered rather than competing:

- **Tier 1, always present: server-issued anonymous id.** Minted the moment a
  session starts; the actual primary key for every session and score record.
  Requires no login, stores no personal data.
- **Tier 2, client-held: opaque client-supplied key.** The client persists
  tier 1's id and presents it on return. This is not a separate identity
  system, only proof the client already holds a tier 1 id. Losing it loses
  that history, with no recovery path, exactly as an anonymous id implies.
- **Tier 3, optional: linked identity providers** (Google, Apple, Facebook).
  Linking attaches a recovery method to an existing tier 1 id; it never
  creates a new identity. Opt-in, so the product never forces an account
  before first play.

`SessionRepository` keys on the tier 1 id. A future `IdentityLinkRepository`
maps a provider's external id (Google `sub`, Apple `sub`, Facebook user id) to
a tier 1 id. OAuth token verification happens outside the engine entirely, at
whatever layer sits in front of it; the engine never sees provider tokens or
credentials, consistent with the plugin boundary's existing rule that
credentials never cross into anything except the layer that owns them.

## Repository layout

See `docs/handoff.md` for the current phase status. The file tree is:

```
puzzlegen/            the installable package (core, graph, providers,
                      content, engine, games)
content/seeds/        hand-authored and exported content artifacts
tools/                snapshot-build-time scripts (wn, wordfreq,
                      sentence-transformers dependencies live only here)
tests/unit/           one file per module
tests/invariants/     architectural boundary enforcement (AST-based)
tests/support/        test-only game fixtures, never shipped
docs/                 this file, the handoff document, and (from phase 9)
                      new-game.md and content-lifecycle.md
```

## Open items carried into phase 6

- Session engine, scoring interface, share artifact system, accessibility
  infrastructure: not yet built.
- Session identity: decided above, not yet implemented.
- Third-party subprocess RPC transport: deferred to phase 9 by decision. In
  phases 6 to 8, in-tree games run in-process.
- Game 1 (grouping of twenty, hidden fifth group, mid-puzzle axis switch) and
  game 2 (relationship chain with branch points and a minimum-length budget):
  designed in conversation, not yet built. Scheduled for phases 7 and 8.
- `find_intersecting_groups`, `UNIQUE_UP_TO_TOLERANCE`, and
  `UNIQUE_MINIMAL_PATH` exist in the engine specifically for games 1 and 2 and
  are otherwise unexercised by real content.
