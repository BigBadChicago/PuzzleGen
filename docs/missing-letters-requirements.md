# missing_letters — Requirements

Specification, plus the content the specification turned out to need. The
coverage measure this doc asked for exists as
`tools/measure_letters_coverage.py`, and section 6 is what it found and what
was done about it. Sections 1 to 5 are unmeasured design; section 6 carries
numbers a tool printed.

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

## 6. The content, and why the game has its own taxonomy

Measured, not reasoned. Build A2, medium frequency, 6 to 12 tiles by 2 to 4
blanks, `tools/measure_letters_coverage.py`.

### 6.1 Membership binds, naming does not

`letters_unplaceable` was zero in every cell. The letter matching this doc
spends four sections on is never what refuses a board; the constraint section
1.1 implies and nobody had checked is simply whether a category has members a
board can hold. At medium frequency the whole lexicon had exactly one category
with six or more eligible members, and at easy none anywhere. Two categories
were playable in one cell, `baby grand` and `police van`, neither a name a
solver would reach for.

So shortening names, which 4.3 is about, buys nothing on its own: there was
nothing with enough common members to be worth naming. The route taken instead
was to pick categories a player recognises, get the common words into each, and
settle the name afterwards. The first batch stopped at eight members; 6.5
records why the ceiling moved to twelve and what that opened.

### 6.2 Why the lexicon cannot supply them

A WordNet category's members are its hyponyms, and the frequency filter the
game applies cuts across that structure rather than along it. `edible fruit`
has dozens of hyponyms and few of them are words anyone writes; the eight that
are sit at four different depths under three different parents. Nothing is
wrong with the lexicon. It is organised by what a word means, and a board needs
a set organised by what a player knows, which is a different cut and one no
import can infer.

Note also that the medium band is `COMMON` and `UNCOMMON`, zipf 3.0 to 5.0, so
a *very* common word is ineligible at medium. `dog`, `car` and `water` are all
too common to be tiles there, which is another reason a frequency filter over a
lexicon subtree does not leave a usable set behind.

### 6.3 The curated taxonomy

`games/letters/content/letters.curated.json`, imported with
`--curated games/letters/content/letters.curated.json:letters`, which is the
seam `tools/build_snapshot.py` documents for exactly this: a game's own
taxonomy, optional and repeatable, colliding with neither `wordnet` nor
`overlay` because category identity already includes the taxonomy.

Thirty categories, six to twelve members each, 280 entities: sixteen hold
twelve, four hold eight, three hold seven and seven hold six. A category holds
twelve only where twelve common members exist, which is why the sizes are
uneven.

Entity identity is by lemma, so a member the graph already holds is the same
entity with one more membership rather than a duplicate. Measured: the entity
total goes from 16,710 without this file to 16,842 with it, so 132 members were
new and 148 merged. Separately, 143 of the 280 carry a membership outside this
taxonomy -- 103 in `wordnet`, 37 in both `wordnet` and `overlay`, 3 in
`overlay` alone -- and the remaining 137 are members of a `letters` category
and nothing else. The two figures differ because merging is by lemma and does
not require the entity it merges with to have a membership of its own. That
entity total is the only figure game 1 measures that this file moves.

Three invariants decide whether a category can be a board, and
`tests/unit/test_letters_categories.py` pins each: six to twelve members, a name
of three to seven letters, and a name its own members can spell at the
two-blank floor. The third caught three categories as authored. `fruit` had no
member holding an f, an i or a t; `garment` none holding a g, an m or an n;
`vehicle` one e and no h. Each was fixed by changing members, not by changing
the name, which is the route in 6.1 applied to its own output.

Two categories were renamed, and only after their members were settled:
`gemstone` to `gem` and `currency` to `money`, both eight letters where the
smallest board admits seven.

### 6.4 What the batch measures

Re-measured on the same build with the file in, medium frequency:

| tiles | blanks | playable | too long | too few members | unplaceable |
|---|---|---|---|---|---|
| 6 | 2 | 30 | 5288 | 1760 | 0 |
| 6 | 3 | 29 | 4574 | 2403 | 0 |
| 6 | 4 | 25 | 3764 | 2998 | 0 |
| 8 | 2 | 20 | 3764 | 3294 | 0 |
| 8 | 3 | 19 | 2992 | 3995 | 0 |
| 8 | 4 | 16 | 2366 | 4405 | 0 |
| 10 | 2 | 16 | 2366 | 4696 | 0 |
| 10 | 3 | 15 | 1836 | 5155 | 0 |
| 10 | 4 | 12 | 1440 | 5335 | 0 |
| 12 | 2 | 16 | 1440 | 5622 | 0 |
| 12 | 3 | 15 | 1096 | 5895 | 0 |
| 12 | 4 | 12 | 838 | 5937 | 0 |

Every cell has playable content. All thirty work at six tiles and the two-blank
floor, the twenty with eight or more at eight tiles, and the sixteen with twelve
at both ten and twelve.

Ten and twelve report the same counts, and that is the content's shape rather
than a coincidence: no category holds ten or eleven members, so the sixteen
that fill a board of twelve are exactly the sixteen that fill a board of ten.
A category sized between the two would separate the columns, and none exists
because the eleventh and twelfth common member arrive together or not at all.

The refusal columns are almost all WordNet, unchanged from 6.1 and left in the
table because they are the comparison: 7,050 lexical categories yield nothing
a player would want, and thirty curated ones yield a board at every size.

### 6.5 Twelve members, and the two largest boards

The first batch capped members at eight, which left ten and twelve tile boards
measuring zero: a board needs at least as many eligible members as it has
tiles, so eight members cannot seat ten. That cap was a decision rather than a
limit of the content, and the owner decided against it, so sixteen categories
were extended to twelve members.

The ceiling is now twelve because twelve is the largest board the tool
measures: a thirteenth member could never become a tile. It is a ceiling and
not a target. Fourteen categories stayed below it because their common
vocabulary runs out first, and reaching for a thirteenth word in `liquor` or
`bone` would mean reaching for a word a player does not know, which is the
lexicon subtree this taxonomy exists to avoid.

Which sixteen was decided by the vocabulary, not by preference: `bird`, `drink`,
`fabric`, `fish`, `flower`, `fruit`, `garment`, `gem`, `insect`, `island`,
`mammal`, `metal`, `shape`, `sport`, `tree`, `weather`. Each has at least
twelve members in the medium band, most of them more, so the twelve chosen are
the most recognisable rather than the only ones available.

Extending them did not break the spelling invariant, but it did need the same
care: `sport` gained `polo` because none of its other members holds a p, and
`flower` gained `sunflower` for its f and w. The invariant caught neither,
because both categories were already within their blank budget; they were
picked to leave the budget slack rather than spend it.

### 6.6 What this leaves open

The curated taxonomy is thirty categories, which is not a year of daily
puzzles. It is enough to build the generator against, and the shape of a batch
is now established, so the next batches are curation and not design.

Section 4.3's shorter-synonym substitution has no caller yet. `gem` and `money`
were decided in the file rather than through `SurfacePolicy.preferred`, because
a curated category can simply be named the short name; the substitution matters
for lexical categories, whose names are not ours to choose.
