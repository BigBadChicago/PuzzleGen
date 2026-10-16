# Glossary

Plain definitions for every term this project invented or uses in its own way.
Where a term names something in the code, the module is named.

**Board.** One puzzle as a player sees it on one day. The engine calls the
generated object a `Puzzle` (`puzzlegen/engine/plugin.py`); "board" is the word
used when talking about what is on the screen.

**Cartridge.** One game, packaged as its own installable distribution, that the
platform can add and remove without any change to the platform. Named for a
console cartridge because the relationship is the same: the machine supplies the
power and the screen, the cartridge supplies the game, and neither needs to know
the other's internals. See `cartridge-model.md`.

**Certification.** The checks a cartridge passes before it can be listed. The
central one is regeneration: the platform rebuilds the cartridge's committed
thirty day run and compares it byte for byte.

**Content declaration.** A few lines that say which slice of the graph a game
wants, written so the platform can reproduce the slice rather than trust a file.
A third party cartridge brings a declaration, not a curated data file.

**Day key.** The string that names one puzzle day, for example `2026-10-16`. It
is an input to generation, so the same day key and the same snapshot always
produce the same board.

**Difficulty band.** `EASY`, `MEDIUM` or `HARD`. A band is calibrated against
the boards the engine actually reaches, not against every candidate a generator
offers.

**Graph.** The shared, governed body of facts, relationships, categories and
vocabulary that every game reads. One body, many lenses.

**Lens.** A game, described from the platform's side. A game is a way of looking
at the graph. It never owns the knowledge it looks at.

**Manifest.** The published document for one day of one game. It records the
board, the snapshot hash, every component version, and a commitment to the
dependencies used, so a regenerated board that differs from its original is a
detectable fault rather than a mystery.

**Protocol version.** The version of the engine to cartridge contract,
`PLUGIN_PROTOCOL_VERSION`, currently `1.2.0`. The engine refuses a cartridge
whose major version differs, and refuses at discovery a minor below what play
needs.

**Retraction.** Withdrawing a published day because it was wrong. A retracted
day stops counting against the player who played it
(`puzzlegen/engine/retraction.py`).

**Slot.** One place in a player's library that one cartridge occupies. A player
holds a fixed number of slots and swaps cartridges in and out at will. Distinct
from the operator side cap (`DEFAULT_MAX_ACTIVE`), which bounds how many games
the platform generates days for.

**Snapshot.** An immutable, hashed build of the graph. A puzzle is generated
against exactly one snapshot, and the hash is in the manifest.

**Uniqueness contract.** What a game promises about how many solutions its
boards have, for example exactly one. The engine checks the promise against the
verifier's result rather than taking it on faith.

**Witness.** The elimination a generator already performed, kept rather than
discarded, so a hint can be derived from the real reasoning instead of authored
beside it (`puzzlegen/engine/witness.py`). A game declares whether it has one;
a game that searches for its boards genuinely does not.
