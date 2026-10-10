# missing_letters — Requirements

Specification only. No coverage tool exists for missing_letters yet; unlike the
game 1 coverage work, nothing here is measured. A coverage measure analogous to
`measure_coverage.py`, counting how many categories yield a legal board at each
difficulty, is the natural next artifact before implementation.

Companion to this spec: the surface form subsystem (section 2), an engine-level
tool missing_letters is the first client of.

## 1. The game

A daily lens on the same knowledge graph as game 1, but reasoning about a word's
letters through the surface form subsystem rather than only its meaning.

### 1.1 The board

Every tile is a member of one hidden category; nothing on the board names that
category, because the category is the answer. Some tiles carry one marked
character each; others carry none. The solver gathers the marked characters,
rearranges them freely, and uses them plus blank slots to spell the category's
own name.

### 1.2 Worked example

Category `fish`, tiles `salmon, trout, herring, bass, halibut, carp`. Marks: h
on herring, s on bass, i on halibut. Those letters free-anagram to `ish`, and
one blank supplies the f to spell `fish`. This example has a single blank, so it
is a deliberately degenerate illustration, below the two-blank floor, not a
legal board.

### 1.3 The solve

The answer is the thing the tiles already are, reached from two directions. The
solver can read the category semantically (these are all fish) and can build it
letter by letter from the marks. The blank is the insight: a letter the board
withholds, which the solver supplies from recognizing the category rather than
from any tile. This separates it from game 1, which hides a grouping to find
among the tiles; missing_letters hides a grouping to spell from the tiles.

## 2. The surface form subsystem

A first-class engine subsystem, not a helper. It defines what the letters of an
entity are, and it is a tool the games call. missing_letters is its first
client, but it is built for the engine: any game reasoning about spelling,
length, shared affixes, or letter positions needs one shared definition rather
than each game inventing its own.

### 2.1 Why a subsystem and not a utility

A helper would hide decisions that are load bearing and shared. Promoting it to
a subsystem makes it own them explicitly and makes the same answer available to
every game and to curation tooling.

### 2.2 Responsibilities

- **Canonical letter sequence.** Define the letters of an entity: case folding,
  diacritic handling (whether an accented letter folds to its base or stays
  distinct), and whether spaces and hyphens are kept or dropped.
- **Multi-word collapse.** Decide how a multi-word name reduces to a comparable
  sequence, so a name like `food fish` has a defined letter form.
- **Surface form selection.** Choose which of an entity's several names counts:
  the primary lemma, or a shorter true synonym where that is the common name
  (e.g. `bowed instrument` for `bowed stringed instrument`). A surface form
  decision, not a game decision, so it lives here.
- **Stable, inspectable mapping.** Produce a deterministic entity-to-letters
  mapping, so a board regenerates identically years later and a curator can see
  why a category was ruled playable or not.
- **Precomputed form for cost.** Character-level queries run per candidate on
  every daily build, so the subsystem owns an index or precomputed
  representation. This is the cost a helper would never justify and a subsystem
  must.

### 2.3 Boundary with the content graph

The knowledge graph is built for semantic questions and has no character-level
index. The surface form subsystem sits beside it, consumes entities through the
existing member queries, and adds no new operation to the content service. Games
that need letters call the subsystem; the graph stays as it is.

## 3. Settled rules

- **Free anagram.** Marked characters are unordered; the solver rearranges them.
  No fixed reading order.
- **One mark per tile maximum.** A tile carries zero or one marked character,
  never two.
- **Not every tile is marked.** Some tiles deliberately carry no mark.
- **Blanks scale with difficulty.** Harder means more blanks and longer category
  words; easier means fewer blanks and shorter words.
- **At least two blanks, always.** A legal board never has fewer than two,
  regardless of the single blank in the fish example.
- **Name.** The game id is `missing_letters`.

## 4. Decided design questions

Each was open and is now settled. Each carries a build consequence.

### 4.1 Uniqueness contract: SOUND_INCOMPLETE with a stated search bound

The game does not promise it checked every possible answer. It searches up to a
declared bound and reports that bound. This allows harder boards and far more
usable categories, and admits a small chance that a second valid answer exists
undetected. The weaker promise is recorded on every board, forever.
**Consequence:** the word list the verifier checks against is a real dependency
and must be pinned, because which list is used decides which boards pass.

### 4.2 A marked character must occur in its own tile's word

Every mark is a real letter of the tile that carries it (h in herring, s in
bass, i in halibut). This rules out arbitrary marks, which would be decoration.
**Consequence:** every mark is also a small clue to its tile, and feasible
categories are scarcer, because the category's letters must actually be findable
among its members' spellings.

### 4.3 Multi-word category names permitted, common names favored

Multi-word targets are legal, but the game favors common names, and a cumbersome
name may be reduced to a shorter true synonym, e.g. `bowed stringed instrument`
to `bowed instrument`. **Consequence:** the surface form subsystem must carry
alternative names, a notion of which are common, and the ability to substitute a
shorter synonym. A second reason the subsystem is engine-level: the substitution
is a surface form decision other games would also want.

### 4.4 Blank count conflict: leave a could-have-been-marked tile unmarked

Difficulty sets a target blank count, but the data also forces a count, because
a category whose letters cannot be fully distributed across its members leaves
some letters with no tile. When the target exceeds the forced count, the
generator leaves a tile deliberately unmarked that could have carried a letter,
manufacturing the extra blank. This gives exact difficulty control.
**Consequence and accepted tradeoff:** the mark pattern no longer tells the full
truth about what was possible, and a determined solver watching many days could
detect manufactured blanks. The generator must record, per board, which blanks
were forced by the data and which were manufactured, for reproducibility and
later audit.

## 5. Where the work lives

The character-level constraint (can this category's name be formed from letters
distributed one per tile, with the rest as blanks) is computed in the game
plugin, over members pulled through existing member queries and letters supplied
by the surface form subsystem. It adds no new operation to the content service,
keeping the expensive character work in the one game that needs it while the
shared letter definition stays in the subsystem.
