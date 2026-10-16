# Content policy

What may be in the graph, and what may be in a cartridge's boards. Two
different questions with two different owners, so they are answered separately.

This policy has a decision owner: the platform maintainer. It is written so a
reviewer can apply it without asking, and so an author can predict a refusal
before submitting.

## Part one: the graph

The graph is production infrastructure with its own quality, freshness,
provenance, review and dependency lifecycle. Content policy for it is therefore
mostly about provenance rather than taste.

**Admitted.** Facts, relationships, categories and vocabulary that come from a
source with a recorded licence permitting this use, that are stable enough that
a snapshot taken today is still true next month, and that a reviewer can trace
to that source.

**Refused.**

- Anything whose licence does not permit redistribution in a published puzzle.
  This is the one refusal with no judgement in it.
- Facts about living private individuals. Public figures in their public
  capacity are fine; a person's home town is not graph material.
- Anything whose truth has a short half life. A current office holder, a sports
  record, a price. A daily puzzle generated from a snapshot taken in advance will
  be wrong by the time it is played, and "wrong" in a puzzle means unsolvable or
  unfairly solvable.
- Content that is correct but arrives with no traceable provenance. An unsourced
  fact is a future retraction.

**Reviewed case by case.** Overlay categories that the curator created rather
than imported. These are legitimate and already in use, and the review question
is whether the overlay is a defensible categorisation or one person's opinion
presented as a fact.

## Part two: a cartridge's boards

What a player can be shown. A board is generated, so this policy is applied to
the generator's reachable output, not to a sample.

**Refused in a board, whatever the game.**

- Slurs and hate speech, including as a hidden answer, an anagram, or an
  incidental letter arrangement. An author whose generator can reach one has a
  content filter to add, and the platform's own lexicon exports carry one.
- Sexual content, graphic violence, and content depicting minors in any sexual
  context.
- Content that targets a protected group, a named private individual, or an
  identifiable small group.
- Instructions or recipes for harm, including where the harm is the puzzle's
  theme.
- Content whose solvability depends on knowledge a reasonable player in the
  declared locale could not have. This is a fairness rule, not a taste rule, and
  it is the one most likely to be violated by accident.
- Any text in the board or the share artifact outside the vocabulary the game
  declared. The engine enforces this one for share artifacts.

**Permitted, with care.** Language that is crude but not targeted; subject
matter that is grim but factual, such as a category of diseases; proper nouns,
including brands, where they are the natural member of a category.

**The incidental arrangement problem, stated plainly.** Games that arrange
letters can produce an offensive string nobody authored. This is not a
hypothetical for this platform: one shipped game's whole mechanic is letters
from category members anagramming into a category name. A game that arranges
letters MUST filter its reachable output against a blocklist, and the filter
runs at generation, not at review, because review sees thirty days and the game
will run for years.

## Locale and language

A game declares `supported_locales`. The content policy applies in the locale
the board is published in, which means the blocklist is per locale and a string
that is innocuous in one language and a slur in another is refused in the
locale where it is a slur.

The platform ships English. A game declaring a locale the platform has no
blocklist for cannot be certified in that locale, and that is a platform gap to
close rather than an author's problem to work around.

## Attribution and licences

Every content source in the graph carries its licence and its attribution
requirement. Where a licence requires attribution in the published work, the
attribution appears in the client's about surface, not in the board, because a
board is a generated artifact and threading attribution through it would couple
presentation to provenance.

The project's current lexicon source is WordNet plus curated overlays, and the
attribution obligations of that source are met in the client's about surface.

## Enforcement

| where | how |
|---|---|
| graph admission | curator review, with provenance recorded per record |
| cartridge certification | thirty day run read by a human, plus the share vocabulary and leak checks by machine |
| cartridge runtime | the game's own generation time filter, which is the author's obligation |
| after publication | retraction, which withdraws the day and restores affected streaks |

A content failure that reaches a player is handled as a retraction, never as an
edit. The day was wrong; it is withdrawn, and the people who played it are not
punished for it.

## What this policy does not cover

- Player generated content. There is none, by design.
- Advertising and sponsored content. There is none, and introducing any would
  need this policy extended first.
- Player to player communication. There is none.
