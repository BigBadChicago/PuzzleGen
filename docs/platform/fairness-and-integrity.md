# Fairness and integrity

A daily puzzle makes one promise: everybody got the same puzzle, and it was
solvable. This document says how that promise is kept, how it is checked, and
what happens on the days it is broken.

## The four fairness properties

**1. Identical.** Every player of a given game on a given day key gets the same
board. This is not achieved by sending everybody the same file; it is achieved
by generating the day once, ahead of time, into an immutable published manifest.
There is no per player generation anywhere in the system.

**2. Solvable, and provably so.** A day is not published unless the game's own
verifier accepted it against the contract the game declared. The engine checks
the verifier's result against the declaration, so a game that promises exactly
one solution and returns a board with two is refused rather than published.
Solvable alone is never sufficient: a board with three solutions is solvable and
unfair.

**3. Reproducible.** The same day key, snapshot and version set regenerate the
same bytes. Every version that could change those bytes is recorded in the
manifest, which is what makes a difference detectable as a fault rather than
mysterious. CI regenerates committed day runs and fails on a single byte.

**4. Knowable.** A board must be solvable by a reasonable player in the declared
locale, from knowledge they could have. This is the only one of the four that no
machine checks, and it is the one most often broken by accident: a category whose
members are technically correct and practically unguessable. It is a review
property, and it is in `content-policy.md` as a refusal.

## Difficulty, and why calibration is a fairness question

A difficulty band is a promise to the player about what they are about to spend
twenty minutes on. The rule that keeps the promise honest: a scale is calibrated
over the boards the engine actually reaches, after every gate, not over every
candidate the generator offered. One shipped game is the exception, where the
band itself changes which boards exist, and there the sample is that band's own
reachable set.

Getting this wrong does not produce an error. It produces a model that bands a
minority of boards correctly and reads as a bad model rather than a bad sample.
And a band target that the content cannot reach turns a ranking into a silent
shuffle. Both have happened here.

## Hints

A hint is derived from the elimination the generator already performed, where
the game supplies a witness. The alternative, authoring hints beside the board,
creates a second source of truth that drifts from the first, and a hint that
contradicts the board is worse than no hint.

A hint is charged at the cost the player experiences, not at an authored cost
and not at what it cost to compute. This matters for fairness because the hint
cost is part of the score, and a mispriced hint is a scoring error dressed as a
feature.

## Scoring integrity

- Grading is server side. The client does not hold the answer, so it cannot
  grade, and it is not asked to.
- Every move is recorded in a ledger under the scoring version in force.
- A transcript recorded under one scoring version is never silently regraded
  under another. When the rules change, old transcripts keep their grades.
- A scoring change bumps `SCORING_VERSION` independently of the engine version,
  because grading can change without generation changing.

## Sharing without leaking

Share text is pasted into surfaces the platform does not control, and it is
generated from a session that contains the answer. The share redactor runs a
leak check on every artifact before it leaves, and the game is restricted to the
share vocabulary it declared, which is what lets the platform hold a codepoint
allowlist for share text.

The reason this is a named subsystem rather than a formatting function: a move
ledger whose lopsided shape revealed an answer is a leak that no amount of
careful wording prevents. The shape is the leak.

## When a day was wrong

The mechanism is retraction, never editing. A published day is immutable; a
correction is a withdrawal plus a new publication.

What retraction does:

1. A preview is produced first, naming what the retraction affects, before
   anything is applied.
2. The day is withdrawn and recorded as retracted, with a reason.
3. Every player who played it has the day excused, so it does not count against
   their streak. A streak broken by a board that was wrong is restored.
4. The sessions themselves are kept and marked excused, so the restoration is
   auditable afterwards rather than a silent adjustment.
5. A replacement day may be published for that day key.

What retraction never does: change a player's score on a day that was fine,
delete a session, or edit a published manifest.

The asymmetry is deliberate. A wrong board is the platform's fault, so the
player keeps what they earned and loses nothing they did not. No retraction has
ever been used to take something away.

## Grounds for retracting a day

- The board was unsolvable, or had a different solution count than declared.
- The board was unfair under the knowability rule.
- The board breached `content-policy.md`.
- The board was produced by a cartridge later found hostile, in which case all
  of its affected days go, because none of its boards can be assumed sound.
- The board was generated against a snapshot since found to contain a wrong
  fact, where the board depended on that fact.

A board that was merely harder than its band claimed is not retracted. It is a
calibration bug, it is fixed forward, and the players keep their results.

## What is not protected, and is not pretending to be

- **Ledger plausibility.** A fabricated ledger that grades correctly is
  accepted. This only matters with a leaderboard, and there is none.
- **Collusion.** Two players sharing an answer before one plays is not
  detectable and is not a thing this platform will try to detect.
- **A game's own grading.** Correct grading is the cartridge's promise, recorded
  so that a failure is attributable. The engine checks the terminal solved case
  and nothing finer.
