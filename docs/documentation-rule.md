# The documentation rule

Where a decision, plan, determination or measured finding gets written, and
what counts as having written it.

The rule itself is in `CLAUDE.md`, so that every session reads it without being
asked. This document is the routing table it points at, plus the worked
examples that make the routing unambiguous.

## What counts as recorded

A decision is recorded when a file in this repository states it, says why, and
says where it stands. Three failures that look like recording and are not:

- **In a conversation.** The next session starts cold.
- **In a commit message.** Nobody reads the log to find out how the system
  works, and a message cannot be corrected when the decision changes.
- **In a second document, restated.** Two copies of a rule is one rule and one
  future contradiction.

## The routing table

One subject, one owning document. When something plausibly belongs in two, it
goes in the one that owns the subject and the other links to it.

| what you have | owning document |
|---|---|
| an engine, architecture or layering decision | `architecture.md` |
| a phase's worth of work and what it settled | `architecture.md` |
| the reasoning behind any decision: what was chosen, over what, at what cost | `decision-log.md` |
| the current state, the next work, open decisions, standing cautions | `handoff.md` |
| how a real snapshot is built, and the procedure's numbers | `snapshot-build.md` |
| lexicon root selection and its measurements | `candidate-roots.md` |
| cartridge isolation, the wire protocol, sandbox analysis, resource budgets | `external-games.md` |
| a decision about one game's rules or content | that game's requirements document, for example `missing-letters-requirements.md` |
| what the product is, who it is for, what it refuses to be | `platform/product-vision.md` |
| a requirement, with whether it is built | `platform/platform-requirements.md` |
| what a cartridge is, and its lifecycle | `platform/cartridge-model.md` |
| anything an outside author must know | `platform/game-developer-guide.md` |
| how a cartridge gets listed, updated or pulled | `platform/publishing-and-certification.md` |
| what a version number promises, and deprecation | `platform/compatibility-and-versioning.md` |
| what content is allowed, in the graph or in a board | `platform/content-policy.md` |
| the author's side of the bargain | `platform/developer-terms.md` |
| what is collected, kept, or never collected | `platform/privacy-and-data.md` |
| an attacker, a trust boundary, or what stops neither | `platform/security-and-threat-model.md` |
| daily fairness, retraction, scoring integrity | `platform/fairness-and-integrity.md` |
| what every game must do for every player | `platform/accessibility-standard.md` |
| running the daily pipeline, incidents, retention | `platform/operations-runbook.md` |
| how a change reaches a player, and rollback | `platform/release-process.md` |
| what is being built now, next, later | `platform/roadmap.md` |
| a term this project invented | `platform/glossary.md` |
| an owner decision and the options it was chosen over | `platform/open-questions.md` and `decision-log.md` |
| a rule a test can enforce | the test or invariant, with the reason in its docstring |

`README.md` holds the layout and the entry points. It is not a place to record
a decision; it is a place to point at the document that did.

## The second record, where code is involved

A decision that changes what the code does is recorded twice: in the document
that owns the subject, and as the check that enforces it.

Where a test can hold the rule, **the test is the record** and the prose points
at it. The platform already works this way: the invariant that the platform
names no game, the assertion that identity material never comes from the
deterministic generator, the assertion that the engine imports no token
handling, and `WITHOUT_A_WITNESS`, which names each game that supplies no
witness together with the reason. Each of those is a decision that cannot
quietly stop being true.

Prose alone is the right record only where no check is possible: a product
choice, a policy, a reason, or a promise the engine cannot verify and therefore
records so that a failure is attributable.

## Saying where something stands

Every recorded decision carries its state, in its own words rather than as a
footnote:

- **Decided and built.** The code does this, and a check holds it.
- **Decided and unbuilt.** The choice is made and nothing implements it yet.
- **Measured and unresolved.** A number exists and the decision does not.
- **Specified, staged behind a trigger.** The design is done and somebody
  decides when to build it, the trigger named rather than numeric.

The failure this prevents is a document describing an intention in the present
tense. "A sandbox denies network access by default" is true of a sandbox that
exists and is a lie about a plan.

## Numbers

A number in prose that an artifact also produces is a future contradiction. The
rules, which come from four separate instances of exactly this in this project:

- A tool that could disagree with the engine imports the engine's constant or
  reads the engine's output. It never re-derives the value.
- A document that could disagree with an artifact gets the artifact rebuilt
  rather than the document edited.
- A number is written down with what produced it and when, or it is marked as
  not re-measured.

## Worked examples

**"Five slots per player."** A product requirement. It goes in
`platform/platform-requirements.md` as a requirement with its state, in
`platform/open-questions.md` as the answer with the options it beat, and in
`decision-log.md` with what it commits the project to. It does not go in
`product-vision.md`, which links to the requirement instead.

**"Every new game supplies a witness or is named with a reason."** A rule a test
can hold. The record is `WITHOUT_A_WITNESS` in the architecture invariant, with
the reason in the docstring; `architecture.md` states the decision and points at
the invariant.

**"The daily set's coupling stays measuring, and the cross board generation link
is not built."** A decision not to build something. It goes in
`decision-log.md` with why measuring more pairs would not produce a coupled one,
and in `handoff.md` so the next session does not re-open it. The machinery stays
in place and unused, and the document says that is deliberate.

**"A1 produced 3,229 entities."** A number from a build that no longer exists.
It is either re-measured against the current build or marked as superseded.
`snapshot-build.md` does this in its opening paragraph, which is the pattern.

## When the rule is satisfied and the work is still wrong

Recording a decision is not the same as the decision being good, and this rule
does not make a document an authority. A document says what was decided and
why. The code and the artifacts say what is true. When they disagree, the code
is right and the document is a bug.
