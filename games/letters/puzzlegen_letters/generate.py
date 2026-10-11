"""Choosing a category whose own name its own members can spell.

A candidate here is a claim that one category and one set of its members can
be a board: the name is short enough for the board to mark, the members carry
the letters, and no other reading of the same tiles spells a different answer.
The claim is cheap and most are discarded, which is the contract: filtering is
the engine's job, and a game that pre-filters on freshness, confidence or
policy duplicates a gate it cannot see the inputs to.

Four things this module does refuse on its own, because each is a fact about
the board rather than about content policy.

A name the board cannot carry, which is marks against tiles and the one
refusal curation cannot fix later. Letters no member contains beyond the
day's blank budget, which is the character-level constraint section 5 puts
here. A tile whose own word is the answer, which prints the answer on the
board. And a second category that every tile also belongs to whose name the
same marks and blanks spell, which is the uniqueness of the answer and the
only place it can be established while the board is still being chosen.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from puzzlegen.content.query import ContentResult, EntityView, GroupView
from puzzlegen.core.hashing import short_hash
from puzzlegen.core.rng import DeterministicRng
from puzzlegen.engine.plugin import GenerationContext, PuzzleCandidate
from puzzlegen.surface import SurfaceIndex, form_for

from . import content as content_module
from .descriptor import (
    GAME_ID,
    MINIMUM_BLANKS,
    blanks_for,
    board_size_for,
    board_sizes_for,
)
from .marks import SURFACE_POLICY, MarkPlan, plan_marks, spells

#: Why a category was not a board. Counted rather than logged, because an
#: empty day is a number problem: knowing that 200 groups were refused is
#: useless, and knowing that 180 of them were refused for the name's length
#: says exactly which constraint to argue with.
NAME_TOO_LONG = "name_too_long"
NAME_UNUSABLE = "name_unusable"
LETTERS_UNPLACEABLE = "letters_unplaceable"
WRONG_SIZE = "wrong_size"
ANSWER_ON_A_TILE = "answer_on_a_tile"
AMBIGUOUS_ANSWER = "ambiguous_answer"
NO_CATEGORY_NAME = "no_category_name"


@dataclass(frozen=True, slots=True)
class BoardPlan:
    """One category, its tiles and its marks, with the evidence for the claim."""

    group: GroupView
    letters: str
    plan: MarkPlan
    board_size: int
    #: Every letters-taxonomy category each tile belongs to, by name. The
    #: verifier reads the readings of the board off this, so without it
    #: "no alternate interpretation" would mean only "we did not look".
    memberships: Mapping[str, tuple[str, ...]]
    #: The answer vocabulary this board was checked against, which is what
    #: makes the game's incomplete-verification promise a recorded fact rather
    #: than a sentence in a document.
    vocabulary: tuple[str, ...]

    @property
    def category(self) -> str:
        return self.group.shared_category or ""

    def tiles(self) -> tuple[EntityView, ...]:
        return self.group.members

    def dependency_ids(self) -> tuple[str, ...]:
        return self.group.dependency_ids()

    def candidate_id(self) -> str:
        """Derived from the category and its tiles, so one board is one claim.

        The members are part of the identity and not only the category,
        because two subsets of one category are two different boards: the
        marks differ, the decoy tiles differ, and one can be playable while
        the other is refused for a letter no member of it holds. Hashed rather
        than listed, because a twelve-tile board's ids run to a few hundred
        characters and a candidate id is read in logs.
        """
        tiles = short_hash(sorted(member.entity_id for member in self.tiles()))
        category = self.group.shared_category_id or self.category
        return f"{GAME_ID}:{category}:{self.board_size}:{tiles}"


def member_forms(group: GroupView, index: SurfaceIndex):
    index.add(group.members)
    return tuple(index.form(member.entity_id) for member in group.members)


def memberships_of(group: GroupView) -> dict[str, tuple[str, ...]]:
    """Each tile's category names, which are the readings a solver could give.

    Read off ``EntityView.types`` rather than walked in the taxonomy: the
    generator holds no graph handle, and the names are already on every
    member. They are the categories of the queried taxonomy alone, which is
    what makes the vocabulary a pinned one.
    """
    return {member.entity_id: tuple(member.types) for member in group.members}


def shared_categories(memberships: Mapping[str, tuple[str, ...]]) -> frozenset[str]:
    """Categories every tile on the board belongs to.

    These are the candidate answers, and the reason the check is an
    intersection rather than a vocabulary scan: a word only answers the board
    if it is true of every tile, and one tile outside it is enough to make the
    reading wrong. The intended category is always here, since the tiles are
    its members.
    """
    if not memberships:
        return frozenset()
    shared: set[str] | None = None
    for names in memberships.values():
        shared = set(names) if shared is None else shared & set(names)
    return frozenset(shared or ())


def alternate_answers(
    plan_marks_shown: Mapping[str, str],
    blanks: int,
    shared: Iterable[str],
    answer: str,
) -> tuple[str, ...]:
    """Other readings of this board that the marks and blanks also spell.

    The board asks one question and must have one answer. A category every
    tile also belongs to, whose name has the right length and contains every
    marked letter, is a second answer a player would be right to give and
    would be marked wrong for.
    """
    shown = Counter(plan_marks_shown.values())
    return tuple(
        sorted(
            name
            for name in shared
            if name != answer and spells(name, shown, blanks)
        )
    )


def plan_for(
    group: GroupView,
    *,
    board_size: int,
    blanks: int,
    vocabulary: tuple[str, ...],
    index: SurfaceIndex,
    rng: DeterministicRng,
) -> tuple[BoardPlan | None, str]:
    """This group as a board, or the reason it is not one.

    The checks run cheapest first, which is also most-informative first: a
    name too long for the board is a naming problem no curation fixes, and
    finding it before the matching keeps a day's refusal counts readable.
    """
    name = group.shared_category
    if not name:
        return None, NO_CATEGORY_NAME
    if len(group.members) != board_size:
        return None, WRONG_SIZE

    letters = form_for(
        EntityView(
            entity_id=group.shared_category_id or name, name=name
        ),
        SURFACE_POLICY,
    ).letters
    needed = len(letters) - blanks
    if needed < 1:
        # The blanks would spell the whole name, so the board shows nothing.
        return None, NAME_UNUSABLE
    # At least one tile carries no mark, which is a settled rule and the
    # reason a board needs one more tile than it has marks.
    if needed + 1 > board_size:
        return None, NAME_TOO_LONG

    forms = member_forms(group, index)
    if any(form.letters == letters for form in forms):
        # A tile labelled with the answer is the answer, printed on the board.
        return None, ANSWER_ON_A_TILE

    marks = plan_marks(letters, blanks, forms, rng)
    if marks is None:
        return None, LETTERS_UNPLACEABLE

    memberships = memberships_of(group)
    alternates = alternate_answers(
        marks.marks, blanks, shared_categories(memberships), name
    )
    if alternates:
        return None, AMBIGUOUS_ANSWER

    return (
        BoardPlan(
            group=group,
            letters=letters,
            plan=marks,
            board_size=board_size,
            memberships=memberships,
            vocabulary=vocabulary,
        ),
        "",
    )


def vocabulary_of(result: ContentResult) -> tuple[str, ...]:
    """Every category name the day's content offered, as the answer vocabulary.

    Recorded on every board because section 4.1's promise is about this list:
    the verifier is exhaustive over it and silent about every word outside it,
    so a board whose vocabulary cannot be read back is a board whose
    uniqueness claim cannot be audited.
    """
    return tuple(
        sorted({group.shared_category for group in result.groups if group.shared_category})
    )


def payload_for(plan: BoardPlan) -> dict[str, object]:
    """What a candidate carries forward.

    Ids, names and letters, never view objects: everything here crosses the
    plugin boundary and has to survive being JSON on the way to a subprocess.
    """
    return {
        "board_size": plan.board_size,
        "blanks": plan.plan.blanks,
        "answer_length": len(plan.letters),
        "category": plan.category,
        "category_id": plan.group.shared_category_id,
        "letters": plan.letters,
        "tiles": [
            {"id": member.entity_id, "label": member.name} for member in plan.tiles()
        ],
        "marks": dict(sorted(plan.plan.marks.items())),
        "forced_blanks": list(plan.plan.forced_blanks),
        "manufactured_blanks": list(plan.plan.manufactured_blanks),
        "memberships": {
            tile: list(names) for tile, names in sorted(plan.memberships.items())
        },
        "vocabulary": list(plan.vocabulary),
    }


def rationale_for(plan: BoardPlan) -> str:
    return (
        f"{plan.category!r} on {plan.board_size} tiles, "
        f"{plan.plan.marked} marks, {len(plan.plan.forced_blanks)} forced and "
        f"{len(plan.plan.manufactured_blanks)} manufactured blanks"
    )


#: Manufactured blanks are ranked in bands, and the cap is why.
#:
#: Preferring no manufacturing to some is worth a rank; preferring two to
#: three is a distinction nobody can see on the board, and ranking it would
#: spend the day's variety on it.
#:
#: Nothing else is ranked at all, which is the lesson game 1 paid for. Any
#: total order over content properties hands the day to whichever board scores
#: highest and keeps handing it over, because the engine takes the first
#: candidate that verifies and the day's shuffle only breaks exact ties. The
#: second half of the settled rule, longer words for harder days, is delivered
#: by the difficulty band rather than by a preference here: the measured score
#: rises with the answer's length, so a hard day's in-band search selects the
#: longer names on its own, and a preference would have fought it. Measured on
#: build A2, ranking by length as well as by manufacture gave one category six
#: of ten easy days.
def ranked(
    candidates: Sequence[PuzzleCandidate], rng: DeterministicRng
) -> list[PuzzleCandidate]:
    """The day draws an answer, then a board for it.

    One board per answer first, in a day-shuffled answer order, then the
    second board of each, and so on. The engine verifies in list order and
    keeps the first survivor, so what this ordering decides is which answer
    the day lands on, and it makes that draw uniform over the answers the
    content offers rather than over the boards.

    The difference is most of the variety this game has. A category with ten
    members yields far more playable boards than one with six, so a flat
    shuffle hands the day to whichever answer happens to be well supplied: of
    2,440 in-band easy candidates on build A2, 1,430 of them were ``sport``,
    and ten easy days drew it eight times. Grouping by answer first costs
    nothing -- every board still gets its turn, just later -- and turns that
    59% into one share in eight.

    There is deliberately no content preference beyond that. An earlier
    version put the boards that manufacture fewest blanks first, reasoning
    that 4.4's tradeoff should be kept as small as the content allows; that
    was a near-total order whose first bucket held five category names, and it
    reproduced game 1's documented failure from the other side. Manufactured
    blanks are already priced as a difficulty feature, so a board that leaves
    many tiles alone drifts harder and the band gate declines it on an easy
    day.

    Randomness lives here rather than in the engine because the engine does
    not choose: it verifies in list order and keeps the first survivor.
    """
    if not candidates:
        return []
    shuffled = rng.derive("candidate-order").shuffled(candidates)
    boards: dict[str, list[PuzzleCandidate]] = {}
    for candidate in shuffled:
        boards.setdefault(str(candidate.payload["category"]), []).append(candidate)
    answers = rng.derive("answer-order").shuffled(sorted(boards))
    deepest = max(len(group) for group in boards.values())
    return [
        boards[answer][depth]
        for depth in range(deepest)
        for answer in answers
        if depth < len(boards[answer])
    ]


def generate_candidates(
    context: GenerationContext, *, salt: str = ""
) -> Sequence[PuzzleCandidate]:
    """Offer as many boards as the budget allows, best first.

    Every size's candidates, the day's own size first, rather than stopping at
    the first size that produced any. A candidate is a claim and the engine is
    what finds out whether it verifies, so stopping here would let a single
    unverifiable board at the preferred size lose a day that had workable
    boards one size down. The preference survives as the order, which is where
    a preference belongs when the chooser is downstream.

    ``salt`` is the plugin's own, and it has to be the same one
    ``get_content_requirements`` used: the size order is a pure function of the
    day key and the salt, and a salted deployment that asked for content in one
    order and then ranked it in another would serve the unsalted day's
    preferred size while claiming a disjoint stream.
    """
    # Cleared first, so a run that returns early cannot leave the previous
    # day's refusals to be reported as its own.
    _LAST_REJECTIONS.clear()
    rejected: dict[str, int] = {}
    blanks = blanks_for(context.difficulty_target)
    index = SurfaceIndex(policy=SURFACE_POLICY)
    ordered: list[PuzzleCandidate] = []

    for board_size in board_sizes_for(context.day_key, salt):
        at_size = _candidates_at(context, board_size, blanks, index, rejected)
        ordered.extend(ranked(at_size, context.rng))
        if len(ordered) >= context.candidate_budget:
            break

    _LAST_REJECTIONS.update(rejected)
    # A tuple, as the protocol's return type says: the sequence a plugin hands
    # back is not the engine's to mutate.
    return tuple(ordered[: context.candidate_budget])


def _candidates_at(
    context: GenerationContext,
    board_size: int,
    blanks: int,
    index: SurfaceIndex,
    rejected: dict[str, int],
) -> list[PuzzleCandidate]:
    """Boards at one size, from that size's own content.

    Refusals accumulate across sizes into one mapping, keyed by size, because
    "nothing worked" is only diagnosable if each size says what stopped it.
    """
    result = context.content.get(content_module.sized(content_module.CANDIDATES, board_size))
    if result is None or not result.groups:
        rejected[f"size {board_size}: no categories"] = 1
        return []

    vocabulary = vocabulary_of(result)
    candidates: list[PuzzleCandidate] = []
    seen: set[str] = set()

    for group in result.groups:
        if len(candidates) >= context.candidate_budget:
            break
        # A substream per category, so two boards of the same category
        # manufacture their blanks independently and adding a candidate
        # elsewhere in the run cannot move either of them.
        rng = context.rng.derive(
            f"marks:{group.shared_category_id or group.shared_category}:{board_size}"
        )
        plan, reason = plan_for(
            group,
            board_size=board_size,
            blanks=blanks,
            vocabulary=vocabulary,
            index=index,
            rng=rng,
        )
        if plan is None:
            key = f"size {board_size}: {reason}"
            rejected[key] = rejected.get(key, 0) + 1
            continue
        candidate_id = plan.candidate_id()
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        candidates.append(
            PuzzleCandidate(
                candidate_id=candidate_id,
                payload=payload_for(plan),
                fact_refs=plan.dependency_ids(),
                rationale=rationale_for(plan),
            )
        )
    return candidates


#: Why the last generation run found nothing. Module state, and deliberately
#: not part of any return value: the plugin protocol has no channel for it,
#: and inventing one would put a diagnostic in the contract every game then
#: has to implement. Read by ``unusable_reason`` and by nothing else.
_LAST_REJECTIONS: dict[str, int] = {}


def last_rejections() -> dict[str, int]:
    return dict(_LAST_REJECTIONS)


def unusable_reason(context: GenerationContext, *, salt: str = "") -> str | None:
    """Why today produced nothing, in words a curator can act on.

    The content result already counts what each gate rejected; this says which
    of the game's own requirements went unmet, which is the part no engine gate
    can know.

    Takes the same ``salt`` as generation, because it reports the day's own
    size and that is what the salt moves. A diagnostic naming a size the run
    never asked for sends a curator to the wrong content.
    """
    board_size = board_size_for(context.day_key, salt)
    blanks = blanks_for(context.difficulty_target)
    if blanks < MINIMUM_BLANKS:  # pragma: no cover - the band table sets this
        return f"{blanks} blanks is below the floor of {MINIMUM_BLANKS}"
    result = context.content.get(
        content_module.sized(content_module.CANDIDATES, board_size)
    )
    if result is None or not result.groups:
        return f"no categories returned at {board_size} tiles"
    sized = [g for g in result.groups if len(g.members) == board_size]
    if not sized:
        return f"no category offered {board_size} eligible members"

    # This size's refusals only. A run tries every supported size, so a day
    # whose own size produced boards still records what the sizes it did not
    # need refused; counting those would call a working day broken.
    prefix = f"size {board_size}: "
    counts = {
        key.removeprefix(prefix): value
        for key, value in last_rejections().items()
        if key.startswith(prefix)
    }
    if counts:
        cause, count = max(counts.items(), key=lambda pair: (pair[1], pair[0]))
        total = sum(counts.values())
        detail = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        return (
            f"{total} categories refused at {board_size} tiles with {blanks} "
            f"blanks, mostly {cause} ({count}); {detail}"
        )
    return None


__all__ = [
    "AMBIGUOUS_ANSWER",
    "ANSWER_ON_A_TILE",
    "LETTERS_UNPLACEABLE",
    "NAME_TOO_LONG",
    "NAME_UNUSABLE",
    "NO_CATEGORY_NAME",
    "WRONG_SIZE",
    "BoardPlan",
    "alternate_answers",
    "generate_candidates",
    "last_rejections",
    "member_forms",
    "memberships_of",
    "payload_for",
    "plan_for",
    "ranked",
    "rationale_for",
    "shared_categories",
    "unusable_reason",
    "vocabulary_of",
]
