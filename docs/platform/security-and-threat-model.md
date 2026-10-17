# Security and threat model

Who would attack this platform, what they would try, and what actually stops
them today. Where nothing stops them, this document says so.

The detailed analysis of the cartridge isolation problem, with measurements, is
`docs/external-games.md`. This document is the whole threat picture and points
there for the hardest part of it.

## Assets

1. **The published day.** Its integrity is the product. A board that is wrong,
   or different for different players, is a failure of the only promise a daily
   puzzle makes.
2. **The answer.** Before a player has played, the answer must not be reachable
   from anything the client holds.
3. **Player identity.** A credential that can be stolen is a streak that can be
   stolen, and a history that can be read.
4. **The graph.** Content integrity, and the provenance that lets a wrong fact
   be traced and withdrawn.
5. **The host process.** A cartridge runs in it.

## Trust boundaries

| boundary | between | enforced by |
|---|---|---|
| client to service | the player's device and the platform | server side grading; the client never holds the answer |
| cartridge to engine | a game and the host | import invariants today; a sandbox when staged |
| engine to graph | generation and governed content | the content port, and a declared requirement |
| session to publication | play and a published day | the session layer reads records and writes nothing back |
| share artifact to the world | the platform and third party surfaces | the share redactor and the declared vocabulary |

## Adversaries and what they get

### The player who wants the answer

Tries: reading the client's cached day; replaying a session; inspecting share
text from somebody who finished; asking for tomorrow's day.

Stopped by: grading is server side and the answer is not in what the client
holds; the share redactor runs a leak check before any artifact leaves, which
exists because a move ledger's lopsided shape revealed an answer here once; share
text is restricted to the game's declared vocabulary.

Decided, not built: a day cached ahead of its date is stored encrypted, with the
unlock keys for the whole cached window delivered on each refresh. Requirements
O6 and O7. It raises the cost of reading tomorrow's board above curiosity and
does not pretend to defeat a determined person with their own device, which is
the honest limit of any client side measure.

### The player who wants a streak they did not earn

Tries: submitting a fabricated move ledger; replaying a day they already played;
playing a day from the future and claiming it later.

Stopped by: the ledger is graded server side under the recorded scoring version,
and a transcript cannot be regraded under a different version; a day is played
once.

Open: ledger plausibility is not checked beyond grading. A ledger that grades
correctly but could not have been produced by a person is accepted. This matters
only once there is a leaderboard, which there is not.

### The hostile cartridge author

Tries: reading the filesystem, the network or the environment; reaching the
content service through the port it was given; consuming unbounded compute or
memory; producing a board that is not what it verified; exfiltrating player
data.

Stopped by, today: a signed and reviewed distribution policy, which means a
human read the code before it was listed. The import invariants catch the
straightforward attempts.

Not stopped, today, and this is the honest statement: the invariants have three
known evasions, and a fourth, an attribute walk from the granted port to the
content service, cannot be closed by parsing at all, because `__closure__`,
`__globals__` and `__class__` are ordinary attributes. Five designs for hiding
the port were attacked and all five were reached. That is a ceiling on the
mechanism, not a task waiting to be done. The engine also has no timeout and no
memory limit anywhere, and candidate consumption, payload size, presentation,
hints and solution lists are unbounded.

What is staged for it: a subprocess transport and a WASM transport, both
specified, both opt in per distribution so the first party games pay none of the
cost. Compute is bounded by an instruction budget rather than a wall clock,
because a timeout would make whether a day generates depend on machine speed,
which is exactly the class of nondeterminism the snapshot hash and version set
exist to eliminate. A sandbox denies clocks, randomness, network, filesystem and
the environment by default and injects explicitly: the difference from today is
the difference between a policy and a property.

The trigger for building it is named rather than numeric: the arrival of an
author the project does not want to review. Somebody decides that; it is not a
threshold crossed quietly.

### The careless cartridge author

Tries nothing. Ships a game that reads a clock, or whose generator can reach an
offensive letter arrangement, or whose verifier proves something weaker than it
declared.

Stopped by: regeneration certification, which catches nondeterminism from a
clock immediately; contract conformance checks; the share vocabulary check; human
review of `verify.py`.

Not stopped: an offensive arrangement that thirty days did not reach. The
mitigation is the author's generation time filter, required by
`content-policy.md`, and retraction when one slips through.

### The attacker with the database

Tries: impersonating a player from a dump.

Stopped by: credentials are stored only as `hmac_sha256(pepper, key)`, with the
pepper injected rather than read from the environment by the package. The dump
alone is not enough.

### The attacker on the network

Out of scope for this document beyond the obvious: transport is TLS, and the
engine never sees a provider token. `VerifiedProviderAssertion` is named for what
the caller promises and carries provider, subject, audience and verification
time, and nothing else, with an invariant test asserting the engine imports no
token handling.

## The promises the engine checks, and the ones it cannot

Checked: the uniqueness contract against the verifier's result; the completeness
declaration against the enumeration; the terminal `solved=True` case; the share
vocabulary; the accessibility declaration's completeness; regeneration.

Taken on trust and recorded so a failure is attributable: grading correctness in
general, and the accessibility promise beyond its declared completeness. Neither
is checkable in general by the engine, and recording them is what turns an
unverifiable promise into an attributable one.

## What to do when something is found

See `operations-runbook.md` for the mechanics. The decision tree:

- A board is wrong: retract the day, restore the streaks.
- A cartridge is misbehaving: deactivate, then decide between delisting and
  revocation.
- A cartridge is hostile: revoke, retract its affected days, and treat the host
  process as compromised for the period it ran.
- A credential leaked: revoke that device key, which is why keys are per device
  and labelled.
- The pepper leaked: every credential is invalidated, which is a forced
  re-authentication for every device and has no partial remedy.
