# External games: adding a game nobody reviewed

How a game written outside this repository, by somebody the maintainers cannot
code review, should be added to the engine.

Everything numbered in this document was measured against `main` at `87566ab`
in a container built the way `.devcontainer/post-create.sh` builds one, with
the platform and all five games installed and the symbolic snapshot built. The
measurements and how to reproduce each are in the next section, because this
project's recurring failure is a number that no longer matches what the code
produces and a design document is as capable of carrying one as a diagnostic
is.

One thing to state before any of it. The honest statement of the gap in phase 8
is that third-party games run in-process and the import boundary is enforced by
the invariants "rather than by isolation", which is "the same enforcement
in-tree games had, now applied to a wider set". The first half is right. The
second half overstates what the invariants do against an author who is not
cooperating, and the engine has correctness holes that a hostile author reaches
without needing to escape anything. So this document is not only about a
sandbox, and the sandbox is not the first thing to build.

## What was measured

| what | measured |
|---|---|
| whole suite | 3,926 passing, 0 failing |
| whole suite, no snapshot built | 3,896 passing, 30 skipped |
| boards round tripped through JSON | 90 generated, all equal, hash preserved |
| payload on the wire | 416 B to 1,694 B |
| largest `VerificationResult` | 1,861 B |
| `verify`, `odd_number_out` | 0.05 ms over 49 states |
| `verify`, `shape_trio` | 0.31 ms over 84 states |
| `verify`, `digit_lock` | 1.10 ms over 720 states |
| `verify`, `grouping` | 11.82 ms over 5,005 states |
| JSON over pipe, 400 B body | 100.8 us median, 162.9 us p95 |
| JSON over pipe, 2 kB body | 112.4 us median, 165.3 us p95 |
| JSON over pipe, 20 kB body | 352.1 us median, 480.1 us p95 |
| JSON over pipe, 200 kB body | 2,844 us median, 4,297 us p95 |
| worker cold start, bare interpreter | 35.0 ms median |
| worker cold start, platform and one game | 337.6 ms median |
| `grade_move` calls per submission | the submission number: 1, 10, 50, 100, 250, 500 |
| `grade_move` calls per maximal session | 125,250 |
| 200,000 candidates offered | all consumed, 63.8 MiB resident growth, run succeeded |
| `Puzzle` payload ceiling | none; 8 MiB accepted |
| RNG from a seed and a label | reproduced byte for byte |

How each was taken. The suite figures are `scripts/test-all.sh`, with and
without `bash docs/build-symbolic-snapshot.sh` having run. The round trip is 30
days per symbolic game through `GenerationPipeline.generate` against the
symbolic snapshot, plus both lexical games' own fixtures, every `payload`,
`solution` and `presentation` compared for equality and for `stable_hash`. The
`verify` and hop figures are medians of repeated calls, 300 round trips per
size, parent and child both CPython 3.13. The cold starts are 10 spawns each,
timed to first reply. The call counts come from a counting proxy around
`replay_state`. The candidate flood and the payload ceiling were run through the
real pipeline and the real type.

Two reproduction notes. The hop and cold start figures are this container's and
will differ on other hardware; what matters is their ratio to the work they
wrap, which is measured here too. And the symbolic snapshot that the three
newer games read builds in seconds with no network, no model and no lexicon:
`bash docs/build-symbolic-snapshot.sh`.

## 1. Isolation: what actually enforces the boundary

### What enforces it today

Four invariants in `tests/invariants/test_architecture.py` under
`TestPluginBoundary`: a game may import only the six names in
`ALLOWED_FOR_GAMES`, may not import anything in `FORBIDDEN_FOR_GAMES`, and may
not call `open`, `eval`, `exec`, `compile` or `__import__`.

Against an author who is cooperating, these are a good code review aid. Against
one who is not, they are close to vacuous, and this is measured rather than
argued. Three holes, each confirmed by running the repository's own checker
over planted code:

**The banned call scan matches only `ast.Name` calls.** A module whose only
imports are `builtins` and `puzzlegen.content.port`, and which calls
`builtins.open("/etc/passwd")`, `builtins.__import__(name)` and
`getattr(builtins, "ev" + "al")(src)`, produces zero violations from all four
tests. `builtins` is not in `FORBIDDEN_FOR_GAMES`, and an attribute call is not
a name call.

**Attribute access needs no import at all.** The same module reaches
`port._service`, and from there the `ContentService`, its `GraphRepositories`
and the store, without importing any forbidden name. The port is handed to the
plugin as a live object, so every capability behind it is one attribute away.

**Only the entry point's own top level package is scanned.** `game_packages()`
resolves `entry.value.split(":", 1)[0].split(".", 1)[0]` and walks that
package. A distribution with two top level packages has its second one
unscanned. I built and installed one: `tp_game`, clean, is the entry point;
`tp_helper`, in the same distribution, imports `os`, `socket`, `subprocess`,
`pathlib` and `puzzlegen.graph.repositories` and calls `open("/etc/passwd")`.
With it installed, all seven boundary and peer tests pass and the scan reports
the five first party packages plus the clean decoy.

None of this is a criticism of the invariants, which were written for in-tree
code and do that job. It is the reason "the same enforcement in-tree games had"
is not the right description once the author is outside the repository: for
in-tree code the real enforcement is the pull request, and the invariant is a
backstop against an honest refactor. Remove the pull request and the backstop
is carrying weight it was not built for.

### The options

Four ways to make the boundary real for code nobody reviewed.

**A. Subprocess with a wire protocol.** The plugin runs in its own operating
system process. The engine speaks the protocol `PLUGIN_PROTOCOL_VERSION`
already names over a pipe, and the process is started with reduced privileges:
a separate user, no network namespace, a read only filesystem view, `setrlimit`
on address space and CPU, and a seccomp profile if the host supports one. The
plugin cannot import the platform at all, because the platform is not in its
process.

*Benefits.* It is the only option here that needs no new language, no new
toolchain and no change to how a game is written: the five existing games are
already pure Python depending on nothing but `puzzlegen`, and they already
communicate in types designed to be JSON. The isolation is enforced by the
kernel rather than by a parser, so it does not have holes of the kind measured
above. A crashing or looping plugin is killed without touching the engine, and
the existing promise that a failing game does not take the day down becomes
true of resource exhaustion as well as of exceptions. It also makes the
resource bounds in section 4 straightforward, because a process is the unit the
operating system knows how to bound.

*Costs.* 337.6 ms median to start a worker that has imported the platform and
one game, which is the dominant new cost and is per worker rather than per
generation if workers are pooled. A per-call hop of 112.4 us at the sizes these
games actually use, which is 1% of `grouping`'s verify and 2.2 times
`odd_number_out`'s, so it is noise for one game and the larger part of the work
for another. A protocol to specify, version and test, and two new failure modes
to handle: a worker that dies mid-call and a worker that stops answering. The
`grade_move` replay pattern has to be batched or play becomes unusable: 125,250
calls over a maximal session is 14.0 s of pure transport at per-call hops.
Sandboxing details are host specific, so a Linux container and a developer's
machine will not enforce the same thing, which is a correctness difference
between CI and production unless the weaker configuration is the tested one.

**B. WebAssembly.** The plugin is compiled to WASM and run in an embedded
runtime with an explicit import list. Nothing is reachable that the host does
not hand in.

*Benefits.* The strongest isolation of the four and the only one that is the
same on every host, because the sandbox is the runtime rather than the
operating system. Startup is far cheaper than a process, so the 337.6 ms is
not paid. Memory is bounded by the module's linear memory, which is a hard
number rather than an `setrlimit` guess. Determinism is also better: a WASM
module has no clock, no entropy source and no filesystem unless one is
imported, which closes by construction the ways a plugin could become
nondeterministic.

*Costs.* It changes what a game is. The five existing games are Python, and
Python in WASM means shipping a CPython or Pyodide build inside the sandbox,
which gives back most of the startup saving and adds a large dependency the
platform would then own. The alternative is that external games are written in
a language that targets WASM directly, which makes the external authoring
experience different from the first party one, and the first party games become
the only ones exercising the Python path. The protocol surface also has to
cross a C ABI rather than a pipe, so every boundary type needs a
representation decision that a JSON pipe does not need. This is the right
answer for a plugin ecosystem of hundreds; it is a large bet to place on the
first external author.

**C. A separate service.** The plugin runs as its own network service, and the
engine calls it over HTTP or gRPC.

*Benefits.* Isolation is total and operational rather than clever: the service
runs wherever its author runs it, under their own account, and the engine holds
only a URL. It scales independently, and a plugin in any language is a plugin.
It is also the only option where the plugin's author bears the cost of running
it, which aligns the incentive against unbounded work.

*Costs.* It is the worst fit for this engine's actual promise. A puzzle must
regenerate identically years later from the day key, the game id and the
snapshot hash, and a remote service is not reproducible: it can change its own
code between the original generation and the replay, and nothing in the
manifest would record that it did. Latency goes from 112 us to a network round
trip, and the `grade_move` replay pattern becomes impossible rather than
merely expensive. It adds authentication, availability and a new class of
outage to a system that currently has none. And generation becomes dependent on
a third party being up at the time a day is built.

**D. Review only, signed, no isolation.** External games are not run unreviewed
at all. A distribution is accepted only after a maintainer has read it, and what
is installed is cryptographically bound to what was read: the author signs the
distribution, the engine verifies the signature against a pinned key before
`discover()` registers anything, and a signature that does not verify is a
`ConfigurationError` naming the distribution.

*Benefits.* It costs almost nothing to build, and it is the only option that
addresses the holes measured above, because the holes are all failures of
automated review and this replaces automated review with human review. It
leaves the five first party games completely untouched: no protocol, no hop, no
cold start, no new failure mode. It keeps reproducibility exactly as it is,
because the plugin is still an in-process Python call. And it is honest about
what the project currently is: five games, all first party, and no external
author yet in existence.

*Costs.* It does not scale. Every new game is a maintainer's reading, which is
the bottleneck the entry point mechanism was built to remove. It is not a
security boundary at all once a reviewer misses something, and a reviewer
reading for puzzle quality will not reliably catch a subtle capability escape.
It gives the project no answer for an author it does not want to review, which
is the question that was actually asked. And signing is easy to do badly:
without a revocation path, a compromised key is permanent.

### Weighing, and the recommendation

The four are not really alternatives on one axis. B and C buy isolation by
changing what a game is, A buys it by changing how a game is called, and D buys
it by declining to run unreviewed code.

Two measurements decide the order. First, the holes in section 1 mean that
today a hostile game needs no sandbox escape: it reaches the store through an
attribute walk on an object it was handed. Second, section 6 shows it does not
even need that to do the damage that matters, because it can publish a puzzle
with a fabricated answer through the engine's own accepted gates. Isolation
contains the expensive attack and leaves the cheap one untouched. Building A
first would be building the stronger wall around the unlocked door.

**Recommended: D now, A staged behind a named trigger, B reconsidered only if
the trigger fires at scale, C rejected.**

Concretely. Adopt signature verification and review as the policy for the next
few external games, because at the current count a maintainer's reading is both
affordable and strictly better than anything automated. Build A when the first
author arrives whom the project does not want to review, which is a decision
someone makes rather than a threshold that is crossed silently. Specify the
protocol now, because specifying it is cheap and it is what keeps the boundary
types honest while five more games are written; implementing the transport is
the expensive half and can wait. C is rejected on reproducibility, which is not
a cost to be traded but a promise the manifest exists to keep.

B stays open rather than rejected. If external games become a real ecosystem,
its advantages are decisive and its cost is a one time investment; the reason
not to start there is that the first external author does not justify making
the first party games second class citizens in their own engine.

## 2. The protocol as a wire format

Every type in `plugin.py` was deliberately made JSON-representable for this,
and the docstring says so. Measured: it is almost entirely true, and the two
exceptions are both in the inputs rather than the outputs.

### What holds

All five games' real boards round trip exactly. I generated 30 days each for
`shape_trio`, `odd_number_out` and `digit_lock` against the symbolic snapshot
through the engine's own pipeline, took `grouping`'s and `missing_letters`'
boards from their own fixtures, and round tripped every `payload`, `solution`
and `presentation`: equal after the trip in every case, and `stable_hash`
preserved in every case. The hash matters more than the equality, because the
verifier's intended-solution comparison and the session service's payload
comparison are both hash comparisons, so preserving the hash is what makes a
process hop invisible to them.

Sizes are small: payloads 416 B to 1,694 B, solutions 354 B to 1,614 B, the
largest `VerificationResult` 1,861 B. Nothing here is near a size where
serialisation cost would matter.

`canonical_json` normalises a tuple and a list to the same text, so a game that
emits tuples and receives lists back computes the same hash. Phase 9's new
`ContentQuery` fields are `float | None` and a `StrEnum`, both clean.

### What does not hold

**`GenerationContext.rng` is a `DeterministicRng`.** It is a live object
holding an HMAC key and a counter, and it has no JSON form. This is the one
genuine blocker in the type set, and it is fixable rather than fundamental:
`derive_seed` is public and reproducible by design, and `derive` produces a
child seed from which an identical stream can be rebuilt. Measured: shipping
the derived substream's seed as 64 hex characters plus its label string
reconstructs the stream byte for byte over eight draws. So the protocol ships a
seed and a label where the in-process call passes an object, and the plugin
constructs its own `DeterministicRng`. Section 3 covers what this does to the
`consumed` counter.

**`ContentQuery.exclude_ids` is a `frozenset[str]`.** `json.dumps` refuses it
outright. `canonical_json` already handles it, by emitting a sorted list, so
the fix is to use the canonicaliser rather than bare `json`, which is the right
answer for a second reason below.

**Three `Mapping[str, Any]` fields are unconstrained by construction**, and
have to be: `Puzzle.payload`, `Puzzle.solution` and the state and presentation
maps are opaque to the engine on purpose. The protocol cannot type them, so it
has to validate them, and the validation is exactly `canonical_json`'s
normaliser: string keys, finite floats, no arbitrary objects.

**The codec must be `canonical_json`'s normaliser, not `json.dumps`.** Measured:
`json.dumps({1: "a"})` succeeds and returns `{"1": "a"}`, silently coercing an
integer key to a string, so the payload that comes back is not the payload that
went out. `canonical_json` rejects the same value with "non-string mapping
key". A naive codec would therefore be looser than the engine's own hashing and
would corrupt a payload rather than refuse it. Using the canonicaliser as the
wire codec also means a serialisation failure happens at the boundary, with the
offending key named, instead of later at hashing time.

## 3. Determinism and reproducibility

The engine's promise is that a puzzle regenerates identically years later from
the day key, the game id, the seed salt and the snapshot content hash, and that
a session is a pure function of its move ledger, with `DeterminismError` raised
on any disagreement. Out of process, three things change.

**The RNG becomes the plugin's own object rather than the engine's.** Today the
engine constructs every substream and hands it over, so a plugin cannot use a
different source without the engine's own `consumed` counter showing it. Out of
process the plugin constructs the stream from a seed, so it could ignore the
seed entirely and use `random` or the clock. The protocol cannot prevent that,
but it can make it visible: the plugin reports `consumed` with its result, and
the engine records it in the trace as it does now. A plugin that reports a
plausible count while drawing from elsewhere is undetectable by the protocol
and detectable by regeneration, which is section 7's staged rollout.

**Regeneration becomes a cross version question.** Today a replay runs the
installed game's code. Out of process it runs whatever version of the worker is
installed, and the manifest already records `game_version` and
`protocol_version` in `VersionSet`, which is what makes a mismatch diagnosable.
What is missing is a reason to trust that the same `game_version` means the
same code, and that is what signing in section 7 supplies: a version plus a
signature over the distribution identifies the bytes, where a version alone
identifies an author's intention.

**Nondeterminism becomes easier to introduce and no harder to catch.** A
subprocess has its own clock, its own `random` seeding and its own environment,
and the import invariants that currently discourage reading them do not apply
to code in another process. This is an argument for option B over option A,
because a WASM module has no clock and no entropy unless one is imported. For
option A the mitigation is the one the engine already has: regenerate and
compare. `scripts/ci.sh symbolic` already rebuilds the snapshot and
regenerates three committed thirty day runs and fails if any moved, in seconds,
with no network and no model. That mechanism is exactly the right one for an
external game, and it is the strongest determinism control available here
because it tests the output rather than the author's promises.

One thing does not change. Grading stays a pure function of the puzzle and the
prior moves, `replay_state` still replays the ledger, and `DeterminismError`
still fires on a regrade disagreement. The `DriftingGame` fixture that keeps a
counter across calls is caught identically in or out of process, because the
check is on the outputs.

## 4. Resource budgets

`PortBudget` bounds queries at 64, results per query at 200 and total results
at 4,000. `MAX_CANDIDATES` is 512, `MAX_ASSEMBLY_ATTEMPTS` 64,
`ON_TARGET_ATTEMPTS` 8. `MAX_MOVES` is 500, a move payload is capped at 32 keys
and 4,096 characters, and the share layer caps text at 1,200 characters, 24
lines and 32 tokens per row.

What remains unbounded for untrusted code, measured.

**Wall clock time: nothing.** There is no timeout, no `signal` use and no
`setrlimit` anywhere in `puzzlegen/engine` or `puzzlegen/content`. A plugin that
loops in `generate_candidates` or `verify` consumes the generation run
indefinitely. The existing promise that "a crashing third-party game fails its
own puzzle" holds for an exception and not for a hang, because `except
Exception` catches the first and cannot see the second.

**Memory: nothing.** No address space limit, and nothing bounds the size of any
value a plugin returns.

**Candidate output: bounded after the fact, not during.** The pipeline does
`list(plugin.generate_candidates(context))` and only then truncates to
`MAX_CANDIDATES`. Measured: a plugin yielding 200,000 candidates had all
200,000 materialised, the trace recorded `candidates_offered=200000`, resident
memory grew 63.8 MiB, and the run succeeded. That is about 335 bytes per
candidate, so a plugin yielding thirty million exhausts a container before the
cap it is nominally subject to ever applies. The cap is on screening, not on
consumption.

**Puzzle and presentation size: nothing.** `Puzzle.__post_init__` requires a
non-empty payload and at least one fact ref and checks no size. Measured: an
8 MiB payload is accepted. `PresentationModel.elements`,
`VerificationResult.solutions` and `Hint.text` are likewise uncapped.

**Recursion: the interpreter's default only.** A `RecursionError` is an
`Exception` and is caught, so a Python level recursion fails the puzzle
correctly; a deep recursion through C code can still end the process.

The share layer is the exception to all of this and is the model for the rest.
It holds a closed token vocabulary checked against the descriptor, three hard
caps, a codepoint allowlist and a leak detector that looks for record ids and
hex runs. It is the one place in the boundary written as though the plugin were
hostile, and every bound it applies is the kind the rest of the boundary lacks.

Out of process, most of this becomes free rather than fixed: a process is what
`setrlimit` bounds, a pipe read is what a timeout applies to, and a worker that
exceeds either is killed. That is the strongest practical argument for option A
over option D, and it is why option D should carry explicit caps as code rather
than relying on review to notice their absence.

## 5. Content

An external game may want content the graph does not hold. Three facts decide
what it may do about it.

`puzzlegen/content/review.py` is the only module permitted to write
`APPROVED`, `puzzlegen/content/snapshots.py` the only one permitted to write
`ACTIVE`, and `TestReviewAuthority` enforces both, enforces that neither writes
the other's status, and enforces that no tool writes either. A game cannot
import `review` at all. So no external code gets write authority over what is
usable in a puzzle, and nothing proposed here changes that.

Category identity already includes the taxonomy, and
`tools/build_snapshot.py --curated PATH:TAXONOMY` imports a curated file under
its own taxonomy name with `wordnet` and `overlay` reserved. A game's content
therefore cannot merge with another game's by construction rather than by
policy. The precedent exists in tree twice, as
`games/grouping/content/overlay.curated.json` and
`games/letters/content/letters.curated.json`.

Phase 9 added the part that changes the answer. `providers/generated.py` holds
providers whose content is a declaration rather than a file, and
`--generated kind:declaration:taxonomy` imports one. The whole symbolic
snapshot that three games read is three such declarations: 181 entities, 1,758
facts, 40 categories, 2,672 records activated, built in seconds with no
network, no model and no lexicon, reproducible byte for byte.

So an external game can bring content, in one of two forms, and the gate is
different for each.

**As a declaration.** A few lines naming a generator kind, its arguments and a
taxonomy. This is the better unit of trust by a wide margin: it is reviewable
in its entirety in a minute, it is reproducible by definition because the
provider is a pure function of its arguments, and the version string is the
declaration, so a widened range is a different source rather than the same
source changed. A reviewer can read the declaration and run the build. What a
reviewer cannot do is review a generator the external author wrote, so the
generator kinds have to be platform owned, and an external game picks from
them rather than supplying one. That is a real limit: a game wanting a content
shape no existing generator produces needs a platform change.

**As a curated file.** Data in the existing schema, under its own taxonomy.
Reviewable, but a large one is thousands of lines that a reviewer skims rather
than reads, which is how a bad fact gets in. The mitigations are the ones the
platform already has: the file enters as records explicitly not usable,
activation applies a stated policy, and nothing reaches a puzzle without
`ACTIVE`. The gate is therefore the review threshold and the activation
policy, not the file.

Either way activation stays a governed step performed by a maintainer, and an
external game's content is proposed rather than installed. The thing worth
deciding is whether an external game is allowed a curated file at all, or only
a declaration. Declarations only is the tighter rule and the one I would start
with, because it makes the content question a review of a few lines instead of
a review of a dataset, and because it is the rule that can be relaxed later
without anything having to be withdrawn.

## 6. The promises the engine checks, and whether they survive a hostile author

The engine's design here is better than its reputation in the phase 8 passage
suggests, and it is worth saying what works before what does not. The
uniqueness contract is decided by the engine from the verifier's reported
count, never from the game's opinion of it. An incomplete enumeration satisfies
no uniqueness contract at all, so declaring `SOUND_INCOMPLETE` is permission to
degrade and fail rather than permission to publish something weaker; three of
the five games declare it and all three must actually return `COMPLETE` to
publish. `UNIQUE_UP_TO_TOLERANCE` and `UNIQUE_MINIMAL_PATH` must report
evidence a count cannot carry. Fabricated dependency ids are caught against the
port's ledger, before assembly and again after. Assembly may narrow a
candidate's references but never widen them. The share vocabulary is closed and
the share text goes through a codepoint allowlist. Five deliberately broken
games ship as fixtures so the gates have something to have rejected.

The five descriptors as installed:

| game | contract | declared completeness | search bound |
|---|---|---|---|
| `shape_trio` | `EXACTLY_ONE` | `COMPLETE` | 400 |
| `odd_number_out` | `EXACTLY_ONE` | `SOUND_INCOMPLETE` | 2,000 |
| `digit_lock` | `UNIQUE_DEDUCTION` | `COMPLETE` | 6,000 |
| `missing_letters` | `NO_ALTERNATE_INTERPRETATION` | `SOUND_INCOMPLETE` | 5,000 |
| `grouping` | `UNIQUE_UP_TO_TOLERANCE` | `SOUND_INCOMPLETE` | 2,000,000 |

### Two promises that do not survive, measured

**The intended-solution check is optional at the plugin's discretion.** The
verifier compares `stable_hash` of the puzzle's stated solution against the
hashes of what the solver found, and returns early without comparing when the
plugin sets `solutions_truncated`. Phase 5 records the skip in a parenthesis;
what it does not say is that the plugin chooses when the skip applies.

Measured against `shape_trio`, which is the strictest combination in the
repository, `EXACTLY_ONE` with `COMPLETE` verification and a 400 state bound.
I took a real generated board, replaced its stated trio with three tile ids
that are not on the board, and returned `solutions=()` with
`solutions_truncated=True`, `solution_count=1` and `COMPLETE`. The engine's
verdict was `accepted=True`, `reason=None`, empty detail. `consistency_problem`
does not catch it either: it rejects truncation that lists every solution,
and zero listed against a count of one is not that.

This is the hole that matters most, because it needs no sandbox escape, no
import, and no capability the plugin was not given. It produces a puzzle that
verifies, satisfies its contract, publishes, and marks a correct player wrong,
which phase 5 itself calls the single most dangerous failure mode.

**`states_examined` is a bare claim, and `search_bound` never constrains a
published puzzle.** `_completeness_problem` returns immediately when the result
says `COMPLETE`, so the declared bound is only consulted on the
`SOUND_INCOMPLETE` path, which cannot publish because no contract is satisfied
there. On the publishable path the only check on the state count is
`consistency_problem`'s, which fires on exactly zero. Measured:
`states_examined=1` with `COMPLETE` and one solution, against a board whose
honest verify examines 84 states, is accepted.

The practical consequence is narrower than it first looks, because a lie about
the state count alone does not change what is published; the count is
telemetry. It matters because it is the evidence the engine would need if it
ever wanted to check that a claimed complete enumeration was plausible, and
because the search bound in every descriptor currently reads as a constraint
and is not one.

### Two promises taken on trust by design, and whether that is still right

**Grading correctness.** `grade_move` is the game's authority and the engine
makes no comparison between a judgement and the verified solution. The
`GenerousGame` fixture marks a wrong answer correct, and the architecture is
explicit that only a test submitting a known wrong group catches it. For a
first party game that is fine: the test exists and the author wrote both. For
an external game it means a plugin can mark everything correct, or nothing, and
the engine will compute a score, build a share and close the session without
noticing.

This one I would leave as it is, and say why. The engine cannot check grading
in general, because partial correctness is exactly the thing only the game
knows: "three of four right" is not derivable from a solution hash. What it
can do is check the terminal case, which is derivable: a judgement with
`solved=True` implies the puzzle is finished correctly, and whether the
replayed final state corresponds to the verified solution is a question the
game could be asked to answer in a checkable form. That is a protocol addition
rather than a bug fix, and it belongs in the protocol specification rather than
in a patch.

**The accessibility declaration.** `validate_declaration` checks internal
consistency and vocabulary: a complete declaration, a known keyboard model, a
target size at or above the minimum, at least one state symbol, and
announcement templates using only placeholders the engine supplies. It cannot
check whether `colour_independent` and `keyboard_complete` are true, because
they are claims about a client that does not exist in this repository. The
architecture is explicit that the declaration is "declared rather than
inferred", and the distinctness rules on `StateSymbol` are a real check on the
part that can be checked.

For an external game this stays a declaration, and the honest framing is that
it is a published commitment rather than a verified property: the engine records
what the game promised, so a failure is attributable. That is worth stating
rather than fixing, because the alternative is for the platform to own a client,
which is out of scope by the decision taken at the start of phase 6.

## 7. Distribution and trust

**Protocol versioning already works and needs one addition.** `SemVer` compares
on major only, deliberately symmetric in minor, and the registry refuses a
plugin whose major differs, naming both versions. `PLUGIN_PROTOCOL_VERSION` is
1.1.0 and the comment records what 1.1.0 added and what a 1.0 plugin can and
cannot do. The addition needed is a minimum: an external plugin declaring 1.0
loads and generates and then fails when a session starts, which the session
service reports by name. For an external game that failure arrives in front of
a player rather than in front of a maintainer, so the registry should refuse at
`discover()` any plugin whose minor is below the minimum needed for play, as a
`ConfigurationError` naming the distribution, which is the shape `discover()`
already uses for everything else it refuses.

**Signing is the mechanism that makes review mean something.** A review binds a
maintainer's judgement to a set of bytes. Without a signature, what is
installed later is bound only to a name and a version number, both of which the
author controls. So: the author signs the distribution, the engine verifies
against a key pinned in configuration before `discover()` registers anything,
and a failed verification is a `ConfigurationError` naming the distribution. The
pinned key list needs a revocation entry from the start, because a key list
without one makes a compromise permanent.

This stays outside the platform's own code in one respect worth being careful
about. The platform holds no list of games and names none, enforced by
`TestThePlatformNamesNoGame`. A pinned key list is not a list of games, because
it names authors and not game ids, so the invariant survives. A pinned list of
distribution names would not, and should not be the mechanism.

**Staged rollout is where determinism gets tested rather than promised.** The
engine already has the mechanism and uses it on itself: `scripts/ci.sh symbolic`
rebuilds the snapshot, regenerates three committed thirty day runs, and fails
if any moved. An external game should be required to ship the same artifact, a
committed run of thirty days against a named snapshot, and the platform's CI
should regenerate it. That is a much stronger admission test than any static
check, because it tests the output: a game that reads the clock, uses `random`,
or depends on dictionary ordering fails it, and a game whose verifier lies
about its solutions is caught by the thirtieth board rather than the first. It
is affordable for exactly the reason phase 9 records: the symbolic snapshot
needs no model and no network and builds in seconds.

**A capability manifest is worth declaring and is not worth enforcing yet.** A
game declaring which taxonomies it reads, which operations it uses and which
content it brings makes a review faster and makes an unexpected query visible
in the trace. But `PortUsage` already records operations and counts per run, so
the enforcement value is small, and a manifest the engine does not enforce is
documentation. Declare it as part of the distribution metadata, check it
against `PortUsage` in the staged rollout, and leave runtime enforcement until
the protocol exists.

**A marketplace needs three things this project does not have**: a revocation
path, a published protocol specification separate from the Python types, and an
answer to who is accountable for a published puzzle that turns out to be
unfair. None of the three is blocked by anything above; all three are premature
while the count of external authors is zero.

## 8. What this costs

### Latency

Per generation, the plugin is called once for `describe`, once for
`get_content_requirements`, once for `generate_candidates`, then up to
`MAX_ASSEMBLY_ATTEMPTS` times for `assemble` with a `verify` and a
`measure_difficulty` after each, with up to `ON_TARGET_ATTEMPTS` extra passes.
A typical successful day is about six calls; the worst case is about 195.

At the measured 112.4 us median hop that is 0.7 ms typical and 21.9 ms worst
case of transport per generation. Against the work it wraps, the ratio is not
uniform and this is the number that should decide whether the transport is
per-game:

| game | verify, in process | one hop as a share of it |
|---|---|---|
| `odd_number_out` | 0.05 ms | 225% |
| `shape_trio` | 0.31 ms | 36% |
| `digit_lock` | 1.10 ms | 10% |
| `grouping` | 11.82 ms | 1% |

So "the hop is noise" is true of the lexical games and false of the symbolic
ones. It is still a small absolute cost on a once-per-day batch operation, which
generation is, and it would be a real cost on anything interactive.

Worker startup dominates and is the number to design around: 337.6 ms median to
start a worker that has imported the platform and one game, against 35.0 ms for
a bare interpreter. Per generation that is unacceptable and per worker it is
irrelevant, so workers must be pooled and reused across days, which is a
lifecycle the engine does not currently have.

Play is the case that forces a protocol decision rather than tolerating one.
`replay_state` calls `grade_move` once per prior submission on every
submission. Measured: 1, 10, 50, 100, 250 and 500 calls at the corresponding
submission counts, 1.24 ms in process at the 500 move cap. Out of process at
per-call hops, one submission at the cap costs 56 ms of transport and a maximal
session costs 14.0 s. So the protocol needs a `replay(puzzle, moves)` verb
that returns the final state and the per-move outcomes in one exchange. With
that, a submission is two hops regardless of ledger length.

### Operational complexity

Option A adds a worker pool, a supervisor, two new failure modes (a worker that
dies mid-call and one that stops answering), a protocol to version and test
against more than one implementation, and host specific sandbox configuration
that will differ between CI and production unless the weaker one is what CI
tests. It also adds a second place where a bug can make a day fail, which for a
once-daily batch is a real operational cost and not a theoretical one.

Option D adds key management and a review queue, and nothing else.

### What it makes harder for the five games that do not need it

This is the part worth being blunt about. All five games are first party, pure
Python, depend on nothing but `puzzlegen`, and are reviewed in pull requests.
None of them needs isolation, and every cost above is a cost they would pay for
a capability that exists for somebody else.

If the transport is mandatory, they pay the 337.6 ms worker start, the hop, the
loss of a stack trace through the boundary, and a debugging experience where a
failing assertion in `verify` arrives as a protocol error rather than as a
traceback. `tests/invariants` becomes harder too: the boundary tests walk
installed game packages by import, and a game that is not importable in the
engine's process cannot be walked, so the invariants would need an in-process
mode for first party games, which is two enforcement paths to keep in step.

So the transport should be opt in per distribution rather than global. A
distribution declares whether it runs in process or sandboxed, the engine
refuses to run an unsigned distribution in process, and the five first party
games keep exactly the path they have today. That also means the hop cost is
only paid by games whose authors were not reviewed, which is the right place
for it.

## Follow-up work this proposes

Named here rather than implemented. Each is small, each is independent of the
isolation decision, and the first two should land before any external game runs
in process under option D, because option D's whole premise is that the
in-process path is safe for reviewed code.

**F1. Make the intended-solution comparison unconditional.** A truncated
solution list is a legitimate state, so the fix is not to remove the skip but
to require evidence in it: when a verifier truncates, require at least one
listed solution and compare against that, and treat an empty list with a
positive count as a `PLUGIN_PROTOCOL_ERROR` in `consistency_problem`. A
verifier that found a solution can always name one. Test: extend
`broken_grouping.py` and each symbolic game's equivalent with a game that
hides a fabricated solution behind `solutions_truncated`, as a sixth
failing-by-design fixture.

**F2. Give `states_examined` and `search_bound` something to mean, or drop the
bound.** Two options, and the choice is a judgement about what the bound is
for. Either check the bound on the `COMPLETE` path as well, which makes a
descriptor's bound a real constraint and requires every game's bound to be
measured against its widest board; or state in phase 5 and in the descriptors
that the bound applies only to the degraded path and that the state count is
telemetry. The first is more honest and costs five measurements; the second is
free and leaves a number in every descriptor that reads stronger than it is. I
lean to the first, because a measured bound is also a regression test on a
verifier's cost.

**F3. Bound what is currently unbounded.** Consume candidates through
`itertools.islice` to `MAX_CANDIDATES` rather than materialising the whole
sequence, so the cap applies to consumption and not only to screening. Add a
size cap to `Puzzle.payload` and `Puzzle.solution` in the shape the share layer
already uses, measured against the largest real board (1,694 B) with
generous headroom. Add a wall clock budget per plugin call, which in process
means a cooperative deadline the pipeline checks between calls and out of
process means a pipe timeout. The first two are small and self contained; the
third is only fully solvable out of process and should be written as the
in-process approximation now and replaced later.

**F4. Close the three invariant evasions, and say what the invariants are
for.** Add `builtins` to `FORBIDDEN_FOR_GAMES`; extend the banned call scan to
attribute calls so `builtins.open(...)` and `getattr(builtins, "eval")` are
caught; walk every top level package in a game's distribution rather than only
the entry point's own, which `importlib.metadata`'s `files()` makes available.
And then add a docstring saying plainly what the suite is: a backstop against
an honest refactor, not a boundary against a hostile author, because an
attribute walk off the granted port defeats any import scan and no amount of
parsing closes that. The last part is the most valuable, because the current
phase 8 wording invites the opposite reading.

## Decisions

1. **Signed, reviewed, in-process distribution is the policy now; the
   subprocess transport is staged behind a named trigger.** The trigger is the
   arrival of an author the project does not want to review, which somebody
   decides rather than a threshold being crossed quietly.
2. **The protocol is specified now and implemented later.** Specifying is cheap
   and is what keeps the boundary types honest while more games are written;
   the transport is the expensive half.
3. **The wire codec is `canonical_json`'s normaliser, not `json.dumps`.**
   Measured: bare JSON silently coerces an integer mapping key where the
   canonicaliser refuses it, so a naive codec is looser than the engine's own
   hashing.
4. **The protocol carries a seed and a label where the in-process call passes
   a `DeterministicRng`.** Measured to reconstruct the stream byte for byte.
   `exclude_ids` crosses as a sorted list, which is what the canonicaliser
   already emits.
5. **The protocol has a batched `replay(puzzle, moves)` verb.** Per-call
   grading makes a maximal session cost 14.0 s of transport; this is a
   precondition for out-of-process play, not an optimisation.
6. **The transport is opt in per distribution, never global.** The five first
   party games keep the in-process path and pay none of the cost.
7. **A separate network service is rejected.** It cannot keep the
   reproducibility promise the manifest exists to make, because a remote
   service can change its own code between a generation and its replay and
   nothing would record that it did.
8. **WASM stays open rather than chosen.** Its isolation and determinism
   properties are the best of the four; the reason not to start there is that
   it makes Python games second class in their own engine for the sake of an
   author who does not yet exist.
9. **An external game brings content as a declaration, not as a curated
   file, to begin with.** A declaration is a few reviewable lines and is
   reproducible by construction. The rule can be relaxed later without
   anything being withdrawn, which is not true in the other direction.
10. **Generator kinds stay platform owned.** An external game picks a
    declaration from what exists; a game needing a content shape no generator
    produces needs a platform change, which is the governed step.
11. **Admission is tested by regeneration, not by inspection.** An external
    distribution ships a committed thirty day run against a named snapshot and
    the platform's CI regenerates it, which is the mechanism
    `scripts/ci.sh symbolic` already applies to the three symbolic games.
12. **The registry refuses, at discovery, a protocol minor below what play
    needs.** A failure in front of a maintainer rather than in front of a
    player.
13. **A capability manifest is declared and checked in the staged rollout
    against `PortUsage`, not enforced at runtime yet.**
14. **Grading correctness and the accessibility declaration stay the game's
    promise.** Neither is checkable in general by the engine; both are recorded
    so a failure is attributable. The terminal `solved=True` case is checkable
    and belongs in the protocol specification.

## Open questions

1. **What is the trigger for building the transport, stated concretely enough
   that somebody can tell whether it has fired?** Decision 1 leaves it as a
   judgement, which is honest but unactionable. One candidate: the first
   distribution submitted by an author outside the project who declines review.
2. **F2's choice.** Make `search_bound` a real constraint on the publishable
   path, at the cost of measuring five games' bounds against their widest
   boards, or document it as applying only to the degraded path. I lean to the
   first and it is not my call.
3. **Who signs, and against what key list?** The mechanism is clear and the
   custody is not. A key list in configuration implies somebody owns the
   configuration, and the project currently has no `ops` layer to own it.
4. **Does an external game get a slot?** `DEFAULT_MAX_ACTIVE` is 4 and five
   games are installed, so activation is already a contested resource. Whether
   an external game competes for the same four slots or for a separate
   allowance is a product decision that changes how much any of this matters.
5. **Should the invariant suite grow a hostile-author mode at all?** F4 closes
   three evasions and the fourth, an attribute walk off the granted port,
   cannot be closed by parsing. Closing three of four may be worse than
   closing none, if it makes the suite read as a boundary it is not.
6. **The protocol specification's home.** The types live in `plugin.py` and
   their JSON representability is a property of the code; a specification an
   external author reads has to live somewhere that is not a Python module, and
   nothing in the repository currently plays that role.
