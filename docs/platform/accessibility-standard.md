# Accessibility standard

What every PuzzleGen game must be able to do for every player, and which half of
it the platform supplies.

The split is the whole design: the engine supplies focus management,
announcements and keyboard routing, but only the game knows what its elements
mean. A game that cannot describe its own semantics cannot be made accessible by
any amount of generic infrastructure, which is why the declaration is required
rather than inferred.

## What a game declares

An `AccessibilityDeclaration` on its descriptor
(`puzzlegen/engine/plugin.py`):

| field | meaning |
|---|---|
| `element_kinds` | the semantic kinds the puzzle presents, for example `("tile", "group")` |
| `keyboard_model` | which keyboard model the engine should install, for example `grid` |
| `announcements` | templates for state announcements, keyed by event, rendered by the engine so wording stays consistent across games |
| `colour_independent` | true when no information is conveyed by colour alone |
| `keyboard_complete` | true when the puzzle is fully operable without pointer input |
| `minimum_target_px` | the smallest interactive target, at least 44 |
| `state_symbols` | every visual state the game shows, each with a name, a symbol and a label |

## The gate

A declaration is complete when it declares element kinds, conveys nothing by
colour alone, is operable by keyboard, and keeps targets at 44px or above. An
incomplete declaration is refused by the session layer, which means no session
can be started against that game: the game still generates, and it cannot be
played.

That placement is deliberate. An early phase game can describe itself while
still being incomplete, and a promise that would be unplayable stays unplayable
rather than shipping with a gap.

Separately, two states sharing a name, a symbol or a label is refused outright
at declaration time, because two states that share one are indistinguishable to
somebody reading shapes or hearing text. This is the common failure in puzzle
games: correct and incorrect rendered as green and red, with the same shape.

## The requirements, as a checklist an author can act on

- [ ] Every visual state has its own symbol and its own label. Not a colour.
- [ ] Nothing is communicated by colour, position or animation alone.
- [ ] The whole puzzle is operable by keyboard, including every mode the game
      has, not only the main grid.
- [ ] Every interactive target is at least 44px.
- [ ] Every state change a sighted player notices has an announcement template.
- [ ] Every semantic element kind is declared, so the engine can describe focus.
- [ ] The share artifact's symbols are distinguishable without colour, because
      share text is read by people who cannot see it.
- [ ] Text contrast meets WCAG 2.2 AA in the client's rendering of the game's
      presentation model.

## What the platform supplies

- The keyboard model named by the declaration.
- Focus management across the puzzle's elements.
- Announcement rendering from the game's templates, so wording is consistent
  across every game a player holds. A player should not have to learn a new
  vocabulary per cartridge.
- Rendering hints per accessibility profile, so the client can adapt.
- The gate, so an incomplete declaration cannot reach a player.

## What the platform cannot supply, and does not pretend to

The accessibility promise beyond declared completeness is the game's, and it is
recorded rather than verified. The engine can check that a declaration is
complete and self consistent. It cannot check that `colour_independent=True` is
true of the rendering, or that the announcements are useful rather than present.
Recording the declaration is what makes a failure attributable to the author
instead of ambiguous.

## Standard the client is held to

The client is not built yet, so these are requirements rather than claims:

- Screen reader support on both platforms, exercised against the announcements
  the engine renders.
- Respect for the system's text size, reduced motion and increased contrast
  settings.
- No time pressure that cannot be turned off. A daily puzzle with a timer is a
  timer a player may need to not have.
- The share artifact readable as text, with alt text, which the share service
  already produces.

## Testing

- Declaration completeness and self consistency: automated, at certification.
- The gate: automated, since a session will not start.
- Share symbol distinguishability: review, against the declared tokens.
- Screen reader behaviour: manual, per client release, and unstaffed today.

The honest gap: everything above the client line is enforced in code, and
everything below it is a requirement with nobody yet assigned to test it.
