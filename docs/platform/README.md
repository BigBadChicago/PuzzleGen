# PuzzleGen platform documents

The engineering record lives in `docs/architecture.md`, `docs/decision-log.md`
and `docs/external-games.md`. This folder holds the documents a game engine and
platform needs in order to be treated as a platform by somebody who is not the
person who built it: a third party author deciding whether to write a cartridge,
a store reviewer deciding whether to list the app, a player deciding whether to
trust it with their play history.

Every document here is written against the code on `main`. Where a document
describes something the code does not do yet, it says so in its own words
rather than in a footnote, because a platform document that quietly describes
intentions is the failure mode this project has already named.

| document | answers | state |
|---|---|---|
| [product-vision.md](product-vision.md) | what PuzzleGen is, who it is for, what it refuses to be | drafted |
| [platform-requirements.md](platform-requirements.md) | what the platform must do, as testable requirements | drafted, player library unbuilt |
| [cartridge-model.md](cartridge-model.md) | what a cartridge is and its whole lifecycle | drafted, install path partly built |
| [game-developer-guide.md](game-developer-guide.md) | how to write a game against the engine | drafted, matches protocol 1.2.0 |
| [publishing-and-certification.md](publishing-and-certification.md) | how a cartridge gets listed, and how it gets pulled | drafted, process unstaffed |
| [compatibility-and-versioning.md](compatibility-and-versioning.md) | what a version number promises | drafted |
| [content-policy.md](content-policy.md) | what a cartridge and the graph may contain | drafted, needs owner review |
| [developer-terms.md](developer-terms.md) | the author's side of the bargain, in plain words | draft, not legal advice |
| [privacy-and-data.md](privacy-and-data.md) | what is collected, kept, and never collected | drafted, needs legal review |
| [security-and-threat-model.md](security-and-threat-model.md) | who the attacker is and what stops them | drafted, isolation staged |
| [fairness-and-integrity.md](fairness-and-integrity.md) | why every player gets the same puzzle, and what happens when they did not | drafted |
| [accessibility-standard.md](accessibility-standard.md) | what every game must be able to do for every player | drafted, gate enforced in code |
| [operations-runbook.md](operations-runbook.md) | running the daily pipeline, and what to do when it breaks | drafted, no on call |
| [release-process.md](release-process.md) | how a change reaches a player | drafted |
| [roadmap.md](roadmap.md) | what is being built now, next, later | drafted |
| [glossary.md](glossary.md) | every term this project invented | drafted |
| [open-questions.md](open-questions.md) | the decisions these documents are waiting on | open |

## Reading order for a newcomer

1. `product-vision.md`, for what is being built.
2. `glossary.md`, for the vocabulary the rest assumes.
3. `cartridge-model.md`, for the one idea the platform is organised around.
4. `game-developer-guide.md`, if you intend to write a game.
5. `docs/architecture.md`, if you intend to change the engine.
