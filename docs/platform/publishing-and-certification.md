# Publishing and certification

How a cartridge becomes a listed game, and how it stops being one.

The process has a stated bias: the expensive check is automated and the cheap
check is human. Regeneration is the gate that cannot be argued with, and it runs
first, because a reviewer reading code cannot tell whether a game is
deterministic and a rebuild can.

## Stages

### 1. Intake

The author submits a distribution and a thirty day run. Required in the
submission:

- the distribution, at a specific version, with its own tests
- a committed thirty day run against a named snapshot
- the content declaration, in reviewable form
- the accessibility declaration (part of the descriptor)
- a short description of what the player does, written for a player
- a contact the platform can reach

Rejected at intake without further review: a submission with no day run, a
submission whose content arrives as an unreviewable data file, or a submission
declaring a protocol major the engine does not speak.

### 2. Automated certification

Run by CI, and the result is pass or fail with no judgement involved.

| check | fails when |
|---|---|
| platform suite, game uninstalled | the game's presence was required for the platform to pass |
| game suite, game installed | the author's own tests do not pass |
| regeneration of the thirty day run | any day differs by a single byte |
| protocol version | major differs, or minor is below what play needs |
| contract conformance | the verifier's results contradict the declared contract |
| completeness declaration | `COMPLETE` declared with a partial enumeration, or incomplete with no `search_bound` |
| band reachability | a declared band that the content cannot produce |
| accessibility gate | the declaration is incomplete, so no session can start |
| share vocabulary | an artifact uses a token outside the declared set |
| share leak check | the artifact leaks solution information |
| import invariants | the game reaches outside its ports by any path the invariants catch |
| determinism probes | a clock, network, filesystem, environment or unseeded randomness is used on a path the probes cover |

Two of those are known to be incomplete rather than sound, and saying so here
matters more than the checklist looking tidy. The import invariants have known
evasions, including an attribute walk from a granted port that no amount of
parsing closes, because `__closure__`, `__globals__` and `__class__` are
ordinary attributes. The determinism probes cover the paths they cover. Both are
why the next stage exists.

### 3. Human review

What a person reads, in this order:

1. `verify.py`. Does the proof prove the declared contract?
2. The content declaration. Is this a slice of the governed graph, or a corpus
   smuggled in?
3. `play.py`. Is grading plausibly correct, and is the hint derived from a
   witness rather than authored?
4. The rest, for anything that reaches for a capability it was not given.

What a person is not asked to do: confirm determinism, confirm band coverage,
or confirm accessibility completeness. Those are machine checks and a human
signing off on them adds a false signal.

The review is a code review by somebody who can refuse. Until the isolation
transports ship, this review is the actual security boundary for a third party
cartridge, and the policy is that nothing is listed without it.

### 4. Signing and listing

A certified, reviewed distribution is signed and listed. The signature binds the
exact version reviewed. An unsigned distribution is not loadable in production,
which is what makes "reviewed" an enforceable property of what runs rather than
a claim about what was inspected once.

Listing is a separate decision from certification. Passing every check earns a
cartridge the right to be considered, not the right to be listed.

### 5. Activation

The operator activates the game against a day key. Activation is bounded by the
platform's active game cap and recorded as an event, so the question "which
games ran that day" is answerable later. A listed but inactive game is visible in
the catalogue and generates no days.

## Updates

A new version of a listed cartridge re-enters at stage 2, always. The thirty day
run is regenerated for the new version.

A version that changes any output bytes for a day key that has already been
published is not an update, it is a different game's worth of risk: the
published days stay as they were published, and the new version takes effect from
a future day key. There is no retroactive regeneration of published days. This is
the same rule the platform applies to itself.

A version that changes grading changes `SCORING_VERSION`, and transcripts
recorded under the old version are not regraded under the new one.

## Withdrawal and revocation

Four different things, deliberately distinguished, because collapsing them is
how a platform ends up punishing players for a maintainer's decision.

**Deactivation.** The operator stops generating days. Played days stay valid and
readable, streaks stand, the cartridge stays in players' slots marked as
producing no new days. Reversible.

**Delisting.** The cartridge leaves the catalogue. Players who hold it keep it.
Reversible.

**Retraction of days.** Specific published days were wrong. They are withdrawn,
the players who played them have their streaks restored, and a preview of what a
retraction affects is produced before it is applied. Days, not the game.

**Revocation.** The signature is revoked and the distribution will not load.
This is for a cartridge found to be hostile or to have violated the terms, and
it is the only case where a player's held cartridge stops working. Revocation
retracts affected days as well, because a hostile cartridge's boards cannot be
assumed sound.

Grounds for revocation are in `developer-terms.md`.

## Service levels, and the honest statement about them

This process is designed and unstaffed. There is one maintainer, so any
published review turnaround would be a number nobody is accountable for.

Stated instead:

- Automated certification runs on submission and reports within one CI run.
- Human review is scheduled by the maintainer and has no committed turnaround
  until there is a second reviewer.
- Retraction of a wrong day is the one path with a real urgency commitment, and
  it is in `operations-runbook.md`.

A committed review time is a thing to add when there is somebody to commit it.
