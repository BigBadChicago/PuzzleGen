# Platform requirements

Requirements, not a feature list. Each is written so that it is either met or
not met, and each says where it stands against the code on `main`. `MUST` is a
requirement for first release; `SHOULD` is a requirement whose absence is a
known, accepted gap; `LATER` is out of scope for first release and recorded so
it is not rediscovered as a surprise.

Three layers have requirements: the player's client, the platform service, and
the cartridge contract. They are kept separate because the client is thin on
purpose and most of these requirements are not its job.

## 1. The player's library and slots

| id | requirement | state |
|---|---|---|
| L1 | A player MUST hold a library of at most **5** slots, each holding one cartridge. Five is a platform constant, not per player, and is separate from the operator's generation budget in L6. | decided, not built |
| L2 | A player MUST be able to add a cartridge to a free slot, and remove one from an occupied slot, at any time, without contacting support. | not built |
| L3 | Removing a cartridge MUST keep that game's play history, streak and best scores. Re-adding it MUST restore them. | not built; session records are keyed by game so the data survives |
| L9 | Removing a cartridge MUST erase the cartridge's own content and anything it held, and MUST NOT touch the player's play history. Two different owners: the cartridge owns its content, the platform owns the player's record. | decided, not built |
| L4 | Adding a cartridge with no free slot MUST refuse and name which cartridge would have to be removed, never silently evict. | not built |
| L5 | A cartridge the platform has deactivated MUST remain in a player's slot, readable in history, and marked as producing no new days. | not built |
| L6 | The platform MUST bound how many games it generates days for, separately from the player's slot count. | built: `DEFAULT_MAX_ACTIVE = 4` in `puzzlegen/engine/registry.py` |
| L7 | Activation and deactivation MUST be recorded against a day key, so "which games ran that day" is answerable after the fact. | built: `ActivationEvent` |
| L8 | A player SHOULD be able to reorder their slots. | not built |

The distinction in L1 against L6 is the one most likely to be collapsed by
mistake. L6 is the operator's generation budget and already exists in code. L1
is a product rule about one person's attention and does not exist yet. A single
number serving both would mean that listing a fifth game forces every player to
drop one.

## 2. The daily cycle

| id | requirement | state |
|---|---|---|
| D1 | Every active game MUST have exactly one published day per day key. | built |
| D2 | A published day MUST be immutable. A correction is a retraction plus a new publication, never an edit. | built: `puzzlegen/engine/retraction.py` |
| D3 | A day MUST NOT be published unless it passed the game's declared verification contract. | built: `puzzlegen/engine/certify.py`, `release.py` |
| D4 | A day MUST record the snapshot hash and every component version that could change its bytes. | built: `puzzlegen/core/versions.py`, manifest format 2 |
| D5 | The same day key, snapshot and version set MUST regenerate the same bytes. | built and checked in CI for the symbolic games |
| D6 | Days MUST be generated ahead of time, not on demand at a player's request. | built: generation is a dispatched build |
| D7 | A retracted day MUST NOT count against the streak of a player who played it. | built: `RestorationService` |
| D8 | The platform MUST be able to publish a replacement day for a retracted day key. | built |
| D9 | A player whose day was retracted MUST see an in-app notice on next open, carrying the reason and what changed. No push notification. | decided, not built |

## 3. Play, scoring and sharing

| id | requirement | state |
|---|---|---|
| P1 | A player MUST be able to play each held game's current day once. | built at the service layer |
| P2 | Grading MUST be performed where the answer is not exposed to the client. | built: server grading via `session_service.py` |
| P3 | Every move MUST be recorded in a ledger, under the scoring version in force. | built: `SESSION_SCHEMA_VERSION`, `SCORING_VERSION` |
| P4 | A transcript recorded under one scoring version MUST NOT be silently regraded under another. | built |
| P5 | A hint MUST be derived from the elimination the generator performed, where the game supplies a witness. | built: `witness.py`, protocol 1.2.0 |
| P6 | A hint MUST be charged at the cost the player experiences, not at an authored cost. | built |
| P7 | Share text MUST pass a leak check before it leaves the platform. | built: `ShareService`, `ShareLeak` |
| P8 | Share text MUST use only the vocabulary the game declared. | built: `share_tokens` on the descriptor |
| P9 | A streak MUST be per game, and the identity MUST be shared across games. | built |

## 4. Offline and caching

| id | requirement | state |
|---|---|---|
| O1 | The client MUST cache a rolling five day window: today, two past days, two ahead. | specified, client not built |
| O2 | The window MUST refresh after a game is played while connected, and otherwise on the next connection. | specified, client not built |
| O3 | A corrected day MUST overwrite a cached day on the client, with no prompt. | specified, client not built |
| O4 | A board played offline MUST be graded on reconnection, and the grade MUST be the server's. | specified, client not built |
| O5 | A client MUST refuse a cached day whose puzzle format version it cannot read, rather than rendering it partially. | specified, client not built |
| O6 | Cached days ahead of today MUST be stored encrypted and MUST NOT be readable as puzzles before their day key. | decided, not built |
| O7 | Each cache refresh MUST deliver the unlock keys for the whole cached window, not for one day, so a player offline across a day boundary can open a day they already hold. | decided, not built |

O6 and O7 are one decision. Caching two days ahead and keeping them unreadable
until their day arrives are in tension, and the resolution is to cache them
encrypted with window wide keys: the offline guarantee survives, and reading
tomorrow's board early costs more than curiosity. It does not defeat a
determined person with their own device, and it is not trying to.

## 5. Identity and accounts

| id | requirement | state |
|---|---|---|
| I1 | A player MUST be able to play with no account. | built: tier 1 `player_id` |
| I2 | The id used as a primary key MUST be distinct from the credential the client holds. | built: `player_id` against `return_key` |
| I3 | A credential MUST be stored only as a keyed hash. | built: `hmac_sha256(pepper, key)` |
| I4 | Identity material MUST NOT come from the deterministic generator. | built, and asserted by an invariant test |
| I5 | A player MUST be able to hold several device credentials, labelled, and revoke one. | built: capped at ten, revoked rather than deleted |
| I6 | Binding a provider subject already bound to another player MUST refuse rather than repoint. | built: raises `MergeRequired` |
| I7 | On `MergeRequired`, the platform MUST ask the player which history continues, showing each side's games, streaks and day counts. | decided, not built |
| I8 | The history the player did not choose MUST be retained rather than deleted, so a wrong choice is recoverable. | decided, not built |

## 6. The cartridge contract

| id | requirement | state |
|---|---|---|
| C1 | Installing a distribution that declares the `puzzlegen.games` entry point MUST add the game; uninstalling MUST remove it. | built |
| C2 | The platform MUST hold no list of games and name none of them. | built, and asserted by an invariant test |
| C3 | The engine MUST refuse a cartridge whose protocol major version differs from its own. | built |
| C4 | The engine MUST refuse, at discovery, a cartridge whose protocol minor is below what play requires. | built |
| C5 | A cartridge MUST declare its uniqueness contract and verification completeness, and the engine MUST check results against the declaration. | built |
| C6 | A cartridge MUST pass the accessibility gate before a session can be started against it. | built: `accessibility.py` |
| C7 | A third party cartridge MAY ship a curated content file where no declaration can express its content, under C10 to C13. A declaration remains the default. | decided, not enforced in code |
| C10 | A shipped content file MUST be reviewed at certification as content, against `content-policy.md`, including the strings its generator can reach. | decided, not built |
| C11 | A shipped content file MUST be pinned to the cartridge version, and its hash MUST be recorded in every manifest generated from it. | decided, not built |
| C12 | A shipped content file MUST be served by the platform and read only at runtime. A cartridge MUST NOT write to it, and nothing a cartridge produces MAY persist on a player's device. | decided, not built |
| C13 | Every approved content file MUST be logged as a missing generator, naming the content and why no declaration expresses it. | decided, not built |
| C8 | A third party cartridge SHOULD run isolated from the host process. | staged; see `docs/external-games.md` |
| C9 | Compute MUST be bounded by an instruction budget rather than a wall clock, where isolation exists. | decided; in-process bound is labelled an approximation |

## 7. Operations

| id | requirement | state |
|---|---|---|
| R1 | A failed generation for one game MUST NOT block publication for the others. | built: per game runs |
| R2 | The operator MUST be alerted when an active game has no published day for the next day key. | not built |
| R3 | Retracting a day MUST produce a preview of what it affects before it is applied. | built: `RetractionService.preview` |
| R4 | A build artifact a document cites MUST be rebuilt rather than hand updated. | built as a practice, checked in CI |
| R5 | Server logs MUST be kept 30 days. A value that will matter beyond that MUST be promoted into a record rather than left in a log. | decided, not built |

## Out of scope for first release

- Multiplayer or head to head play.
- Any in app purchase or subscription mechanism. The platform is free for now, and that is revisited before the first outside cartridge is certified, not after.
- Player authored cartridges from inside the app.
- Localisation beyond the locales a game declares in `supported_locales`.
- A web client. The browser shell consumes published manifests as static JSON
  and is a reading surface, not the product.
