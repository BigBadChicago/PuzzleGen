# Developer terms

The author's side of the bargain, in plain words.

**This is a draft written by the maintainer, not legal advice, and it has not
been reviewed by a lawyer.** It is here because a platform asking somebody to
write a game for it owes them a statement of the deal, and because writing the
deal out reveals the decisions nobody has made. Where a decision is missing,
this document says so rather than filling it in.

## 1. What you are agreeing to

You are submitting a cartridge: a game that runs inside the PuzzleGen platform,
reads content from the platform's governed graph, and is distributed to players
through the platform.

## 2. What the platform gives you

- A governed body of content to generate from, and the snapshot machinery that
  makes a generation reproducible.
- Verification, difficulty banding, sessions, scoring, streaks, hints from your
  witness, share artifacts, and the accessibility layer.
- Distribution to the platform's players.
- Automated certification feedback: if your game fails a check, you are told
  which check and why.
- Notice before any protocol change that would stop your game loading.

## 3. What you give the platform

- A licence to distribute your cartridge to players, for as long as it is
  listed, and to retain the published days it produced after it is not.
- The right for the platform to run your code, inspect it, and refuse it.
- Accuracy in your declarations. The contract on your descriptor is what the
  platform holds you to.

## 4. What you keep

You keep the copyright in your cartridge's code. The licence in clause 3 is for
distribution, not an assignment.

Content remains the platform's. A declaration draws on the graph; it does not
give you rights in it.

## 5. The rules you are agreeing to follow

- Your game uses no clock, no network, no filesystem, no environment and no
  randomness other than the seeded generator it is handed.
- Your game does not reach past the ports it is given, including by any path
  the platform's invariants happen not to catch. A gap in enforcement is not a
  permission.
- Your verifier proves the contract you declared.
- Your grading is correct, to the best of your ability, and you will fix it when
  it is not.
- Your accessibility declaration is honest.
- Your boards respect `content-policy.md`, and if your game arranges letters,
  you filter your reachable output.
- Your content arrives as a reproducible declaration.
- You do not collect anything about a player. You have no mechanism to; adding
  one would be a violation of the clause above about ports.

## 6. What gets your game pulled

Graded, because the consequences differ.

**Deactivated** (no new days, players keep history): you stop maintaining it and
a protocol change leaves it unloadable; a quality problem the platform cannot
fix for you; a listing decision the platform reverses.

**Days retracted** (the days, not the game): boards that were wrong, unfair, or
in breach of the content policy.

**Revoked** (the signature is withdrawn and the cartridge stops working): your
game attempted to reach outside its ports; your game collected player data; your
declarations were knowingly false; your content was a deliberate breach of the
content policy. Revocation is the only case where a player's held cartridge
stops working, and it retracts affected days too, because a hostile cartridge's
boards cannot be assumed sound.

You are told which of these happened and why, through the contact you supplied.

## 7. Liability, stated honestly

The platform is provided as is. Nobody here is in a position to indemnify
anybody. If this platform ever carries money or a meaningful number of players,
this clause is the first one that needs a lawyer.

## 8. Changes to these terms

Changed terms apply to your next submission, not retroactively to a listed
cartridge. A listed cartridge keeps the terms it was listed under until it is
updated.

## 9. The decisions nobody has made yet

Listed rather than invented, because an invented answer here is worse than an
admitted gap:

- Revenue. The platform is free for now, so there is no revenue share to state.
  This gets decided before the first outside cartridge is certified rather than
  after, because an author who ships under free terms and is later handed
  commercial ones has a real grievance.
- Exclusivity. Whether a cartridge may also ship elsewhere is undecided.
- Trademark and naming. Whether an author may use the platform's name in
  describing their game is undecided.
- Dispute resolution and governing law. Undecided.
- Whether a cartridge may be transferred to another author. Undecided.
