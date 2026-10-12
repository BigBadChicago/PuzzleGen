"""Choosing a code and the clues that pin it down.

A candidate here is a claim that a code and a set of clues are a board:
exactly one code in the whole space is consistent with every clue, and none of
the clues is the code itself. The claim is cheap and most are discarded, which
is the contract: filtering is the engine's job, and a game that pre-filters on
freshness, confidence or policy duplicates a gate it cannot see the inputs to.

The construction is elimination, and it is the opposite of a search. Take the
whole space -- 720 codes or 5,040 -- apply the board's stated facts, then walk
a shuffled pool of guesses and keep each one that removes at least one
surviving code, stopping the moment exactly one survives. The board is then
uniquely deducible by construction rather than by luck, and the clue count is
whatever it took.

Two things about that are worth stating because they are easy to get wrong.

A clue that removes nothing is dropped. Not because it is harmful -- a
redundant clue is merely noise -- but because the stopping rule counts clues
and a board padded with uninformative rows would measure as harder than it is.

And the stopping rule is "exactly one survives", not "the secret is among the
survivors". Those differ precisely when the board is not deducible: a board
with two survivors has one answer and no argument for it, and a solver who
reasons correctly to the end has two codes and a coin. That is the board
``UNIQUE_DEDUCTION`` promises not to ship.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from puzzlegen.content.query import ContentResult, EntityView
from puzzlegen.core.hashing import short_hash
from puzzlegen.core.rng import DeterministicRng
from puzzlegen.engine.plugin import GenerationContext, PuzzleCandidate
from puzzlegen.symbols import SymbolIndex, SymbolPolicy

from . import content as content_module
from .descriptor import (
    GAME_ID,
    MAX_CLUES,
    code_lengths_for,
    properties_for,
)
from .lock import (
    CodeError,
    DigitFacts,
    GuessClue,
    PropertyClue,
    check_code,
    property_clues_for,
    space_for,
    survivors,
    verdict,
)

#: Why an attempt produced no board. Counted rather than logged, because an
#: empty day is a number problem: that forty attempts failed says nothing, and
#: that all forty ran out of clues says exactly which constraint to argue
#: with.
TOO_FEW_DIGITS = "too_few_digits"
CLUES_EXHAUSTED = "clues_exhausted"
FACTS_MISSING = "facts_missing"
GUESS_IS_THE_CODE = "guess_is_the_code"

#: How many boards to attempt per code length.
#:
#: Thirty-two, and fewer than the other games' budgets because each attempt
#: costs more: a four-digit attempt filters five thousand codes once per clue,
#: so a budget of sixty-four would be six times the work of a figure day for
#: variety nothing needs. Measured, the construction succeeds on about
#: ninety-nine attempts in a hundred.
ATTEMPTS_PER_LENGTH = 32

#: How many guesses an attempt will consider before giving up.
#:
#: Sixty. The walk stops as soon as one code survives, so this is not a clue
#: ceiling -- ``MAX_CLUES`` is -- but a bound on how many uninformative
#: guesses an unlucky draw may read past. Measured: a successful attempt reads
#: a median of eight guesses to find its five clues.
GUESS_POOL = 60

#: The symbolic form policy this game reads its tiles under.
#:
#: The default. A digit's name is one character, so it is its own glyph with
#: no curation, and the ``spoken`` fact carries the word a reader hears. This
#: is the clearest case the subsystem exists for: "7" and "seven" are the
#: same entity under two channels, and neither is derivable from the other.
SYMBOL_POLICY = SymbolPolicy()


@dataclass(frozen=True, slots=True)
class BoardPlan:
    """One code, its clues and the evidence for the claim."""

    code_length: int
    #: The answer.
    code: tuple[int, ...]
    guesses: tuple[GuessClue, ...]
    properties: tuple[PropertyClue, ...]
    #: Digits in ascending order, with the forms a client draws and says.
    digits: tuple[int, ...]
    glyphs: Mapping[int, str]
    labels: Mapping[int, str]
    #: Entity id per digit, for the dependency ledger.
    entity_ids: Mapping[int, str]
    #: The graph's facts about every digit, carried through to the verifier.
    facts: Mapping[int, DigitFacts]
    #: How large the space was before any clue, and after the stated facts.
    space_size: int
    after_properties: int
    #: How many codes survived every clue but the last. The measure of how
    #: much the final clue had to do.
    before_last: int

    def candidate_id(self) -> str:
        """Identity of the board, from the code and the clues it shows."""
        return short_hash(
            [
                GAME_ID,
                str(self.code_length),
                "code",
                *(str(digit) for digit in self.code),
                "guesses",
                *(
                    f"{''.join(str(d) for d in clue.guess)}:{clue.exact}:{clue.misplaced}"
                    for clue in self.guesses
                ),
                "properties",
                *(f"{clue.kind}:{clue.value}" for clue in self.properties),
            ],
            16,
        )

    def dependency_ids(self) -> tuple[str, ...]:
        """Every graph record this board rests on.

        Every digit the board could name, not only the ones in the code. A
        clue naming a digit the board never declared would be a dependency the
        engine was not shown, and the solver rules out every digit, so every
        digit is load bearing.
        """
        return tuple(dict.fromkeys(self.entity_ids[digit] for digit in self.digits))

    def clue_count(self) -> int:
        return len(self.guesses)


def facts_of(entity: EntityView) -> DigitFacts | None:
    """One digit's facts, or ``None`` when the content does not supply them.

    Read from the graph rather than computed, which is the whole reason this
    game is a graph game: the clue a player reads and the elimination the
    generator did are the same facts, so they cannot disagree.
    """
    value = entity.properties.get(content_module.VALUE)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    prime = entity.properties.get("is_prime")
    parity = entity.properties.get("parity")
    if not isinstance(prime, bool) or parity not in ("even", "odd"):
        return None
    return DigitFacts(value=value, prime=prime, even=parity == "even")


def readable(
    result: ContentResult,
) -> tuple[list[int], dict[int, DigitFacts], dict[int, str], SymbolIndex]:
    """The day's digits, their facts and their entity ids.

    A digit whose facts the content does not carry is dropped here rather than
    in the middle of an attempt, because a board built over nine digits is a
    different game and the requirement, not the generator, is where that
    should surface.
    """
    index = SymbolIndex(result.entities, policy=SYMBOL_POLICY)
    digits: list[int] = []
    facts: dict[int, DigitFacts] = {}
    entity_ids: dict[int, str] = {}
    for entity in result.entities:
        fact = facts_of(entity)
        if fact is None:
            continue
        if not index.form(entity.entity_id).playable:
            continue
        if fact.value in facts:
            continue
        digits.append(fact.value)
        facts[fact.value] = fact
        entity_ids[fact.value] = entity.entity_id
    digits.sort()
    return digits, facts, entity_ids, index


def plan_for(
    *,
    digits: Sequence[int],
    facts: Mapping[int, DigitFacts],
    entity_ids: Mapping[int, str],
    index: SymbolIndex,
    code_length: int,
    properties: Sequence[str],
    rng: DeterministicRng,
) -> tuple[BoardPlan | None, str]:
    """One board attempt, or the name of the refusal that stopped it."""
    if len(digits) < code_length:
        return None, TOO_FEW_DIGITS

    space = space_for(code_length, digits)
    code = tuple(rng.sample(list(digits), code_length))
    stated = property_clues_for(code, facts, properties)

    alive = survivors(space, (), stated, facts)
    after_properties = len(alive)

    guesses: list[GuessClue] = []
    before_last = after_properties
    for guess in rng.sample(space, min(len(space), GUESS_POOL)):
        if len(alive) <= 1:
            break
        if len(guesses) >= MAX_CLUES:
            break
        exact, misplaced = verdict(guess, code)
        clue = GuessClue(guess=tuple(guess), exact=exact, misplaced=misplaced)
        if clue.solves():
            # The guess was the code. Skipped rather than refused: the draw is
            # still fine, and a clue showing the answer is the one thing a
            # board may never do.
            continue
        kept = [item for item in alive if clue.admits(item)]
        if len(kept) == len(alive):
            # Removed nothing. Dropped rather than kept, because the stopping
            # rule counts clues and a board padded with uninformative rows
            # would measure as harder than it is.
            continue
        before_last = len(alive)
        guesses.append(clue)
        alive = kept

    if len(alive) != 1:
        return None, CLUES_EXHAUSTED
    if alive[0] != code:
        # Unreachable: every clue is true of the code by construction, so the
        # code never leaves the surviving set. Counted anyway, because the
        # alternative to counting it is a board that verifies as having one
        # answer which is not the one it states.
        return None, GUESS_IS_THE_CODE

    return (
        BoardPlan(
            code_length=code_length,
            code=code,
            guesses=tuple(guesses),
            properties=stated,
            digits=tuple(digits),
            glyphs={
                digit: index.glyph(entity_ids[digit]) for digit in digits
            },
            labels={
                digit: index.spoken(entity_ids[digit]) for digit in digits
            },
            entity_ids=dict(entity_ids),
            facts=dict(facts),
            space_size=len(space),
            after_properties=after_properties,
            before_last=before_last,
        ),
        "",
    )


def payload_for(plan: BoardPlan) -> dict[str, object]:
    """The candidate payload, which is the plan in plain JSON."""
    return {
        "code_length": plan.code_length,
        "code": list(plan.code),
        "guesses": [
            {
                "guess": list(clue.guess),
                "exact": clue.exact,
                "misplaced": clue.misplaced,
            }
            for clue in plan.guesses
        ],
        "properties": [
            {"kind": clue.kind, "value": clue.value, "text": clue.sentence()}
            for clue in plan.properties
        ],
        "digits": list(plan.digits),
        "glyphs": {str(digit): plan.glyphs[digit] for digit in plan.digits},
        "labels": {str(digit): plan.labels[digit] for digit in plan.digits},
        # The graph's own facts about every digit the board may name, carried
        # so the verifier evaluates a property clue against the same facts the
        # clue was made from. Without this it would have to recompute
        # primality, which is one line and the one line that could make a
        # board unsolvable from what it shows: the clue would say one thing
        # and the verifier another, and a build whose digit taxonomy differed
        # from textbook arithmetic would produce exactly that.
        "digit_facts": {
            str(digit): {
                "value": plan.facts[digit].value,
                "prime": plan.facts[digit].prime,
                "even": plan.facts[digit].even,
            }
            for digit in plan.digits
        },
        "space_size": plan.space_size,
        "after_properties": plan.after_properties,
        "before_last": plan.before_last,
    }


def rationale_for(plan: BoardPlan) -> str:
    return (
        f"{plan.code_length} digits, {len(plan.guesses)} guess clues and "
        f"{len(plan.properties)} stated facts, space {plan.space_size} to "
        f"{plan.after_properties} to 1, {plan.before_last} before the last clue"
    )


def ranked(plans: Sequence[BoardPlan]) -> list[BoardPlan]:
    """Boards ordered by how much work the clues leave the solver.

    Most clues first, then the most codes surviving before the final clue,
    then the candidate id. Both keys point the same way, which is the
    direction the difficulty model reads: a board with more clue rows is more
    to cross-reference, and a board whose last clue had to eliminate forty
    candidates rather than one asked more of the solver on the way there.

    A preference, not a verdict. The engine measures difficulty itself and
    searches for a board inside the requested band, so this decides which
    boards it looks at first and nothing else. The band's own lever is which
    facts the board may state, which is applied before any of this.
    """
    return sorted(
        plans,
        key=lambda plan: (
            -len(plan.guesses),
            -plan.before_last,
            plan.candidate_id(),
        ),
    )


def generate_candidates(
    context: GenerationContext, *, salt: str = ""
) -> Sequence[PuzzleCandidate]:
    """Offer as many boards as the budget allows, most demanding first."""
    # Cleared first, so a run that returns early cannot leave the previous
    # day's refusals to be reported as its own.
    _LAST_REJECTIONS.clear()
    rejected: dict[str, int] = {}
    result = context.content.get(content_module.CANDIDATES)
    if result is None or not result.entities:
        rejected["no digits"] = 1
        _LAST_REJECTIONS.update(rejected)
        return ()

    digits, facts, entity_ids, index = readable(result)
    dropped = len(result.entities) - len(digits)
    if dropped:
        rejected[FACTS_MISSING] = dropped
    if not digits:
        _LAST_REJECTIONS.update(rejected)
        return ()

    properties = properties_for(context.difficulty_target)
    ordered: list[PuzzleCandidate] = []
    seen: set[str] = set()

    for code_length in code_lengths_for(context.day_key, salt):
        plans: list[BoardPlan] = []
        for attempt in range(ATTEMPTS_PER_LENGTH):
            # A substream per attempt, so adding an attempt cannot move the
            # boards the earlier ones produced.
            rng = context.rng.derive(f"board:{code_length}:{attempt}")
            plan, reason = plan_for(
                digits=digits,
                facts=facts,
                entity_ids=entity_ids,
                index=index,
                code_length=code_length,
                properties=properties,
                rng=rng,
            )
            if plan is None:
                key = f"length {code_length}: {reason}"
                rejected[key] = rejected.get(key, 0) + 1
                continue
            plans.append(plan)

        for plan in ranked(plans):
            candidate_id = plan.candidate_id()
            if candidate_id in seen:
                continue
            seen.add(candidate_id)
            ordered.append(
                PuzzleCandidate(
                    candidate_id=candidate_id,
                    payload=payload_for(plan),
                    fact_refs=plan.dependency_ids(),
                    rationale=rationale_for(plan),
                )
            )
        if len(ordered) >= context.candidate_budget:
            break

    _LAST_REJECTIONS.update(rejected)
    return tuple(ordered[: context.candidate_budget])


#: Why the last generation run found nothing. Module state, and deliberately
#: not part of any return value: the plugin protocol has no channel for it.
_LAST_REJECTIONS: dict[str, int] = {}


def last_rejections() -> dict[str, int]:
    return dict(_LAST_REJECTIONS)


def unusable_reason() -> str:
    """The narrative a curator acts on, from the last run's refusals."""
    rejections = last_rejections()
    if not rejections:
        return ""
    ordered = sorted(rejections.items(), key=lambda kv: (-kv[1], kv[0]))
    return "; ".join(f"{reason} ({count})" for reason, count in ordered)


__all__ = [
    "ATTEMPTS_PER_LENGTH",
    "CLUES_EXHAUSTED",
    "FACTS_MISSING",
    "GUESS_IS_THE_CODE",
    "GUESS_POOL",
    "SYMBOL_POLICY",
    "TOO_FEW_DIGITS",
    "BoardPlan",
    "facts_of",
    "generate_candidates",
    "last_rejections",
    "payload_for",
    "plan_for",
    "ranked",
    "rationale_for",
    "readable",
    "unusable_reason",
]
