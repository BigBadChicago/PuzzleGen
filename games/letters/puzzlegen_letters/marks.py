"""The character work: which letters a board can carry, and on which tiles.

This is the constraint the requirements spend four sections on, and section 5
says where it belongs: in the game plugin, over members pulled through the
ordinary member queries, with the letters themselves supplied by the engine's
surface form subsystem. Nothing here reaches the graph and nothing here
invents a letter rule; the policy is the subsystem's and is named once below
so every board in a build rests on the same one.

Three settled rules decide everything in this module. A mark is a real letter
of the tile that carries it, a tile carries at most one mark, and at least one
tile carries none. The first two make this a bipartite matching between the
name's letter occurrences and the board's members; the third is why a board
needs one more tile than it has marks.

The fourth rule, that difficulty sets the blank count, is why the matching is
not the whole job. The data forces some blanks, because a letter no member
contains has no tile to sit on. When difficulty wants more blanks than the
data forces, the generator takes a mark off a tile that could have carried
one, and records that it did: a manufactured blank is indistinguishable from
a forced one on the board, so the only place the difference survives is the
board's own record of it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from puzzlegen.core.rng import DeterministicRng
from puzzlegen.surface import SurfaceForm, SurfacePolicy, canonical_letters

#: The letter rules every board in this game rests on.
#:
#: Stated here rather than taken per call, because two boards built under two
#: policies are not comparable and the difference would be invisible: the
#: index's fingerprint is what ties a board to the rules that produced it, and
#: a policy chosen at the call site cannot be fingerprinted once.
#:
#: The defaults, which is itself a decision. Diacritics fold, because the
#: letter on a tile and the letter the answer needs have to compare equal.
#: Separators drop, because there is no tile that could carry a space. And
#: ``preferred`` is empty: the shorter-synonym substitution section 4.3 settles
#: has no caller here, since this game's categories are curated and can simply
#: be named the short name. A lexical category, whose name is not ours to
#: choose, is where that substitution will be configured, and this is the
#: constant it would be configured on.
SURFACE_POLICY = SurfacePolicy()


def letters_of(text: str) -> str:
    """The canonical letter sequence of a name, under this game's policy."""
    return canonical_letters(text, SURFACE_POLICY)[0]


@dataclass(frozen=True, slots=True)
class MarkPlan:
    """One board's marks, and which blanks the data forced.

    ``marks`` is the board: a tile id to the single letter it carries. The two
    blank tuples are the record section 4.4 requires, and they are kept apart
    rather than summed because they answer different questions. A forced blank
    is a fact about the content: no member of this category contains that
    letter. A manufactured blank is a fact about the generator: a tile could
    have carried the letter and was deliberately left alone to hit the day's
    difficulty.
    """

    marks: Mapping[str, str]
    forced_blanks: tuple[str, ...]
    manufactured_blanks: tuple[str, ...]

    @property
    def blanks(self) -> int:
        return len(self.forced_blanks) + len(self.manufactured_blanks)

    @property
    def marked(self) -> int:
        return len(self.marks)

    def multiset(self) -> Counter[str]:
        """The letters the board shows, which is what a solver rearranges."""
        return Counter(self.marks.values())


def place_marks(
    letters: str, members: Sequence[SurfaceForm]
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Match as many of ``letters`` as possible onto distinct members.

    Returns the marks it placed and the letters it could not place. Which
    letters go unplaced is the content's answer and not the caller's choice:
    a name whose only ``q`` no member contains leaves that ``q`` for a blank,
    and asking the caller to nominate the blanks up front would refuse boards
    that exist.

    One slot per letter occurrence rather than per distinct letter, because a
    name needing two ``s`` needs two tiles; keying by letter would let the
    second occurrence silently reuse the first one's tile and produce a board
    that cannot be spelled from what it shows.

    The matching is Kuhn's augmenting search, which is exact. A greedy pass
    refuses a board that exists whenever an early letter takes the only tile a
    later letter could have used: only ``axe`` holds an x, and a pass that
    gives ``axe`` the a has nowhere left for the x. The same search runs in
    ``tools/measure_letters_coverage.py``, which is how a board the measure
    promised is a board this can build.

    Deterministic: slots in sorted order, members in entity id order, so the
    same category and the same members produce the same marks every time.
    A maximum matching is not unique, and only its size is invariant, so the
    orderings are the whole of the reproducibility claim here.
    """
    slots = sorted(Counter(letters).elements())
    ordered = sorted(members, key=lambda form: form.entity_id)
    holders = {
        letter: [form.entity_id for form in ordered if letter in form.letters]
        for letter in set(slots)
    }
    owner: dict[str, int] = {}

    def assign(slot: int, seen: set[str]) -> bool:
        for entity_id in holders[slots[slot]]:
            if entity_id in seen:
                continue
            seen.add(entity_id)
            if entity_id not in owner or assign(owner[entity_id], seen):
                owner[entity_id] = slot
                return True
        return False

    unplaced: list[str] = []
    for slot in range(len(slots)):
        if not assign(slot, set()):
            unplaced.append(slots[slot])

    return (
        {entity_id: slots[slot] for entity_id, slot in sorted(owner.items())},
        tuple(sorted(unplaced)),
    )


def plan_marks(
    letters: str,
    blanks: int,
    members: Sequence[SurfaceForm],
    rng: DeterministicRng,
) -> MarkPlan | None:
    """A board's marks for exactly ``blanks`` blanks, or None if impossible.

    Impossible has one cause: the content forces more blanks than difficulty
    asked for, which is the category whose letters its own members cannot
    carry. Every other case is reachable, because a mark can always be taken
    off a tile and a blank can always be added.

    The marks dropped to manufacture a blank are drawn from the day's stream
    rather than taken from the front of the matching. Taking the first would
    make the manufactured blank a function of entity id order, so the same
    category would withhold the same letter on every board it ever appeared
    on, which is the pattern 4.4's accepted tradeoff already worries about a
    solver detecting.
    """
    if blanks < 0:
        raise ValueError("a board cannot have a negative number of blanks")
    marks, forced = place_marks(letters, members)
    if len(forced) > blanks:
        return None

    wanted = blanks - len(forced)
    if wanted == 0:
        return MarkPlan(marks=dict(marks), forced_blanks=forced, manufactured_blanks=())
    if wanted > len(marks):
        # Every mark removed is still not enough blanks, which means the name
        # is shorter than the blank count: a board with no marks is not a
        # puzzle, and the caller screens for it before asking.
        return None

    dropped = rng.derive("manufactured-blanks").shuffled(sorted(marks))[:wanted]
    return MarkPlan(
        marks={
            entity_id: letter
            for entity_id, letter in sorted(marks.items())
            if entity_id not in dropped
        },
        forced_blanks=forced,
        manufactured_blanks=tuple(sorted(marks[entity_id] for entity_id in dropped)),
    )


def spells(word: str, shown: Mapping[str, int], blanks: int) -> bool:
    """Whether ``word`` is spellable from the marks on the board plus blanks.

    ``shown`` is a count per letter, not a tile-to-letter map: two tiles can
    carry the same letter, and what the solver arranges is the letters.

    The solver's own arithmetic, and the only letter question verification
    asks. A word qualifies when every mark is one of its letters and the
    blanks supply exactly the rest: the board shows how many slots there are,
    so a word of any other length is not an answer to it, and a word missing
    one of the marks leaves a tile's letter with nothing to be.
    """
    letters = letters_of(word)
    marks = Counter(shown)
    if len(letters) != sum(marks.values()) + blanks:
        return False
    available = Counter(letters)
    return all(available[letter] >= count for letter, count in marks.items())


def marks_are_honest(
    marks: Mapping[str, str], forms: Mapping[str, SurfaceForm]
) -> tuple[str, ...]:
    """Marks whose letter is not in their own tile's word.

    Rule 4.2, checked rather than assumed. Every mark here was placed by
    ``place_marks`` from the tile's own letters, so a violation is a bug in
    this game rather than a property of the content -- which is exactly why it
    is worth one cheap check before a board is published, since the failure it
    would produce is a tile whose mark is decoration.
    """
    return tuple(
        sorted(
            entity_id
            for entity_id, letter in marks.items()
            if entity_id not in forms or letter not in forms[entity_id].letters
        )
    )


__all__ = [
    "SURFACE_POLICY",
    "MarkPlan",
    "letters_of",
    "marks_are_honest",
    "place_marks",
    "plan_marks",
    "spells",
]
