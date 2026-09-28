# PuzzleGen Handoff

This file is overwritten at the end of every conversation in the Puzzle
Universe project. It is the first thing a new conversation reads. It answers
three questions: where things stand, what you need to do before the next
conversation starts, and what the next conversation should do first.

## Where things stand

- Phases 1 through 6 are complete. 1093 tests pass across both storage
  backends (in-memory and SQLite): 763 from phases 1 to 5, 330 added in
  phase 6.
- Full decision history: `docs/architecture.md`. Read it after confirming the
  steps below, not before. Phase 7's content, review, proposal and board-shape
  decisions are already recorded there, under "Content, review and proposal
  (decided ahead of phase 7, for phase 7 to implement)". They are settled. Do
  not reopen them; implement them.
- Phase 6 delivered the session engine, three-tier session identity, the
  scoring interface, the share artifact system and the accessibility
  infrastructure. Seven new modules under `puzzlegen/engine/`: `sessions.py`,
  `session_storage.py`, `identity.py`, `scoring.py`, `session_service.py`,
  `sharing.py`, `accessibility.py`.
- Plugin protocol is `1.1.0`. `grade_move` and `get_hint` were added
  additively: a 1.0 game still registers and still generates, and cannot have
  a session started against it.
- Nothing under `puzzlegen/graph/`, `puzzlegen/providers/` or
  `puzzlegen/content/` changed in phase 6.
- `puzzlegen/games/` is still empty. Game 1 is phase 7.
- No real snapshot exists yet. Everything built so far has been exercised by
  test fixtures, an 8-synset lexicon and a 23-entity curated file. Building a
  real snapshot is phase 7's first job.

## Manual steps required before the next conversation begins

1. Merge the phase 6 branch. Fourteen files, five of them replacing existing
   ones; the paths are listed in `docs/architecture.md`'s repository layout.
   Confirm `python -m pytest -q` reports 1093 passing.
2. Push this file and `docs/architecture.md` to the repository root's `docs/`
   directory.
3. If the repository is connected to this Claude project, confirm the
   connection is active before starting phase 7, so the reading-order
   convention (conversation, then attachments, then project memory, then
   repository) has a repository to actually read from.
4. Have a build environment available where `wn` and `wordfreq` can be
   installed. The engine never imports either; the exports run offline and the
   resulting JSON is committed. This is not needed to start phase 7, only to
   run batch 1a's commands when they arrive.
5. No other manual steps. Phase 7 requires no new external accounts, API keys
   or provider registrations.

## What the next conversation must do first

Before any phase work: ask whether the manual steps above are complete.
Accept exactly one of two replies:

- **`Steps Complete`** — proceed with phase 7 as scoped below.
- **`Detour {filename.md}`** — stop, read the named file (must be attached to
  the chat or a path under `docs/` in the repository), and ask what to do
  with it before touching phase 7 work. Do not resume phase work until the
  detour is explicitly closed.

If the reply is neither of these two forms, ask for it to be restated as one
of them. Do not guess which was intended.

There is no open design question blocking the start. The content source, the
identity mode, the proposer's signals, the review threshold, the board shape
and the axis-switch trigger are all decided and recorded. Begin with batch 1b.

## Phase 7 batch order

Settled in conversation. The proposer comes before the seed file, so the seed
is designed as the proposer's input format rather than retrofitted to it.

| Batch | Contents |
|---|---|
| 1b | `puzzlegen/content/review.py` and `ReviewRepository`, the ten-accept threshold rule, the activation policy admitting APPROVED, the invariant test forbidding automatic promotion |
| 1c | `tools/propose_overlay.py` and `tools/review.py` |
| 1d | `content/seeds/overlay.curated.json`, the seed overlay, authored against the proposer's format |
| 1a | `tools/report_lemma_collisions.py`, `tools/build_snapshot.py`, and the seven export plus frequency commands to run |
| 2 | `puzzlegen/games/grouping/descriptor.py` and `content.py`: descriptor with share tokens, state symbols, difficulty thresholds sized for 5 to 9, declared content requirements |
| 3 | `generate.py` and `assemble.py`: candidate generation using `find_intersecting_groups`, group selection, decoy construction |
| 4 | `verify.py` and `difficulty.py`: `enumerate_partitions` with the partition-level validity check, `UNIQUE_UP_TO_TOLERANCE` reporting `collapsed_solutions`, difficulty normalised against board size |
| 5 | `play.py`: `grade_move` with the derived axis switch, `get_hint`, `score`, `render`, `create_share_artifact` |
| 6 | The five broken fixtures in `tests/support/`, `tests/unit/test_grouping_game.py`, and an end to end day test |

1a sits after 1d because its commands need a machine with `wn` installed and
its output is JSON to commit, so it is the one batch whose completion does not
depend on the conversation continuing.

## Things phase 7 will get wrong if it forgets them

- `grade_move` must be a pure function of the puzzle and the moves before it.
  The session layer replays the entire ledger on every submission and raises
  `DeterminismError` if a regrade disagrees with a recorded outcome. The
  derived axis switch runs a feasibility check inside `grade_move`; memoising
  within one replay pass is fine, across calls is not.
- The verifier must report `collapsed_solutions` or the
  `UNIQUE_UP_TO_TOLERANCE` contract fails. A grouping puzzle that never
  actually applies its tolerance rule cannot claim credit for it.
- An incomplete enumeration satisfies no uniqueness contract. A budget overrun
  reports `SOUND_INCOMPLETE` and a lower bound, never a count dressed up as
  exact.
- The game must declare `share_tokens` and `state_symbols`, or it cannot have
  a session started against it. The accessibility gate and the share
  vocabulary check are both enforced, not advisory.
- Nothing in `tools/` may write `APPROVED` or `ACTIVE`. The proposer writes
  JUDGED candidates that land in `PENDING_REVIEW`; the review service derives
  `APPROVED`; only `SnapshotBuilder.activate()` writes `ACTIVE`.
- The five broken fixtures ship in phase 7 as failing-by-design proofs that
  the engine catches what it claims to. They live in `tests/support/`, never
  in `puzzlegen/games/`. Fixing the real game against real content is phase 8.

## Standing project conventions (apply regardless of phase)

- No preamble, no recap, no filler; decisions and open questions stated in
  lists at the end of each response.
- Every multi-option question or choice gets a plain-language explanation
  (assume a middle-school reading level) plus a benefits-versus-costs
  weighing for each option, before any recommendation.
- Files longer than ~300 lines: state what is about to be produced and wait
  for confirmation before generating it.
- Reading order for any single reply: conversation, then attachments, then
  this project's file context, then the connected repository, in that
  priority order.
