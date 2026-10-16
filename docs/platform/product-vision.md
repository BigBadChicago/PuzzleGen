# Product vision

## What PuzzleGen is

A mobile platform where a person plays the daily puzzles they chose, and where
the puzzles themselves come from anywhere.

Three things are true of it at once, and the product only makes sense when all
three hold:

1. **One body of knowledge, many games.** Facts, relationships, categories and
   vocabulary are governed once, in one graph, and every game reads from it.
   A game is a lens on that body, never an owner of it.
2. **Every game is a cartridge.** A game ships as its own distribution, is added
   and removed without touching the platform, and may be written by somebody the
   platform's maintainers have never met. The platform holds no list of games
   and, by an enforced test, names none of them.
3. **Every published puzzle is proved before it ships.** The engine will not
   publish a board it has not verified against the game's own declared
   contract. Solvable is never sufficient.

## Who it is for

**The daily player.** Somebody who already plays one or two daily puzzles and
would play four if they were in one place, kept one streak, and did not each
demand a separate account. They want today's puzzles to work on a train with no
signal, and they want the share text to look right when they paste it.

**The puzzle author.** Somebody with a good idea for a daily puzzle and no
appetite for building a content pipeline, a graph, a verifier, a scoring system,
an accessibility layer and a share format. They write the lens. The platform
supplies everything else and tells them, before they ship, whether their game
produces thirty days that regenerate identically.

**The operator.** The person running the platform, who needs to know every
morning that every active game produced a valid day, and who needs a way to
withdraw a day that was wrong without punishing the people who played it.

## The cartridge bargain

The cartridge is the whole product idea, so it is worth stating as a bargain
with two sides.

The platform promises a cartridge: a governed graph to draw content from, a
reproducible generation run, verification, difficulty banding, sessions,
scoring, streaks, hints derived from real eliminations, share artifacts with a
leak check, an accessibility layer, and distribution to players who already
trust the shell.

The cartridge promises the platform: a declared contract it does not violate, a
thirty day run that regenerates byte for byte, grading that is correct, an
honest accessibility declaration, and no reach for anything outside the ports it
was handed.

Neither side can be taken on trust alone, which is why the platform checks what
is checkable (regeneration, contract conformance, the accessibility gate, share
vocabulary) and records what is not (grading correctness, the accessibility
promise) so that a failure is attributable to the party that caused it.

## What a player does

- Holds a library of a fixed number of slots, each occupied by one cartridge.
- Adds a cartridge from the catalogue, which occupies a slot.
- Removes a cartridge, which frees the slot and keeps the history.
- Plays each held game's day once per day.
- Keeps one identity, one streak per game, and one share format across all of
  them.
- Plays offline against a small rolling window of cached days.

## Non-goals

Stating these matters more than usual here, because the optionality doctrine this
project runs on can be misread as "build for every future".

- **Not a general game engine.** There is no physics, no real time loop, no
  rendering engine. The unit is a daily puzzle with a verifiable solution.
- **Not a social network.** Sharing is a text artifact a player pastes
  elsewhere. There is no feed, no following, no comments.
- **Not a user generated content firehose.** A cartridge is a reviewed,
  signed distribution, not an upload. The volume this platform is designed for
  is dozens of good games, not thousands of unreviewed ones.
- **Not a live service with changing rules.** A published day is immutable. If
  it was wrong it is retracted, never edited.
- **Not an engine that can be made to work without the graph.** A game that
  needs a content shape no platform generator produces needs a platform change,
  and that is deliberate: the governed step is where quality comes from.

## How we will know it worked

- A game written by somebody outside the project is listed, played, and needed
  no platform change to add or to remove.
- Five or more games share one snapshot, and at least two of them are not word
  games, which is already true in the repository.
- A day is retracted and no player loses a streak they earned.
- A player who has not opened the app in two days still has today's puzzles
  cached and playable with no signal.
