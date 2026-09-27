# PuzzleGen Handoff

This file is overwritten at the end of every conversation in the Puzzle
Universe project. It is the first thing a new conversation reads. It answers
three questions: where things stand, what you need to do before the next
conversation starts, and what the next conversation should do first.

## Where things stand

- Phases 1 through 5 are complete. 763 tests pass across both storage
  backends (in-memory and SQLite).
- Full decision history: `docs/architecture.md`. Read it after confirming the
  steps below, not before.
- Repository files reflect phase 5's end state exactly. Nothing has changed
  since the phase 5 zip was produced.
- Session identity was decided ahead of schedule (three-tier: server-issued
  anonymous id as the primary key, client-held key as its proof of return,
  optional linked providers for recovery). Recorded in
  `docs/architecture.md` under "Session identity", not yet implemented.

## Manual steps required before the next conversation begins

1. Confirm the GitHub repository is created and all five phase zips are
   merged into it as described in the directory structure (chat message,
   this project, dated 2026-09-27). Push `docs/architecture.md` and this file
   to the repository root's `docs/` directory.
2. If the repository is connected to this Claude project, confirm the
   connection is active before starting phase 6, so the reading-order
   convention (conversation, then attachments, then project memory, then
   repository) has a repository to actually read from.
3. No other manual steps. Phase 6 does not require any new external accounts,
   API keys, or provider registrations; OAuth provider setup (tier 3 identity)
   is not needed until it is actually implemented, later than phase 6.

## What the next conversation must do first

Before any phase work: ask whether the manual steps above are complete.
Accept exactly one of two replies:

- **`Steps Complete`** — proceed with phase 6 as scoped in
  `docs/architecture.md`'s "Open items carried into phase 6" section.
- **`Detour {filename.md}`** — stop, read the named file (must be attached to
  the chat or a path under `docs/` in the repository), and ask what to do
  with it before touching phase 6 work. Do not resume phase work until the
  detour is explicitly closed.

If the reply is neither of these two forms, ask for it to be restated as one
of them. Do not guess which was intended.

## What phase 6 covers

From the confirmed plan: session engine, scoring interface, share artifact
system, accessibility infrastructure, plus implementing the three-tier
session identity decided above. Follow the same phase discipline as phases 1
through 5: state a one-line decision per open design point, implement
completely, test exhaustively, stop at the phase boundary, update this
handoff and `docs/architecture.md` before ending the conversation.

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
