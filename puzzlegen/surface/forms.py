"""What the letters of an entity are.

The knowledge graph answers questions about meaning. A game that reasons about
spelling, length, shared affixes or letter positions is asking a different kind
of question, and the answer rests on decisions the graph never had to make: is
``é`` the same mark as ``e``, does a solver's board carry the space in ``food
fish``, and when an entity has four names, which one is the word being spelled.

Those decisions are shared and load bearing, so they live here once rather than
inside whichever game needs them first. This module owns them, states each one
in a policy a curator can read, and produces a deterministic mapping from an
entity to the letters it is made of. It consumes entities through the ordinary
member queries and adds no operation to the content service: the graph is
unchanged, and a game that needs letters calls this.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from ..content.query import EntityView
from ..core.hashing import stable_hash

#: The alphabet a form may be made of. A board carries letters, and a tile that
#: carried "3" or "&" would be marking something the solver cannot anagram, so
#: anything outside this is dropped and recorded rather than silently kept.
ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz")


class Diacritics(StrEnum):
    """What an accented letter folds to."""

    #: ``é`` becomes ``e``. The letter a solver holds on a tile and the letter
    #: they need for the answer have to compare equal, and on an English board
    #: nobody distinguishes the two by eye.
    FOLD = "fold"
    #: ``é`` stays ``é``, and so is outside :data:`ALPHABET` and dropped. For a
    #: locale where the accent is part of the spelling rather than decoration.
    KEEP = "keep"


class Separators(StrEnum):
    """What a space, hyphen or apostrophe becomes."""

    #: Dropped, so ``food fish`` has eight letters. A tile carries a letter;
    #: there is no tile that could carry a space.
    DROP = "drop"
    #: Kept, and so outside :data:`ALPHABET` and dropped from the letters
    #: anyway, but counted as refused rather than as a separator. Exists so a
    #: future game that lays out words by position can ask for it.
    KEEP = "keep"


#: Characters :data:`Separators.DROP` removes before the alphabet check, so
#: they are not reported as refused characters.
SEPARATORS = frozenset(" -'’_/.")


class FormSource(StrEnum):
    """Which of an entity's names the form was built from."""

    #: The primary name the exporter chose, which is the first lemma.
    NAME = "name"
    #: An alias, chosen because a curator named it in the policy's overrides.
    CURATED_ALIAS = "curated_alias"
    #: An alias, chosen because it was shorter and the policy allows that.
    SHORTER_ALIAS = "shorter_alias"


@dataclass(frozen=True, slots=True)
class SurfacePolicy:
    """The decisions a letter form rests on, written down.

    Every field here changes which boards are legal, so the policy is hashed
    into the index's fingerprint: a board generated under one policy and
    regenerated under another is not the same board, and the difference is
    visible rather than silent.
    """

    diacritics: Diacritics = Diacritics.FOLD
    separators: Separators = Separators.DROP
    #: Entity id to the alias to use instead of the primary name. The curated
    #: path for the substitution the requirements call for, ``bowed stringed
    #: instrument`` to ``bowed instrument``.
    preferred: Mapping[str, str] = field(default_factory=dict)
    #: Whether an unreviewed alias may win for being shorter.
    #:
    #: Off, and deliberately. The requirements want the short *common* name,
    #: and nothing in the graph says which alias is common: WordNet synonym
    #: sets put ``moggy`` beside ``cat``. Taking the shortest would quietly
    #: spell boards out of words no solver would reach for, which is the exact
    #: failure this subsystem exists to prevent. A curator who knows the
    #: shorter name is the common one says so in ``preferred``.
    prefer_shorter_alias: bool = False

    def fingerprint(self) -> str:
        return stable_hash(
            {
                "diacritics": self.diacritics,
                "separators": self.separators,
                "preferred": dict(sorted(self.preferred.items())),
                "prefer_shorter_alias": self.prefer_shorter_alias,
            }
        )


@dataclass(frozen=True, slots=True)
class SurfaceForm:
    """One entity's letters, and why they are those letters."""

    entity_id: str
    #: The name the letters were taken from, as written.
    display: str
    #: The canonical letter sequence, lowercase and inside :data:`ALPHABET`.
    letters: str
    source: FormSource
    #: Words in ``display``, before the collapse. A multi-word name is legal
    #: and a game may want to know it was one.
    word_count: int
    #: Characters the alphabet refused, in order of first appearance. A form
    #: with any of these is still usable; the field is here so a curator can
    #: see why a category's letter count does not match its name.
    refused: tuple[str, ...] = ()

    @property
    def length(self) -> int:
        return len(self.letters)

    @property
    def playable(self) -> bool:
        """Whether anything is left to spell with."""
        return bool(self.letters)

    def multiset(self) -> Counter[str]:
        """The letters as counts, which is how spelling questions are asked.

        A board distributes a name's letters one per tile, so what matters is
        how many of each letter the name needs, never their order.
        """
        return Counter(self.letters)


def canonical_letters(
    text: str, policy: SurfacePolicy | None = None
) -> tuple[str, tuple[str, ...]]:
    """``text`` as letters, with the characters the alphabet refused.

    The order of operations is itself a decision: case folds first, then
    diacritics, then separators drop, then the alphabet check. Folding after
    the alphabet check would refuse ``é`` before it had the chance to become
    ``e``.
    """
    policy = policy or SurfacePolicy()
    folded = text.casefold()
    if policy.diacritics is Diacritics.FOLD:
        # NFKD splits an accented letter into its base plus a combining mark,
        # so dropping the marks leaves the base. Mn is the combining-mark
        # category; ß casefolds to "ss" above rather than decomposing here.
        folded = "".join(
            char
            for char in unicodedata.normalize("NFKD", folded)
            if unicodedata.category(char) != "Mn"
        )
    letters: list[str] = []
    refused: list[str] = []
    for char in folded:
        if policy.separators is Separators.DROP and char in SEPARATORS:
            continue
        if char in ALPHABET:
            letters.append(char)
        elif char not in refused:
            refused.append(char)
    return "".join(letters), tuple(refused)


def form_for(entity: EntityView, policy: SurfacePolicy | None = None) -> SurfaceForm:
    """The letter form of one entity under ``policy``."""
    policy = policy or SurfacePolicy()
    display, source = _choose_name(entity, policy)
    letters, refused = canonical_letters(display, policy)
    return SurfaceForm(
        entity_id=entity.entity_id,
        display=display,
        letters=letters,
        source=source,
        word_count=len(display.split()),
        refused=refused,
    )


def _choose_name(
    entity: EntityView, policy: SurfacePolicy
) -> tuple[str, FormSource]:
    """Which of the entity's names is the word being spelled."""
    curated = policy.preferred.get(entity.entity_id)
    if curated is not None:
        # Checked rather than trusted: a preference naming an alias the entity
        # does not have would spell a board out of a word the entity is not
        # known by, and a typo in curation should not be able to do that.
        if curated not in (entity.name, *entity.aliases):
            raise ValueError(
                f"preferred name {curated!r} is not a name of {entity.entity_id}"
            )
        if curated != entity.name:
            return curated, FormSource.CURATED_ALIAS
        return curated, FormSource.NAME
    if policy.prefer_shorter_alias and entity.aliases:
        # Ties go to the primary name, and among aliases to the alphabetically
        # first, so the choice does not depend on export order.
        best = min(entity.aliases, key=lambda alias: (len(alias), alias))
        if len(best) < len(entity.name):
            return best, FormSource.SHORTER_ALIAS
    return entity.name, FormSource.NAME


class SurfaceIndex:
    """Letter forms for a set of entities, computed once.

    Character-level feasibility runs per candidate on every daily build, and
    folding a name is cheap only the first time. Owning the precomputed
    mapping is the cost a helper would never justify, and the reason this is a
    subsystem: the index is also the record of what the letters were, so a
    board regenerates identically years later and a curator can ask why a
    category was ruled playable.
    """

    def __init__(
        self,
        entities: Iterable[EntityView] = (),
        *,
        policy: SurfacePolicy | None = None,
    ) -> None:
        self._policy = policy or SurfacePolicy()
        self._forms: dict[str, SurfaceForm] = {}
        self.add(entities)

    @property
    def policy(self) -> SurfacePolicy:
        return self._policy

    def add(self, entities: Iterable[EntityView]) -> None:
        """Compute forms for ``entities``, skipping ones already held.

        Daily builds ask for overlapping member sets, so the same entity
        arrives many times and recomputing it would be the whole cost the
        index exists to avoid.
        """
        for entity in entities:
            if entity.entity_id not in self._forms:
                self._forms[entity.entity_id] = form_for(entity, self._policy)

    def __len__(self) -> int:
        return len(self._forms)

    def __contains__(self, entity_id: object) -> bool:
        return entity_id in self._forms

    def get(self, entity_id: str) -> SurfaceForm | None:
        return self._forms.get(entity_id)

    def form(self, entity_id: str) -> SurfaceForm:
        """The form for ``entity_id``, or :class:`KeyError`.

        A game asking for letters the index was never given has a bug in what
        it passed to the index, and a default would turn that into a board
        quietly spelled out of nothing.
        """
        try:
            return self._forms[entity_id]
        except KeyError:
            raise KeyError(f"no surface form for {entity_id}") from None

    def letters(self, entity_id: str) -> str:
        return self.form(entity_id).letters

    def forms(self) -> tuple[SurfaceForm, ...]:
        """Every form held, in entity id order."""
        return tuple(self._forms[key] for key in sorted(self._forms))

    def fingerprint(self) -> str:
        """Identity of the policy and of every form under it.

        Two indexes with the same fingerprint answer every letter question the
        same way, which is what a board's reproducibility rests on.
        """
        return stable_hash(
            {
                "policy": self._policy.fingerprint(),
                "forms": [
                    [form.entity_id, form.display, form.letters, form.source]
                    for form in self.forms()
                ],
            }
        )


def letter_pool(forms: Sequence[SurfaceForm]) -> Counter[str]:
    """Every letter the given forms can supply between them, with counts.

    One tile carries at most one mark, so a pool counting a letter twice means
    two different tiles can supply it. A game checking whether a name can be
    spelled from a board starts here and then has to place the letters on
    distinct tiles, which this deliberately does not do: the pool is an upper
    bound and a cheap first refusal, not the placement.
    """
    pool: Counter[str] = Counter()
    for form in forms:
        pool.update(set(form.letters))
    return pool
