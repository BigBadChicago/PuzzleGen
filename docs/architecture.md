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

**The content hash covers every record's timestamps, so a byte-identical
rebuild requires a pinned build time** (`tools/build_snapshot.py --now`);
without it two builds of identical inputs agree on content but not on hash.

## Phase 3: Query, similarity, policy, and the plugin boundary

**The primary-plus-crosscutting storage pattern is a data model, not a game.**
An entity can belong to categories in more than one taxonomy at once: a
primary taxonomy that organizes it (an animal, a plant, a tool) and any number
of secondary taxonomies whose categories cut across the primary one without
moving the entity off its primary shelf. Nothing about this requires a
"puzzle," a "hidden group," or words at all. It is storage: a category is a
row, a membership is an edge, and an entity can have edges into more than one
taxonomy's categories simultaneously. `intersects_taxonomy` on `ContentQuery`
is the query-side expression of this: "find groups in taxonomy A whose members
also touch taxonomy B," with no assumption about what A or B contain.

Concretely, for a number game with no words or wordplay anywhere in it: the
primary taxonomy could be `parity` (`even`, `odd`) or `size` (`single_digit`,
`double_digit`), and a secondary taxonomy `notable_years` could tag `1984`,
`2001`, and `1969` as also belonging to `is_a_year`. A group query against
`parity` with `intersects_taxonomy="notable_years"` returns groups of numbers
that share their parity and also include at least one number somebody would
recognize as a year, the same query shape game 1 uses for words, run against
numbers instead. The storage and the query never change; only what is loaded
into the taxonomies does.

Game 1 (the word grouping game) is the first thing built that reads this
pattern, and it reads it as "four visible piles, plus one hidden pile made of
one borrowed member from each visible pile." That read, the borrowing rule,
the "hidden" framing, the difficulty scoring, all of it, belongs to game 1's
plugin code (`puzzlegen/games/grouping/`). None of it lives in the storage
layer, and no future game is expected to reuse game 1's specific mechanic
merely because it also uses `intersects_taxonomy`.

**Group search enumerates within categories, not across the pool.** Asking
every combination of the pool whether its members share a category is the
wrong way round: a 144 entity overlay in 15 categories has 480 million
combinations of five and 3,780 that share anything, so a 20,000 combination
budget reached none of them, returned empty, and reported the emptiness as a
property of the content. Grouping by category first makes the work
proportional to the answers.

**Membership edges are read once per service, not once per entity.** One
indexed lookup costs about 7 ms, which is nothing until a pool is 14,720
entities and the same query asks 14,720 times. A snapshot is sealed and a
service is per request, so the cache cannot go stale under itself.

**`NO_SHARED_CATEGORY` is a separate rejection reason from
`SEMANTIC_DISTANCE_TOO_HIGH`.** The two need opposite fixes, one a looser
threshold and the other different content, and sharing a code sent a reader
to the wrong knob.


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

Within puzzlegen/engine/, the session layer added in phase 6 is: sessions.py
(records, Clock and DayWindow), session_storage.py (six repositories),
identity.py, scoring.py, session_service.py, sharing.py, accessibility.py.
```

## Phase 6: Sessions, scoring, sharing and accessibility

Phases 1 to 5 end at a published manifest. Phase 6 starts there and ends at a
shareable result, and the seam is deliberately thin: the session layer reads
`PuzzleRecord` and `PuzzleManifest` and writes nothing back to either. A
published day stays exactly as published however many people play it. No
module under `graph/`, `providers/` or `content/` changed in this phase, which
is the first evidence the layering held: adding play required no change to how
knowledge is stored, governed or queried.

**Everything lands under `puzzlegen/engine/`.** A new top-level layer would
mean editing `LAYERS` in the invariant test, and a session record is engine
output exactly like a puzzle, a manifest or a trace. Seven new modules:
`sessions.py` (records and injected policies), `session_storage.py` (six
repositories), `identity.py`, `scoring.py`, `session_service.py`, `sharing.py`
and `accessibility.py`.

### Identity

**The tier 1 id and the tier 2 key are two different strings.** `player_id` is
the primary key on every session, score and streak record, safe to log and to
place in a record. `return_key` is a bearer credential the client holds.
Collapsing them, which the shorthand "the client presents its id" invites,
would make every debug dump an account takeover.

**Return keys are stored only as `hmac_sha256(pepper, key)`.** A dump of the
store cannot impersonate anybody. The pepper arrives in an injected
`SessionSecrets`; no module in the package reads process environment.

**`DeterministicRng` is never used for identity.** Reproducibility is its
entire purpose and is precisely the property a credential must not have.
Identity material comes from an injected `SecretSource`, and an invariant test
asserts `identity.py` imports no `rng` module.

**A player holds several live return keys, one per device**, each labelled,
capped at ten, revoked rather than deleted. No rotate-on-use: it would strand
the second device and any client whose reply was lost. A deleted key also
leaves no evidence a device was ever trusted, and the question after a
suspected leak is which devices existed.

**One provider subject belongs to one player forever.** Relinking a subject
already bound elsewhere raises `MergeRequired` rather than repointing, because
the common cause is one person who played anonymously on two devices and both
histories are real. Merge is a product decision nobody has made; picking a
winner silently discards somebody's play.

**The engine never sees a token.** `VerifiedProviderAssertion` is named for
what the caller is promising, carries provider, subject, audience and
verification time, and nothing else. An invariant test asserts no
credential-shaped name (`access_token`, `client_secret`, `password` and the
rest) appears anywhere in `puzzlegen/`.

**`forget_player` exists now rather than later.** A store of play history needs
a real erasure path; building one after two games ship means discovering the
collection that was missed by finding a stray record. Published manifests are
untouched: they belong to the day, not to any player.

### Sessions

**One session per player per manifest per kind**, with the id minted from
those three parts, so a client retrying `start` after a dropped reply resumes
rather than opening a second history.

**Kind is decided by the engine, never requested.** A manifest for today is
`LIVE`; any other day is `PRACTICE`. A client that could choose would
eventually choose wrong, and `LIVE` is the flag that admits a score into a
ranking. Practice sessions are recorded in full and enter no score, no streak
and no share; `SessionRepository.completed_days` is the one filter that has to
be right, because every streak answer is built on it. A practice window runs
one day-length from when it starts, since the day it belongs to is long past.

**Moves are an append-only ledger stored inline on the session document.** A
move and the counters derived from it can never be half written. The 500 move
cap is what keeps the document bounded.

**A repeated sequence is idempotent when its payload matches and a conflict
when it does not; a gap is refused.** That is what makes a retry safe on a bad
connection while leaving a replayed or forged move detectable.

**Game state is rebuilt by replaying the ledger, never cached.** A stored state
and a stored ledger can disagree and there is no way to tell which is right.
Replay also doubles as a purity check: a regrade that flips a recorded outcome
raises `DeterminismError`, which catches a grader reading the clock or a
random source. The cost is O(moves) per submission, bounded by the cap.

**Grading belongs to the game and runs on the server.** Only the game knows
what a partially correct answer is, so `grade_move` was added to the plugin
protocol; the client is never given the solution, so it cannot grade. Plugin
exceptions fail the move, never the process, mirroring phase 4's rule about
one game's bug not taking down the day.

**Telemetry is derived, never submitted.** Every number a scoring function
reads is computed from the ledger and from timestamps the engine wrote. Client
timestamps are stored on the move as advisory and feed nothing that ranks.
`elapsed_ms` is start to last activity, not start to now, or an abandoned tab
accumulates hours of play that a share would then publish.

**`last_activity_at` may not precede the last move.** Found by test: a session
whose activity clock disagrees with its own ledger reports a shorter elapsed
time than the play actually took, while passing every other check.

**Hints occupy a sequence number like any other move**, so a client cannot
under-report its hint count by declining to mention it. Giving up is its own
move kind rather than a plain abandon: "stopped playing" and "asked to see the
answer" are different facts and only one is a decision the player made.

**Day boundaries come from an injected `DayWindow`.** `UtcDayWindow` takes a
grace period; `OffsetDayWindow` uses a fixed offset rather than a named zone,
because a named zone moves the boundary twice a year and would produce a
23 or 25 hour day, which breaks the consecutive-day streak rule. Expiry is
applied lazily on read, because the engine owns no scheduler and a session
nobody looks at again never needed the write. An expired session settles like
any other ending: a zero score is still a fact about the day.

**`Clock` is injected everywhere.** `SystemClock` is the single sanctioned
reader of the real clock, exempted in the invariant test by class name rather
than by file, so a second reader added beside it is still caught.

### Scoring

**A score is a pure function of the puzzle and the ordered moves.** The stored
`ScoreRecord` is a cache of that function and carries the hash of the ledger
that produced it. `recompute` rebuilds it; a disagreement raises rather than
writes, because the stored number is what a player already saw.

**A score is written once and frozen**, enforced in `ScoreRepository.put`.

**Ranking is within one game.** Points, then elapsed, mistakes, hints and
attempts as an explicit tiebreaker tuple, ordered by how directly each reflects
play. No cross-game normalisation, for the reason phase 5 gave about
difficulty: one scale across games would be comparable and wrong. Rank is
computed on read, never stored, because a rank is a fact about a set that
changes all day.

**Efficiency is measured only when a game declares `optimal_attempts` in its
presentation**, and is 1.0 otherwise. An invented optimum would quietly
penalise every player of that game.

**Move payloads never reach a scoring or share function.** `move_events`
exposes sequence, kind, outcome and an offset in milliseconds; a guess names
parts of the answer, and a share grid only ever needs the shape of the attempt.

**Streaks have no quiet exceptions.** A completed live session on the day after
the last advances; anything else resets. No freezes and no grace days, because
a rule that quietly forgives a gap cannot be tightened later without rewriting
history. A back-dated completion triggers a full rebuild from
`completed_days`, since the session history is the authority and the streak
record is a cache like a score is. `streak_is_live` answers the display
question before any reset is written.

### Sharing

**The engine renders all share text from the game's semantic tokens.** A game
emits token names and never characters, as `ShareArtifact`'s own phase 4
docstring already promised.

**A game declares its vocabulary in `GameDescriptor.share_tokens`**, each token
carrying a glyph, one ASCII character and a spoken label. An undeclared token
is a protocol error (`share.undeclared_token`), matching phase 5's treatment of
self-contradictory verifier output as a bug rather than a content rejection.

**Three renderings are mandatory.** `text`, `text_plain` and `alt_text`;
construction fails without all three. A share with only glyphs excludes the
players who most need the alternative, and a reader announcing emoji by their
Unicode names is not an alternative.

**Everything a player posts is written by the engine.** The game's `headline`
is dropped: it is free text on a public surface with no way to check intent.
The counts line comes from the ledger, so a game cannot publish a flattering
attempt count beside a real score.

**`ShareRedactor` checks the finished text rather than the intent behind it**:
a codepoint allowlist built from the declaration, no record id, no long hex
run, no session, player, manifest or puzzle id, and caps on length, lines and
tokens per row. This is `public_view()` applied to a public surface, and it
exists because phase 4's leak was found by test rather than by inspection.

**The spoiler check runs over game-supplied text only.** The header, counts
line and alt sentence are engine-written from a vocabulary the engine controls,
so checking them against the puzzle's own words produces only false positives:
a puzzle whose prompt used "tries" would make the counts line unwritable. What
does get checked is the display name and the outcome string, against every
word of four or more characters in the payload and solution, minus a stopword
list.

**Practice sessions produce no share.** A share says "here is how I did on
today's puzzle", and a replay of a past day is not that.

### Accessibility

**Accessibility is a data contract, not styling.** The engine renders nothing;
it holds the declaration, checks it, and reports data the shell interprets.

**`AccessibilityDeclaration.is_complete()` is unchanged from phase 4** and
remains the game's own self-description. The stronger gate is
`accessibility.validate_game`, called at session start: the keyboard model must
be one the shell installs, targets must meet the 44px minimum, announcement
templates must resolve against the placeholders the engine actually supplies,
and a game claiming colour independence must back it with state symbols. That
declaration had existed since phase 4 and was enforced nowhere.

**State symbols are validated for distinct name, symbol and label.** Two states
sharing a symbol are identical to a player reading shapes; two sharing a label
are identical to one hearing text. `state_symbols` defaults to empty so a
phase 4 game still describes itself and still generates, and is refused at
session start so an unplayable promise stays unplayable.

**The symbol channel is never stripped for sighted players.** A game rendered
one way for some players and another way for others is a game whose accessible
path is the one nobody tests.

**Announcements are rendered engine side** from the game's templates, with
counters filled from the ledger so an announcement cannot tell a player
something different from what the score will. Politeness is decided by the
engine from a short assertive set, because a reader that interrupts constantly
gets turned off.

**The player's profile is stored against the player, not the device**, so it
follows them, and is copied onto each session at start, so an old rendering
stays reproducible after a later settings change.

### Plugin protocol 1.1.0

`grade_move` and `get_hint` were added, with `MoveJudgement` and `Hint` as
boundary types, both plain frozen dataclasses of JSON-representable values like
everything else that will cross the phase 9 wire. The bump is additive: a 1.0
game still registers and still generates, and what it cannot do is have a
session started against it, which `SessionService` reports by name rather than
as an attribute error mid-play.

### New architecture invariants

Five, each protecting something that fails silently: identity imports no
reproducible generator; no session module reads the clock except `SystemClock`;
no session module reads the environment; no credential-shaped name appears in
the package; no layer above the engine, and no game, holds a session
repository. Four further tests plant each violation and assert the checker
catches it, so the new rules are proved non-vacuous the way phase 3's were.

## Content, review and proposal (decided ahead of phase 7, for phase 7 to implement)

Game 1 is the first game generated against real content rather than test
fixtures, so the content questions had to be settled before any game code
could be written.

### The snapshot game 1 generates against

**A WordNet backbone plus a curated overlay, imported in lemma mode.** The
taxonomy comes from seven `tools/export_wordnet.py` runs, one per domain; the
second axis the game needs comes from a hand-authored and then tool-extended
overlay. WordNet alone cannot supply the hidden group, because the hidden
group is a property that cuts across the hypernym hierarchy rather than
sitting inside it.

**Seven tight domains rather than one broad export.** A deep slice of
`animal.n.01` produces hundreds of entries no player has heard of, and four
groups drawn from three domains produce two groups a player will reasonably
merge. The roots: `carnivore.n.01` and `bird.n.01` at depth 3,
`edible_fruit.n.01` at depth 2, `musical_instrument.n.01`, `vehicle.n.01` at
depth 3, `garment.n.01` and `hand_tool.n.01` at depth 2.

**Lemma identity, not sense.** An entity id minted from the canonical name is
what lets the curated overlay merge onto the WordNet backbone instead of
sitting beside it; sense identity would force the overlay to name exported
sense keys and to be re-authored on every export. The known cost is
polysemy collapse, and it is real: `kiwi` appears in both the bird and the
fruit export. `tools/report_lemma_collisions.py` lists every lemma appearing
in more than one export so colliding entries are excluded deliberately rather
than merged silently.

**Frequency runs before the game, not after.** Without the band table every
candidate is unscored, the frequency gate passes everything, and the first
real board is full of words nobody knows. Embeddings may stay on
`DevHashEmbeddingProvider` through phase 7, which labels itself in snapshot
metadata and raises an import warning, so a development snapshot cannot be
mistaken for a publishable one.

**`tools/build_snapshot.py` drives import, activate and seal.**
`SnapshotBuilder` has existed since phase 2 and nothing outside tests has ever
driven it.

### The overlay and its proposer

**The overlay is the design-critical artifact and it is small.** It carries
only the second axis: categories that cut across every domain, with their
`is_a` relationships. Each overlay category needs at least as many members as
the day's group size, drawn from different taxonomic groups, or the hidden
group is not hidden, it is a fifth pile.

**Overlay membership is proposed by tooling and can never self-activate.**
"Salmon is also a colour" is an interpretation, not something a source said,
so it is JUDGED, and the phase 2 rule holds without exception: the normalizer
routes JUDGED to `PENDING_REVIEW` regardless of confidence and the default
activation policy excludes the class. A proposer may suggest as freely as it
likes precisely because nothing it writes can reach a puzzle on its own.

**The proposer lives in `tools/`, not in the engine.** Proposing membership
means reading names, definitions, aliases and embeddings across the whole
graph and writing candidate records, which is content-layer work with the
heavy dependencies attached. A proposer inside the engine would violate the
layer rule the invariant tests enforce.

**It judges on both string and embedding signals, and records which fired.**
String and gloss matching alone misses anything needing world knowledge;
embeddings alone produce proposals a curator cannot see the reason for.
Recording the firing signal is what lets a curator triage a long queue by
method rather than reading forty unsorted rows.

**The overlay grows, and growth is what creates future groupings.** Candidate
generation queries overlay categories by active member count, so a category
with four active members is invisible to the generator and the same category
with nine is a usable hidden group. Nothing special happens at the crossing
point; the query already expresses it.

### Review

**A review decision is an append-only record, never a status edit.**
`ReviewDecision(subject_ref, reviewer, decision, proposal_batch, at, note)` in
a `ReviewRepository`, filling the `ReviewRepositoryProtocol` declared and
unimplemented since phase 1. Status is derived by counting the ledger, as
activation is derived from `ActivationEvent`. "Which items had I approved on
the 3rd" must give the same answer a year later.

**The threshold maps onto the existing lifecycle with no new statuses.** A
record sits at `PENDING_REVIEW` until it has ten credited accepts, at which
point it derives to `APPROVED`. `APPROVED` is still not usable: only
`SnapshotBuilder.activate()` moves anything to `ACTIVE`, and `USABLE_STATUS`
is `{ACTIVE}` alone. Overlay membership therefore passes two independent
gates, repeated acceptance and explicit activation, and neither substitutes
for the other.

**An accept is credited only when it is both a new proposal batch and a later
day than the last credited one.** With a single curator, ten accepts cannot
mean ten people, so what makes two accepts distinct has to be stated: the item
must survive being re-proposed against regenerated evidence, and it must be
read on a separate occasion. Repeated clicks in one sitting credit once.

**Rejection is asymmetric and immediate.** One reject moves the record to
`REJECTED` with no threshold. The threshold protects against a hasty yes,
which puts wrong content into a published puzzle; a hasty no costs only a
re-proposal, and requiring ten rejections to kill an obviously bad suggestion
would make review tedious enough to stop happening. A rejected subject may be
re-proposed, and the ledger keeps the rejection so the proposer can show it,
or the same bad suggestion arrives every week unrecognised.

**The reviewer is recorded, not authenticated.** The engine cannot verify who
anybody is, and pretending otherwise would be security theatre inside a
library. What enforces single-curator control is that the review tool runs on
the curator's machine against their repository and each decision is a
committed file. What the code contributes is that every decision names its
reviewer, and `Provenance` already refuses to let a JUDGED record go ACTIVE
without one, so an unattributed approval cannot exist even by accident.

**Nothing automatic may write `APPROVED` or `ACTIVE`.** A new invariant test
asserts that no module under `tools/`, and no module outside the review
service and `SnapshotBuilder`, assigns either status.

**The seed overlay bootstraps around the threshold, deliberately.** Ten
credited accepts means at least ten days from proposal to playable, so a
freshly seeded overlay would leave game 1 with nothing to draw on for its
first fortnight. The hand-authored seed file is `CURATED_INTERNAL` rather than
JUDGED and activates on the normal path; only proposer-generated additions
take the ten-accept route. This is the distinction the lifecycle already
draws, between what a person asserted and what a machine inferred, and not a
special case for getting started.

### Game 1 board shape

**Group size is drawn per day by the deterministic RNG, from 5 to 9
inclusive**, across four visible groups, so a board is 20 to 36 tiles. The
hidden fifth group is an overlay category with exactly as many members as the
day's group size, drawn one from each visible group where possible.

Two consequences carry into implementation. Verification cost is driven by
group size, since `enumerate_partitions` over 36 items into groups of 9 is a
far larger search than 20 into groups of 5, so the budget must be sized for
the worst case rather than the average. And difficulty is now partly a
function of board size, so the per-game thresholds must normalise for it or
every 9-item day lands in the hardest band for a reason unrelated to the
puzzle.

**The axis switch is derived, not scheduled.** It fires when the remaining
tiles can no longer be partitioned taxonomically, which is a better puzzle
than a fixed trigger and costs a feasibility check inside `grade_move`. Two
constraints follow: the check is existence-only, reusing `enumerate_partitions`
with a first-solution exit and a budget sized for the negative answer, which
is the expensive direction and the one that fires the switch; and it must stay
a pure function of the puzzle and the moves before it, because the session
layer replays the whole ledger on every submission and raises
`DeterminismError` on any disagreement. Memoising within one replay pass is
fine; memoising across calls is not.

## Open items carried into phase 7

- Game 1 (four groups of 5 to 9, hidden overlay group, derived axis switch):
  designed in conversation and scoped above, not yet built. Scheduled for
  phase 7. It is the first real exercise of `find_intersecting_groups`,
  `UNIQUE_UP_TO_TOLERANCE` and `enumerate_partitions`, and the first game to
  implement plugin protocol 1.1 for real rather than as a test fixture.
- Content, review and proposal tooling, all scoped above and none of it built:
  `tools/propose_overlay.py`, `tools/review.py`,
  `tools/report_lemma_collisions.py`, `tools/build_snapshot.py`,
  `puzzlegen/content/review.py` with `ReviewRepository`, and
  `content/seeds/overlay.curated.json`.
- Five deliberately broken game fixtures, to prove the engine catches what it
  claims to: a verifier omitting `collapsed_solutions`, a verifier claiming
  complete enumeration having examined zero states, an impure `grade_move`, a
  `grade_move` marking a wrong answer correct, and a share builder emitting an
  undeclared token. They ship in phase 7 as failing-by-design fixtures and are
  fixed against real content in phase 8.
- Game 2 (relationship chain with branch points and a minimum-length budget):
  phase 8. First real use of `UNIQUE_MINIMAL_PATH` and `enumerate_paths`.
- Third-party subprocess RPC transport: phase 9 by decision. In phases 7 and 8,
  in-tree games run in-process.
- Account merge across two anonymous histories: deliberately unbuilt.
  `MergeRequired` names the situation and refuses; nothing resolves it yet.
- Hint pricing beyond a game-declared cost, and any ranking wider than one day
  of one game: deliberately unbuilt.
- `ops` and `cli` layers: still empty. The HTTP or serverless surface that
  calls `SessionService` is out of scope until one exists, by the decision
  taken at the start of phase 6.


## Phase 7: Game one

**The overlay seed is authored from the lexicon, not against it.** The first
seed was written before the snapshot existed and 134 of its 144 words were
absent from it, so they could only ever be hidden group members and the
two-axis mechanic worked for ten words. Every member of the second seed was
chosen from the depth 6 exports, and all 144 sit in both a lexical and an
overlay category.

**Exports run at depth 6, not depth 3.** Depth 3 holds taxonomic
abstractions rather than the familiar nouns a word game needs: 194
recognisable single words against 424 at depth 6, with no turtle, no axe and
no motorcycle. Depth is the fix, not more roots.

**The board's group size is derived from the day key, not from the
generation RNG.** Content requirements are resolved before the engine builds
a generation context, so the size has to come from public inputs alone.

**Game 1 declares `SOUND_INCOMPLETE` with a bound rather than `COMPLETE`.**
The widest boards can legitimately exhaust the enumerator, and a game that
declared COMPLETE and returned a partial search would be committing a
protocol violation. A day that overruns is refused publication, which is the
correct outcome.

**The tolerance rule collapses tiles with identical distinguishing
signatures.** Two tiles carrying exactly the same categories are
indistinguishable, so partitions differing only by exchanging them are the
same puzzle. A swap between tiles that differ by even one category survives
as a real second solution and fails the contract.

**The verifier reports the intended grouping as the puzzle's own solution
object.** The engine hashes the whole solution and looks for that hash among
the verifier's, so an equivalent rebuilt from the search hashes differently
and a correct game is rejected.

**Tile category memberships travel in the solution, never the payload.** The
verifier needs them to enumerate the groupings a player could defend; a
client holding them could rank tiles by shared category and read the groups
straight off.

**The axis switch is derived, not scheduled.** After every correct move the
game asks whether the tiles still on the board can be partitioned
taxonomically at all, and switches when they cannot. A budget overrun counts
as "no partition found", because answering "one probably exists" on the
strength of not having looked would switch the axis on some replays and not
others.

**There is no "one away" share token, though there is a state symbol.** A
share is built from the move ledger with payloads stripped, so it can see
that an attempt was wrong but never how wrong.
