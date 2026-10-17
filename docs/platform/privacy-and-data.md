# Privacy and data

What the platform collects, what it keeps, what it never has, and why each
answer is what it is.

**Needs legal review before publication.** The technical statements are accurate
against the code; the regulatory statements are a maintainer's reading.

## The short version, for a player

You can play without an account. We keep your play history so your streak works.
We store the key your phone uses to identify itself only as a hash, so a copy of
our database cannot impersonate you. Games you install cannot see anything about
you. We do not sell anything and there are no advertisers.

## What is collected

| data | why | where |
|---|---|---|
| `player_id` | the primary key on every session, score and streak record | every session record |
| `return_key` | the credential your device presents to resume being you | stored only as `hmac_sha256(pepper, key)` |
| device label, per key | so you can tell which device to revoke | identity store |
| move ledger, per session | grading, scoring, replay, and hint costing | session record |
| session timing | scoring | session record |
| day key and game id | which puzzle you played | session record |
| streak and best score, per game | the feature itself | score store |
| provider subject, if you sign in | linking a device to an account | identity store |

## What is deliberately not collected

- No email, name, phone number or date of birth unless you choose to sign in
  with a provider, and then only that provider's subject identifier.
- No advertising identifier, no device fingerprint, no location.
- No contact list, no photos, no microphone, no camera.
- No analytics SDK from a third party.
- Nothing at all is collected by a game. A cartridge is handed a content port
  and a seeded generator. It has no player identifier, no session, no network and
  no storage, and the platform's invariants assert it imports nothing that would
  give it any of those.
- Nothing a cartridge produces persists on a player's device. Where a cartridge
  ships its own content file, that file is served by the platform and is read
  only, and removing the cartridge erases the cartridge's content. What removal
  never touches is the player's play history: the cartridge owns its content,
  the platform owns the player's record, and answer L3 depends on that
  separation.

## Two design decisions that carry the privacy properties

**The identifier and the credential are different strings.** `player_id` is the
key on records, safe to log and to place in a record. `return_key` is a bearer
credential the client holds. Collapsing them, which the shorthand "the client
presents its id" invites, would make every debug dump an account takeover.

**Credentials are stored only as a keyed hash.** The pepper arrives in an
injected secrets object; no module in the package reads the process
environment. A dump of the identity store cannot impersonate anybody.

A third, smaller, worth stating because it reads as a quirk otherwise: a revoked
device key is marked revoked rather than deleted. The question after a suspected
leak is which devices ever existed, and a deleted row cannot answer it.

## Retention

| data | kept |
|---|---|
| session records and move ledgers | indefinitely, because they are your history and your streak depends on them |
| revoked device keys | indefinitely, as hashes, for the reason above |
| a retracted day's sessions | kept, marked as excused, so the streak restoration is auditable |
| server logs | 30 days |

Indefinite retention of play history is a deliberate choice and a cost: it is
what makes a removed cartridge's history survive and come back when the
cartridge does. It is also the thing a deletion request collides with, which is
the next section.

Thirty days for logs is safe only because of the rule beside it: anything that
will matter in three months is promoted into a record rather than left in a log.
A milestone, a streak event, a retraction, a merge choice. When a value will be
needed later, writing it only to a log is the bug and promoting it is the fix.

## Deletion and export

Not built. Stated as a requirement rather than a claim:

- A player MUST be able to request deletion of everything keyed to their
  `player_id`, and the platform MUST be able to perform it without breaking
  another player's record.
- A player SHOULD be able to export their play history in a readable format.
- Deletion MUST NOT be satisfiable by deleting the credential alone, because the
  history is the personal data.

The complication to resolve before building it: a published day is immutable and
a session record is not part of a published day, so deletion of sessions is
clean. Streak restoration audit trails reference sessions, so the design needs
to decide whether an excused day survives the deletion of the session that
earned the excuse.

## Children

**The app is declared general audience**, with the privacy posture kept strict
enough that the distinction mostly does not matter: no advertising identifier,
no third party analytics, no location, nothing collected by a cartridge, and no
personal data beyond what a streak needs. A daily puzzle app has child players
whether or not it is aimed at them, and the defence against that is collecting
nothing worth worrying about rather than a declaration.

What the declaration does not settle, and what still needs a lawyer rather than
an engineer: the market by market obligations, what they mean for provider sign
in, and whether any target market requires an age gate regardless. That review
happens before the first store submission, not after.

## Third parties

There are none, today, in the data path. No analytics vendor, no advertising
network, no crash reporter, no third party authentication beyond whatever
provider sign in is chosen.

Each one of those added is an amendment to this document, and the rule is that
the amendment lands before the dependency does.

## Security of the data

See `security-and-threat-model.md`. The summary relevant here: credentials are
hashed with an injected pepper, identity material never comes from the
deterministic generator, and an invariant test enforces that separation because
reproducibility is the one property a credential must not have.
