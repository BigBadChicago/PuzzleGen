# Game developer guide

How to write a PuzzleGen game. Written against protocol `1.2.0`.

If you read one thing first, read this: the platform will not publish a board
your own verifier did not accept, and it will refuse your game if a thirty day
run does not rebuild byte for byte. Everything below is in service of those two
facts.

## What you are writing

A lens. Your game decides what a board means, which boards are good, whether a
board is valid, and how a move is judged. It does not decide where facts come
from, when days are generated, who the player is, how a session is stored, or
what the share text is allowed to contain.

## Layout

Your game is its own distribution:

```
games/<name>/
  pyproject.toml           declares the puzzlegen.games entry point
  puzzlegen_<name>/
    __init__.py
    plugin.py              the GamePlugin implementation
    descriptor.py          the static contract
    content.py             the content requirement and its declaration
    generate.py            candidates
    assemble.py            candidate to board
    verify.py              the proof
    difficulty.py          the scale and its bands
    play.py                grade_move, get_hint, render, share
  tests/
  tools/generate_days.py   the committed day run
  content/                 only if the platform permits a curated file
```

Nothing enforces this split, but every shipped game uses it, and the reason is
that `verify.py` is the module a reviewer reads first and it should be readable
without reading generation.

## Declaring yourself

```python
# games/<name>/puzzlegen_<name>/pyproject.toml
[project.entry-points."puzzlegen.games"]
<name> = "puzzlegen_<name>.plugin:make_game"
```

An entry point resolves to either a plugin or a zero argument factory returning
one. Prefer the factory: it lets the registry decide when your game is
constructed, which matters because the registry constructs games during
discovery and you do not want to do work there.

## The descriptor is the contract

`describe()` returns a `GameDescriptor`, and nearly everything the engine will
later hold you to is declared there. The fields that carry real consequences:

| field | what you are promising |
|---|---|
| `game_id` | a stable identifier, alphanumeric with underscores. Changing it is a new game, not a new version. |
| `protocol_version` | the engine contract you implement. The engine refuses a different major and, at discovery, a minor below what play needs. |
| `uniqueness_contract` | how many solutions your boards have. Default `EXACTLY_ONE`. |
| `expected_solution_count` | required when the contract is `EXACTLY_N`. |
| `verification_completeness` | whether your verifier enumerates the whole state space or searches a bounded part of it. Declaring `COMPLETE` and returning a partial enumeration is a protocol violation, not a content problem. |
| `search_bound` | required when verification is incomplete. State the bound; do not imply completeness you do not have. |
| `supported_bands` | the difficulty bands you can actually produce. A band you declare and cannot reach does not fail loudly: it turns your ranking into a shuffle. |
| `accessibility` | an `AccessibilityDeclaration`. The session layer gates on it. |
| `difficulty_thresholds` | where your scale cuts into bands. Calibrate against the boards the engine reaches, not every candidate you offer. |
| `supplies_witness` | whether you implement `witness()`. Declared, not discovered, because a game that searches for its boards has no reason sequence to hand over, and the absence is a fact worth stating. |
| `share_tokens` | the complete vocabulary your share artifacts may use. A token outside it is a protocol error. |

## Content

Ask for what you need; do not reach for it.

```python
def get_content_requirements(self, ...):
    # a declaration of the slice, not a file you shipped
```

A third party game brings its content as a declaration, which the platform can
reproduce from the graph, rather than as a curated file, which the platform
could only trust. Generator kinds are platform owned: you pick from the
declarations that exist. If no generator produces the shape you need, that is a
platform change and a conversation, not a workaround.

Two cautions that have cost this project real time:

- Never glob lexicon files. Name exact roots. A glob picks up test fixtures.
- Check the `root` field inside a lexicon file, not the filename. A file named
  for a root it does not hold has happened here already.

## Determinism

You are handed a `DeterministicRng`. Use it and nothing else. No `random`, no
clock, no environment, no filesystem, no network.

```python
rng = context.derive("candidates")   # label your streams
```

Derive labelled sub streams rather than threading one generator through
everything, because a labelled stream is reconstructible from the seed and the
label, and that is what lets the protocol carry a seed where the in-process call
passes a generator.

The test of determinism is not that your code looks pure. It is that thirty days
rebuild to the same bytes, and CI will run exactly that test.

## Verifying

`verify()` returns a `VerificationResult`. This is your proof, and it is the
reason the engine can publish your board at all. Two rules:

1. Verify the contract you declared, not a weaker one. Solvable is not unique.
2. If your enumeration is bounded, say so in the descriptor and report the bound
   in the result. The engine checks the result against the declaration.

The terminal `solved=True` case is checkable by the engine and will be checked.
Grading correctness in general is not checkable, so it stays your promise and is
recorded as such, which means a grading failure is attributable to you rather
than ambiguous.

## Difficulty

Calibrate on the boards the engine reaches after every gate, not on the
candidates you generated. One shipped game is the exception where the band
changes which boards exist, and there the sample is the band's own reachable
set. Getting this wrong does not raise: it produces a model that bands a minority
of boards correctly and looks like a bad model rather than a bad sample.

A band target has to be reachable by your content. Both symbolic number games
here first shipped a target their content could not produce, and in both cases
the symptom was a silent shuffle, not an error.

## Hints and the witness

If your generator eliminated possibilities to reach its board, keep the
elimination and return it from `witness()`, and set `supplies_witness=True`. A
hint derived from the real elimination is correct by construction. A hint
authored beside the board is a second source of truth that will drift from the
first.

Hints are charged at the cost the player experiences. Do not price a hint by
what it cost you to compute.

## Accessibility

Your declaration is checked, and an incomplete one blocks a session from
starting. Minimum: declare your semantic `element_kinds`, declare a
`state_symbol` for every visual state you show, convey nothing by colour alone,
be fully operable by keyboard, and keep interactive targets at 44px or larger.
Two states sharing a symbol or a label is refused, because they are
indistinguishable to somebody reading shapes or hearing text.

The engine renders the announcements so the wording stays consistent across
games; you supply the templates and the semantics. See
`accessibility-standard.md`.

## Sharing

Your `create_share_artifact()` output goes through a redactor before it leaves
the platform. It is checked against your declared `share_tokens` and against a
leak check, because a move ledger with a lopsided shape has revealed an answer
here before. A token outside your declared vocabulary is a protocol error, not a
content rejection.

## Local development

```
pip install -e ".[dev]"
for game in games/*/; do pip install -e "$game"; done
scripts/test-all.sh
```

Several suites run, not one: the platform's, which must pass with no game
installed, and each game's. Do not merge them. A single run would hide the
property that makes the module boundary real.

For a snapshot to build content against, the symbolic path needs no model, no
network and no lexicon: `docs/build-symbolic-snapshot.sh` builds in seconds and
reproduces byte for byte. Use it unless your game genuinely needs the lexicon.

## The thirty day run

Before you submit, commit a thirty day run against a named snapshot, produced by
your own `tools/generate_days.py`. The platform's CI regenerates it and fails on
any difference. This is the admission test.

One rule about that tool, which is this project's most repeated mistake: a
diagnostic must read what the engine actually did, never recompute it. If your
day generator derives a value the engine also derives, import the engine's
constant or read the engine's output. Four separate bugs here were a tool and the
engine disagreeing about a number.

## Checklist before submitting

- [ ] Platform suite passes with your game uninstalled.
- [ ] Your suite passes with your game installed.
- [ ] Thirty day run committed and regenerating identically.
- [ ] Descriptor declares the contract your verifier actually proves.
- [ ] `search_bound` set if verification is incomplete.
- [ ] Every declared band is reachable by your content.
- [ ] Accessibility declaration complete; a session starts.
- [ ] Share artifacts use only declared tokens and pass the leak check.
- [ ] No clock, network, filesystem, environment or unseeded randomness.
- [ ] Content arrives as a declaration.
