# Compatibility and versioning

What each version number in this system promises, and to whom.

The organising rule, from `puzzlegen/core/versions.py`: every component that can
change the bytes of a published puzzle carries its own version, and every
manifest records all of them. Without that, a regenerated puzzle that differs
from its original is indistinguishable from a bug.

## The versions

| version | covers | bumped when | who reads it |
|---|---|---|---|
| `ENGINE_VERSION` | generation, verification, difficulty, scoring behaviour in the engine | any change that can alter output bytes | the manifest, a rebuild check |
| `CONTENT_SCHEMA_VERSION` | the shape of graph records | any incompatible change to the graph models | a snapshot, which is rejected rather than misread if the version is wrong |
| `PUZZLE_FORMAT_VERSION` | the shape of a published puzzle document | any change to what a manifest holds | the client, the browser shell, a rebuild check |
| a shipped content file's hash | the exact content bytes a board came from | the cartridge ships different content | a manifest, a retraction, a rebuild check |
| `SCORING_VERSION` | the rules that turn a move ledger into a grade | telemetry derivation, the scoring contract, or a shipped game's scoring function changes in a way that can alter a number | a session record, a replay |
| `SESSION_SCHEMA_VERSION` | the shape of a session document and its inline ledger | the ledger would have to be reinterpreted rather than read | a replay, which refuses rather than guesses |
| `SHARE_FORMAT_VERSION` | the shape of a share artifact | share text changes | third party surfaces, which is why it is split from the puzzle format |
| `PLUGIN_PROTOCOL_VERSION` | the engine to cartridge wire contract | a verb is added or changed | the registry, at discovery |
| a game's `game_version` | that one cartridge | the author decides | certification, the manifest |

The separations are not bookkeeping. `SCORING_VERSION` is separate from
`ENGINE_VERSION` because grading can change without generation changing, and a
transcript recorded under one grading rule must not be silently graded under
another. `SHARE_FORMAT_VERSION` is separate from `PUZZLE_FORMAT_VERSION` because
share text is pasted into surfaces the platform does not control and changes on
a different cadence.

**Recording a content file's hash in every manifest is a manifest change**, so
the decision to require it (condition 2 in `content-policy.md`) costs a
`PUZZLE_FORMAT_VERSION` bump when it is built, and a client that cannot read the
new version refuses the day rather than rendering it partially. That is the
coupling the condition buys: a board traces to the exact content bytes behind
it, so a retraction can name the content rather than the game. It was accepted
deliberately.

## The protocol version in particular

`PLUGIN_PROTOCOL_VERSION` is `1.2.0`. Its history is the model for how it moves:

- `1.0` the original contract.
- `1.1` added `grade_move` and `get_hint`. Additive within the major, so a 1.0
  cartridge still loads and still generates. What it cannot do is have a session
  started against it, and the session service reports that by name rather than
  as an attribute error mid play.
- `1.2` added `witness`, and added it as a declared capability rather than a
  required method. A cartridge announces it in `supplies_witness` and the engine
  calls it only where that is set. Additive again, so every 1.1 cartridge loads
  unchanged.

Two rules the engine enforces:

1. **A different major is refused.** Not degraded, not adapted. Refused.
2. **A minor below what play needs is refused at discovery**, not at play. A
   failure in front of a maintainer rather than in front of a player.

The pattern to copy for any future addition: add the verb, declare the
capability on the descriptor rather than discovering it by introspection, and
call it only where declared. A declared absence is readable; a missing method is
an error somebody hits at the worst moment.

## What a major bump would cost

A protocol major bump invalidates every cartridge until each is updated, so it
is the most expensive action available in this system. The bar for it: a change
that cannot be expressed as an additive verb plus a declared capability. Before
proposing one, show that the capability declaration pattern cannot carry it.

## Published days are immutable across all of it

No version bump regenerates a published day. A day was generated against one
snapshot and one version set, both recorded, and that is what it stays. When
behaviour changes, the new behaviour takes effect from a future day key.

The corollary for a rebuild check: rebuilding an old day under new versions is
expected to differ, and the check compares a day against the version set it was
published under. `check_rebuild` in `puzzlegen/engine/release.py` is the
mechanism.

## Client compatibility

The client is thin, and the contract between it and the platform is the
published manifest plus a JSON Schema generated from the engine's models.

- A client MUST refuse a manifest whose `PUZZLE_FORMAT_VERSION` it cannot read,
  and say so, rather than rendering a partial board.
- A client MUST tolerate fields it does not know within a format version it does
  know, because that is what makes additive manifest changes shippable without a
  forced client update.
- A format major is a forced client update, and the platform must be able to
  tell a client to stop trying.

## Deprecation

Nothing in this system is on a deprecation clock yet, because nothing outside
the project depends on it. The policy for when something does:

- An additive change ships with no notice.
- A removal from the protocol is announced at the same time a replacement is
  available, and the removal is the next major.
- A listed cartridge affected by a removal is told, by its contact, before the
  major ships, and is not revoked for failing to update: it is deactivated,
  which leaves its players' history intact.
