# Working rules for PuzzleGen

## Document what was decided, in the document that owns it

**A decision, plan, determination or measured finding is not recorded until it
is in a file in this repository.** A conversation is not a record, a commit
message is not a record, and a thread somebody can scroll back through is not a
record. The next session starts cold and reads files.

This is not a style preference. This project's named recurring failure is an
artifact and a statement about it drifting apart: a hand copied threshold, a
tool that recomputed a value instead of reading it, a document whose table
disagreed with the artifact it summarised, a lexicon named for a root it does
not hold. A decision kept in a conversation is the same failure one step
earlier.

### The rule, in four parts

1. **Write it in the same session it is made.** Not at the end of the phase.
2. **Write it in the document that owns the subject**, by the routing table in
   `docs/documentation-rule.md`. One subject, one owning document.
3. **Write the reason, not only the outcome.** What was chosen, what it was
   chosen over, and what it costs. An outcome with no reason gets reversed by
   somebody who only sees the cost.
4. **Say where it stands.** Decided and built, decided and unbuilt, or measured
   and unresolved. A document that quietly describes an intention as a property
   is the failure this rule exists to prevent.

### Where things go

`docs/documentation-rule.md` holds the routing table and the worked examples.
The short version:

| what | goes in |
|---|---|
| an engine or architecture decision | `docs/architecture.md` |
| the reasoning behind any decision, and what it was chosen over | `docs/decision-log.md` |
| current state, next work, open decisions, cautions | `docs/handoff.md` |
| a product, policy, process or platform decision | the owning document in `docs/platform/` |
| how a snapshot is built | `docs/snapshot-build.md` |
| cartridge isolation, the wire protocol, sandboxing | `docs/external-games.md` |
| a decision about one game | that game's requirements document |
| a decision a test can enforce | the test or invariant, named, with the reason in its docstring |

A decision that changes what the code does is recorded twice: in the document
that owns the subject, and as the check that enforces it. Where a test can hold
the rule, the test is the record and prose points at it.

### What not to do

- Do not restate a decision in a second document. Link to the owning one.
- Do not copy a number out of an artifact into prose. Rebuild the artifact, or
  have the tool print it.
- Do not record an intention in the present tense. "The sandbox denies network
  access" when no sandbox exists is the sentence that costs the most later.

## Response conventions

No preamble, recap or filler. Decisions and open questions in lists at the end.
Every multi option choice gets a plain language explanation and a benefits
against costs weighing before any recommendation. State anything over roughly
300 lines before producing it and wait. No dashes except within a word.
