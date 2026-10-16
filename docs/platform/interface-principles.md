# Interface principles

What the client has to be, given what the engine already decided. Written
alongside ten mockups of different approaches, each of which is a different
answer to the same set of constraints.

The ten mockups are a design canvas, not a repository artifact; this document
is the record of what they were testing and what was learned from drawing
them. Nothing here is built.

## The constraint that shapes everything

**The platform holds many games and names none of them, so the client cannot
know what a board looks like.** A game returns a presentation model, not pixels,
and the client renders it. That has one consequence that governs every screen:

The client supplies the frame, never the board. Header, progress, hints,
mistakes, share, the slot library, the retraction notice, the accessibility
announcements: all of that is the client's and is identical across games. What
sits in the middle is the game's, and the client must not assume it is a grid, a
row, a word, a number or a picture, because the five shipped games are all five
of those.

A client designed around one game's board shape is a client that needs a
redesign for the sixth cartridge, which is the one thing the whole architecture
exists to avoid.

## What innovative has to mean here, and what it cannot mean

A daily puzzle is played in two to twenty minutes, once, by somebody who is
mostly standing up. The interface earns nothing by being novel at the level of
navigation or chrome, and loses a player every time it is.

So the room for invention is in three places, and only three:

1. **The reasoning.** Every other daily puzzle app hands out hints that somebody
   wrote next to the answer. This engine keeps the elimination its generator
   actually performed and charges a hint at what it costs the player. That is a
   real difference and nobody has drawn it yet. Mockup 5 is entirely about it.
2. **The shelf.** No daily puzzle app lets you swap the games themselves. Five
   slots, add and remove at will, history that survives removal: that is a
   product idea with no established interface, which means it is both the
   biggest opportunity and the easiest thing to get wrong. Mockups 2 and 3 are
   two different readings of it.
3. **The honesty.** A withdrawn day, a restored streak, a hint's real price, a
   difficulty band that was wrong. Every one of those is normally hidden. Shown
   plainly, they are the thing that makes a player trust a generated puzzle.
   Mockup 10 is about the correction notice.

Everywhere else, be ordinary on purpose.

## Nine rules the mockups were drawn against

1. **One board, one screen, no scrolling.** A player should never scroll to see
   the puzzle they are solving. This is the single hardest constraint and it is
   what kills most layouts: sixteen tiles, a header, a hint affordance, a
   mistake counter and a submit button do not fit a phone unless something is
   given up.
2. **Everything reachable with a thumb.** The actions live at the bottom, the
   board above them. A hint button in a top corner is a hint button nobody
   presses.
3. **Every state has a shape, not only a colour.** This is already enforced in
   code: two states sharing a symbol or a label is refused at declaration time.
   In the interface it means a correct tile and a wrong tile differ in outline
   weight or mark, never in hue alone, and the share artifact carries the same
   distinction.
4. **Targets at 44px, and the board sized from the count.** A sixteen tile grid
   on a 390px screen gives roughly 86px columns, which is comfortable; a game
   that wants twenty-five tiles has to be told what it will get.
5. **The hint is a price list, not a lightbulb.** A player deciding whether to
   spend a hint needs to know what it removes before they spend it. Hiding the
   cost makes every hint feel like a cheat.
6. **The day is immutable, so the interface never implies otherwise.** No
   retry, no reshuffle, no "new board" button. A withdrawn day says it was
   withdrawn.
7. **No timer anywhere.** Scoring uses session timing, which is not the same as
   showing a clock. A visible timer changes who plays.
8. **Offline is the normal case, not an error state.** The cache holds five
   days. The interface should not have a connection indicator, because a player
   with no signal is having an ordinary day, not a degraded one.
9. **The share artifact is a first class screen.** It is restricted to the
   game's declared vocabulary and it is read aloud by people who cannot see it,
   so it is designed, not generated as an afterthought.

## The ten approaches, and what each one is for

| # | approach | what it proves or risks |
|---|---|---|
| 1 | One board, one thumb | The conservative baseline. Everything fits, nothing is novel, and it works for all five games unchanged. The one to beat. |
| 2 | The shelf | Treats the five slots as a physical object. Makes swapping games feel like the product's point rather than a settings screen. |
| 3 | The deck | The day as a stack you swipe through. Fastest to play four games in a row; weakest at letting a player pick one game and stop. |
| 4 | Plain text | Monospace, symbols, no decoration. Accessible by construction and legible in any locale. Reads as a tool rather than a game, which suits some games and not others. |
| 5 | The witness ladder | The hint system as the main event: rungs, each with what it removes and what it costs. The most distinctive thing this engine could ship. |
| 6 | Glyph first | Accessibility as the aesthetic. Shape carries every state, so the same board works for a colour blind player with no separate mode. |
| 7 | Drag into wells | Direct manipulation, no select mode, no submit button, and a scrubber instead of undo. Best feel, worst fit for a one row game. |
| 8 | The daily page | The day as a printed page with a masthead. Carries five games and a correction notice gracefully; slower to get into a board. |
| 9 | Focus mode | Nothing on screen but the board, controls on long press. Beautiful and the easiest to get wrong, because a hidden control is a control a new player never finds. |
| 10 | The standing object | The record as a persistent object, with the retraction notice shown as an honest correction rather than hidden. |

## The recommendation, stated as one combination rather than one mockup

Take the frame from 1, the home screen from 2, the hint screen from 5, the state
language from 6, and the correction notice from 10. That is one coherent app: a
conservative board, a tactile library, a hint system nobody else has, a state
vocabulary that is accessible without a mode, and an honesty that a generated
daily puzzle has to earn.

Hold 4 as the accessibility fallback rather than a separate build, and keep 7 as
the interaction model for grid games only, where it is clearly better. 3, 8 and
9 are the ones to admire and not ship first: 3 fights a player who wants one
game, 8 costs a tap before every board, and 9 hides what a new player needs.

## What has to be decided before any of this is built

- What a game's presentation model may contain, because that is the real
  contract between a cartridge and the client and it has to be written before
  the client assumes anything.
- Whether the client renders a game's board from a declared layout kind (grid,
  row, canvas) or from a freer description. The former is simpler and bounds
  what a sixth cartridge can be; the latter is more optional and harder to make
  accessible.
- What the client does with a game whose board does not fit one screen.
