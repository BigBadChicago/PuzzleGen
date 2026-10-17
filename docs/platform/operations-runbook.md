# Operations runbook

Running the daily pipeline, and what to do when it breaks.

Written for one operator, because there is one. Where a procedure assumes
somebody on call, it says so instead of pretending.

## The daily cycle

Builds are dispatched, not run in a working session. Each workflow runs one task
of `scripts/ci.sh` and ends its log with a summary between
`===== SUMMARY =====` markers, also shown on the run page, so a result is a
dozen lines rather than a build log.

| workflow | runs on | does |
|---|---|---|
| `tests` | push to main, PRs, dispatch | platform alone, then every suite, on 3.11 and 3.13 |
| `coverage artifacts` | content changes, dispatch | rebuilds the coverage baseline and fails if committed files differ |
| `snapshot` | dispatch only | snapshot with real vectors, then days; uploaded as an artifact |
| `symbolic snapshot` | symbolic game or platform changes, dispatch | rebuilds the symbolic snapshot and committed day runs, fails on any difference |

The same tasks run locally: `scripts/ci.sh tests`, `scripts/ci.sh coverage`,
`DAYS=5 scripts/ci.sh snapshot`, `scripts/ci.sh symbolic`.

## Daily checks

Today these are a person looking. The alerting in the right hand column is the
requirement, not the state.

| check | how | should alert |
|---|---|---|
| every active game has a published day for tomorrow | read the publication store | yes, and it does not |
| every published day carries a release certificate | `check_release` | yes |
| the day buffer is at least two days deep | read the publication store | yes |
| no game's generation failed | per game run status | yes |
| the snapshot in use matches the one the days were generated against | manifest hash | yes |

A failed generation for one game does not block the others, by design. One game
with no day for tomorrow is a single game missing a day, not an outage.

## Generating days

Days are generated ahead of time and in bulk, never on a player's request. Keep
at least two days of buffer, which is also what the client's rolling five day
window assumes: today, two past, two ahead.

If a game has no day for tomorrow and generation fails repeatedly, deactivate it
for the affected day keys rather than leaving a hole. A deactivated game shows
as producing no new days, which is a legible state; a missing day is not.

## Retracting a day

The one procedure with real urgency, because every hour a wrong board is live is
another cohort of players losing a streak to it.

1. **Confirm it is wrong.** Reproduce the claim against the published manifest.
   A board that is merely harder than its band is not a retraction; it is a
   calibration bug fixed forward.
2. **Preview.** `RetractionService.preview(ref_id)` names what the retraction
   affects. Read it before applying. This step exists because a retraction
   touches other people's streaks.
3. **Retract**, with a reason recorded.
4. **Restore.** `RestorationService` excuses the day for every affected player
   and restores streaks. Check the restored streak report; `changed()` and
   `describe()` say what moved.
5. **Verify.** `RetractionService.verify` reports anything inconsistent.
6. **Republish**, if a replacement day for that day key is wanted.
7. **Tell the players.** Every affected player sees an in-app notice on next
   open, carrying the reason and what changed. No push notification: a
   retraction is not urgent to the player, it is only confusing if unexplained.
   The notice mechanism is decided and not built.

`retract_many` exists for the case where a bad snapshot fact or a hostile
cartridge affects a run of days.

## Rotating the credential pepper

The pepper is injected, not read from the environment by the package, so
rotating it is a deployment change. There is no partial remedy: every stored
credential hash becomes unverifiable, which is a forced re-authentication on
every device.

Rotate only on a suspected pepper compromise. For a single leaked device
credential, revoke that device key instead, which is why keys are per device,
labelled, capped at ten, and revoked rather than deleted.

## Log retention

Server logs are kept thirty days. The rule that makes that safe is the one
beside it: a value that will matter beyond thirty days is promoted into a
record, not left in a log. Milestones, streak events, retractions and merge
choices are records. When a question three months from now can only be answered
from a log line, the fix is to write the record, not to extend retention.

## Incident classes

| class | example | first action |
|---|---|---|
| wrong board | unsolvable, or a solution count other than declared | retract that day |
| wrong content | a board breaching the content policy | retract, then fix the generator's filter |
| bad snapshot | a fact in the graph is wrong and days depended on it | `retract_many` for affected days, rebuild the snapshot, republish |
| cartridge misbehaving | reaching past its ports, or consuming the host | deactivate, then decide delist or revoke |
| cartridge hostile | deliberate | revoke, retract every affected day, treat the host process as compromised for the period it ran |
| identity store exposed | a dump | rotate the pepper; the dump alone cannot impersonate |
| pepper exposed | a leak of the injected secret | rotate, and accept the forced re-auth |
| generation failing | one game cannot produce a day | buffer absorbs it; deactivate for the day if it persists |
| client cannot read a day | a format version the client does not know | the client must refuse rather than render partially; ship the client update |

## The failure mode this project keeps having

Named here because it is an operational hazard, not only a code smell: **a
diagnostic that recomputes a value instead of reading what the engine actually
did.** Four instances so far, including a hand copied threshold that drifted and
a tool reading a drawn size rather than the engine's. When a tool and the engine
could disagree about a number, the tool imports the real constant or reads the
real output. When a document and an artifact could disagree, the artifact gets
rebuilt.

Operationally, the consequence is that a dashboard number which disagrees with a
manifest is a bug in the dashboard until proven otherwise.

Three more cautions that have bitten and will again:

- A lexicon named for a root it does not hold. Check `root` inside the file, not
  the filename.
- Never glob `content/seeds/wordnet-*.lexicon.json`. Name exact roots; a glob
  picks up an eight synset test fixture.
- `build/` is gitignored, so `git status --porcelain` says nothing about a
  committed artifact that was never committed. A check on a build artifact needs
  `git ls-files` too.

## Service levels, stated honestly

There is one maintainer and no on call rotation. Rather than publish numbers
nobody is accountable for:

- A wrong board is retracted as soon as the maintainer is aware and has
  confirmed it. This is the only commitment with real intent behind it.
- Everything else is best effort.
- Automated certification reports within one CI run.

When there is a second person, this section becomes real targets. Until then a
published uptime figure would be a fiction.
