# Roadmap

Now, next, later. Written as outcomes rather than tasks, and each item says what
makes it ready to start or ready to stop.

The ordering principle is the project's own optionality doctrine: prefer work
that increases future flexibility, survives the game that motivated it, and is
reversible. The practical reading of that here is that the player facing layer
is now the bottleneck, because five games and no client means the platform's
flexibility is unproven against a real player.

## Now

**1. The player's library and slots.**
The product idea nobody has built. Five slots, add and remove at will, history
surviving removal. Requirements L1 to L5 and L8 in `platform-requirements.md`.
Ready when: the add, remove and re-add paths keep session history intact.

**2. The thin client, first version.**
Today's puzzle, played, graded server side, for the games in the player's slots.
Ready when: a player can play every held game's day on a phone and the share
text pastes correctly.

**3. The cache window.**
Rolling five days: today, two past, two ahead. Refresh after a play while
connected, otherwise on next connection. A corrected day overwrites with no
prompt. Days ahead of today are stored encrypted, with the unlock keys for the
whole window delivered on each refresh, which is what keeps a player offline
across a day boundary able to open a day they already hold.
Ready when: a day ahead of its date cannot be read, and a day that has arrived
can be, with no connection in between.

**4. Operational alerting.**
Requirement R2. An active game with no published day for tomorrow must page
somebody. Today a person looks.
Ready when: the daily checks in `operations-runbook.md` all alert rather than
being read.

## Next

**5. Player deletion and export.**
A player can delete everything keyed to their `player_id`, and export their
history. The design question is what happens to an excused day's audit trail
when the session that earned the excuse is deleted.
Ready to start when: the children and jurisdiction questions in
`privacy-and-data.md` have answers, because they change what deletion must
guarantee.

**6. Account merge, as a player's choice.**
`MergeRequired` is raised today rather than guessed at, which is correct and is
also a dead end for the player who hit it. The decision is that the player
chooses which history continues, shown each side's games, streaks and day
counts, with the unchosen history retained rather than deleted so a wrong choice
is recoverable.
Ready when: the choice can be deferred without locking either account, and what
was chosen is recorded.
Costs more than the alternatives did: this is a player facing flow, not a rule.

**7. The cartridge catalogue.**
Listing, description, and the add flow, in the client. Needed before any third
party cartridge is worth certifying, because there is nowhere to put it.

**8. The first outside cartridge.**
Written by somebody outside the project, certified, listed, played. This is the
success criterion for the whole cartridge model and it cannot be faked by a
first party game pretending.
Ready when: `game-developer-guide.md` is enough for somebody to ship from, which
will be learned by them failing to.

**9. Protocol specification for the sandbox transport.**
Specified now, implemented later, which is already the decision. Keeping the
specification current while more games are written is what keeps the boundary
types honest.

## Later

**10. Isolation, actually built.**
Subprocess or WASM, opt in per distribution, with a sandbox that makes clocks,
randomness, network, filesystem and the environment absent rather than
discouraged, and compute bounded by an instruction budget.
Trigger: the arrival of an author the project does not want to review. Somebody
decides that; it is not a threshold crossed quietly. The measured analysis is in
`docs/external-games.md`, and its conclusion is that WASM is the cheaper runtime
and the more expensive adoption, with the authoring language being the whole of
the decision.

**11. Locales beyond English.**
A game can declare a locale today and the platform has no blocklist for it, so
it cannot be certified there. Closing that is a platform gap, not an author's
problem.

**12. Leaderboards, if ever.**
Not planned. Recorded because it is the feature that would make two currently
accepted gaps matter: ledger plausibility and collusion. Neither is worth
building before a leaderboard exists, and both are preconditions for one.

**13. Resource budgets in process.**
The engine has no timeout and no memory limit, and candidate consumption,
payload size, presentation, hints and solution lists are unbounded. The
in-process bound can only ever be an approximation, which is why the real answer
is item 10 and this is a mitigation rather than a fix.

## Not on the roadmap, deliberately

- Multiplayer, head to head, or any real time play.
- A social feed, following, or player to player messaging.
- Player authored cartridges from inside the app.
- Advertising or sponsored content.
- A web client as a product surface. The browser shell reads published manifests
  and stays a reading surface.

## Undecided enough to not be scheduled

Monetisation is decided as free for now, which is a decision with a deadline
rather than a permanent answer: it is revisited before the first outside
cartridge is certified, because the terms an author ships under cannot be
changed on them afterwards. Until then there is no revenue share in
`developer-terms.md`, no purchase mechanism in the requirements, and no question
of what a paid cartridge would mean for the five slot cap.

Whether a third party cartridge may ship a curated content file is the one
question still genuinely open. The analysis, and the four conditions
recommended, are in `open-questions.md`.
