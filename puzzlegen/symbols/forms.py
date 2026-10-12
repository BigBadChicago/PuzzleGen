"""What an entity looks like when it is not a word.

The knowledge graph answers questions about meaning, and the surface form
subsystem answers questions about spelling. Both assume the thing on the tile
is a word, because for a lexical game it is: the name is the label, the label
is what a client draws, and a screen reader reads the same characters.

A digit, a quantity and a figure break that assumption in the same place. The
tile shows ``7`` or ``△``, which is not a name; a reader has to say "seven" or
"triangle", which is not the glyph; and a board of numbers has an order that a
board of words does not. Nothing in the platform decided where any of those
three come from, so the first game over such content would decide, the second
would decide differently, and the engine would be unable to announce a tile in
a game it did not ship with.

This module owns the three decisions once:

* **The glyph.** What a client draws, as one or two characters. Content, not
  code: that a triangle is drawn ``△`` is a fact about the figure, supplied by
  whatever produced it, exactly as its membership is.
* **The spoken label.** What a reader says. Never derived from the glyph,
  because ``△`` has no pronunciation and ``7`` has one only by convention; it
  comes from the entity's own name or from a fact that overrides it.
* **The ordinal.** Where the entity sits on a scale, when it sits on one at
  all. A number has one and a word does not, so it is optional rather than
  assumed, and it is read rather than parsed out of the name: ``entity:7``
  having a numeric id is a coincidence of slugging and not a value.

Like ``puzzlegen.surface``, this consumes entities through the ordinary member
queries and adds no operation to the content service. The graph is unchanged;
a game, or the engine's own presentation layer, calls this.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from ..content.query import EntityView
from ..core.hashing import stable_hash

#: Predicate carrying the glyph a client draws.
#:
#: A predicate rather than a field on the entity, for the reason every other
#: assertion is a fact: the glyph of a figure comes from whoever drew it and
#: the glyph of a digit from the numbering system, and those are different
#: sources with different provenance. ``graph/models.py`` states the rule.
GLYPH_PREDICATE = "glyph"

#: Predicate carrying the spoken label, where the name is not what to say.
#:
#: Usually absent. An entity named ``seven`` needs no override; one named
#: ``7`` does, and one named ``two filled red triangles`` already reads
#: correctly and needs none either. The override exists for the gap between a
#: name that identifies and a phrase that announces.
SPOKEN_PREDICATE = "spoken"

#: Predicate carrying the position on a scale.
VALUE_PREDICATE = "value"

#: Predicate carrying the kind, where the content knows it.
KIND_PREDICATE = "symbol_kind"


class SymbolKind(StrEnum):
    """What sort of thing the tile is showing.

    Declared by the content and defaulted by this module, because the kind
    changes what a reader should hear: a digit is read as a digit ("seven"), a
    quantity may be read as a number ("seventeen"), and a figure has no
    reading at all except the words somebody wrote for it. A game that needs
    to know whether its tiles are comparable asks for the kind rather than
    guessing from the glyph.
    """

    #: One character of a numbering system. Ordered, and its own glyph.
    DIGIT = "DIGIT"
    #: A quantity. Ordered; its glyph may be several characters.
    NUMBER = "NUMBER"
    #: A simple object: a shape, with whatever attributes the content gave it.
    #: Unordered unless the content supplies a value.
    FIGURE = "FIGURE"
    #: A word, which is what every lexical game's tiles are. Here so that a
    #: mixed board is expressible and so that this module can describe a
    #: lexical entity rather than refusing it.
    WORD = "WORD"


class GlyphSource(StrEnum):
    """Where the glyph came from, recorded like a surface form's source."""

    #: The ``glyph`` fact. The ordinary case for symbolic content.
    FACT = "fact"
    #: The entity's own name, used whole because it is already short enough
    #: to be a glyph. This is how a digit entity named ``7`` works without
    #: any curation at all.
    NAME = "name"
    #: Nothing usable. The form is unplayable and says so.
    NONE = "none"


class MissingGlyph(StrEnum):
    """What to do with an entity that has no glyph."""

    #: Return an unplayable form naming the problem. The caller decides: a
    #: coverage tool wants to count these, a generator wants to skip them.
    REFUSE = "refuse"
    #: Use the name however long it is. For a board of words, where the label
    #: is the glyph and no length rule applies.
    USE_NAME = "use_name"


#: Longest glyph a tile is assumed to hold.
#:
#: Two, which is what a two-digit number needs and what a single figure
#: needs. Not a layout constant: a client lays out whatever it is given. It is
#: the length at which a name stops being plausibly a glyph, so that an entity
#: named ``periwinkle`` is not silently drawn on a tile meant for ``7``.
MAX_GLYPH_LENGTH = 2


@dataclass(frozen=True, slots=True)
class SymbolPolicy:
    """The decisions a symbolic form rests on, written down.

    Every field changes which boards are legal, so the policy is hashed into
    the index's fingerprint: a board generated under one policy and
    regenerated under another is not the same board, and the difference is
    visible rather than silent. The same arrangement ``SurfacePolicy`` uses,
    for the same reason.
    """

    #: What a glyphless entity becomes.
    missing_glyph: MissingGlyph = MissingGlyph.REFUSE
    #: How long a name may be and still serve as its own glyph.
    max_glyph_length: int = MAX_GLYPH_LENGTH
    #: Kind assumed when the content declares none.
    default_kind: SymbolKind = SymbolKind.FIGURE
    #: Entity id to the spoken label to use, overriding both the fact and the
    #: name. The curated path, for a reading a curator corrects without
    #: rebuilding a snapshot.
    spoken: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.max_glyph_length < 1:
            raise ValueError("a glyph is at least one character")

    def fingerprint(self) -> str:
        return stable_hash(
            {
                "missing_glyph": self.missing_glyph,
                "max_glyph_length": self.max_glyph_length,
                "default_kind": self.default_kind,
                "spoken": dict(sorted(self.spoken.items())),
            }
        )


@dataclass(frozen=True, slots=True)
class SymbolForm:
    """One entity as something a client can draw and a reader can say."""

    entity_id: str
    #: What a client draws. Empty when the entity has none and the policy
    #: refuses to invent one.
    glyph: str
    #: What a reader says. Never empty for an entity with a name, because a
    #: tile nobody can hear described is a tile some players cannot use, and
    #: the accessibility gate exists to refuse exactly that.
    spoken: str
    kind: SymbolKind
    source: GlyphSource
    #: Position on a scale, where the content supplies one.
    ordinal: float | None = None
    #: Why this form has no glyph, when it has none. Empty otherwise.
    refused: str = ""

    @property
    def playable(self) -> bool:
        """Whether there is anything to put on a tile."""
        return bool(self.glyph) and bool(self.spoken)

    @property
    def ordered(self) -> bool:
        return self.ordinal is not None

    def value(self) -> float:
        """The ordinal, or :class:`ValueError`.

        Raised rather than defaulted. A game sorting a board of numbers and
        silently receiving zero for the one entity that carries no value would
        publish a board whose order is wrong in a way no test of the sort
        itself could find.
        """
        if self.ordinal is None:
            raise ValueError(f"{self.entity_id} carries no {VALUE_PREDICATE!r}")
        return self.ordinal


def _fact(entity: EntityView, predicate: str) -> object | None:
    value = entity.properties.get(predicate)
    return value if value is not None else None


def _text(value: object | None) -> str:
    return str(value).strip() if value is not None else ""


def kind_for(entity: EntityView, policy: SymbolPolicy | None = None) -> SymbolKind:
    """The kind the content declares, or the policy's default.

    An unrecognised declaration is the default rather than an error. The kind
    changes how a tile is announced and nothing about whether it is correct,
    so a snapshot carrying a kind this release has not heard of should serve
    boards rather than fail them.
    """
    policy = policy or SymbolPolicy()
    declared = _text(_fact(entity, KIND_PREDICATE)).upper()
    try:
        return SymbolKind(declared)
    except ValueError:
        return policy.default_kind


def glyph_for(
    entity: EntityView, policy: SymbolPolicy | None = None
) -> tuple[str, GlyphSource, str]:
    """``(glyph, source, refusal)`` for one entity under ``policy``.

    The fact wins over the name, always. An entity can legitimately be named
    one thing and drawn as another -- that is the whole point of a figure
    whose name is ``two filled red triangles`` -- and a name that happened to
    be short would otherwise beat the glyph somebody chose.
    """
    policy = policy or SymbolPolicy()
    declared = _text(_fact(entity, GLYPH_PREDICATE))
    if declared:
        return declared, GlyphSource.FACT, ""
    name = entity.name.strip()
    if not name:
        return "", GlyphSource.NONE, "the entity has neither a glyph nor a name"
    if policy.missing_glyph is MissingGlyph.USE_NAME:
        return name, GlyphSource.NAME, ""
    if len(name) <= policy.max_glyph_length:
        return name, GlyphSource.NAME, ""
    return (
        "",
        GlyphSource.NONE,
        f"no {GLYPH_PREDICATE!r} fact and the name is "
        f"{len(name)} characters, over the {policy.max_glyph_length} a glyph holds",
    )


def spoken_for(entity: EntityView, policy: SymbolPolicy | None = None) -> str:
    """What a reader says for one entity.

    Three places, in order: the policy's curated override, the ``spoken``
    fact, then the entity's own name. The name is last and is also the
    fallback that makes this always answer something, which matters more here
    than anywhere else in the subsystem: a glyph can be missing and the board
    simply loses a tile, while a missing spoken label is a board that renders
    and cannot be played by somebody using a reader.
    """
    policy = policy or SymbolPolicy()
    curated = policy.spoken.get(entity.entity_id)
    if curated:
        return curated.strip()
    declared = _text(_fact(entity, SPOKEN_PREDICATE))
    if declared:
        return declared
    return entity.name.strip()


def ordinal_for(entity: EntityView) -> float | None:
    """The entity's position on a scale, when it carries one.

    Read from the ``value`` fact and from nowhere else. Parsing the name would
    work for ``7`` and silently produce nothing for ``seven``, which is the
    same entity under a different export.
    """
    raw = _fact(entity, VALUE_PREDICATE)
    if isinstance(raw, bool) or raw is None:
        # A boolean is a flag, not a position. ``isinstance(True, int)`` is
        # true in Python, so the check has to come first or every entity with
        # a boolean ``value`` would sort as zero or one.
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    return None


def form_for(entity: EntityView, policy: SymbolPolicy | None = None) -> SymbolForm:
    """The symbolic form of one entity under ``policy``."""
    policy = policy or SymbolPolicy()
    glyph, source, refused = glyph_for(entity, policy)
    return SymbolForm(
        entity_id=entity.entity_id,
        glyph=glyph,
        spoken=spoken_for(entity, policy),
        kind=kind_for(entity, policy),
        source=source,
        ordinal=ordinal_for(entity),
        refused=refused,
    )


class SymbolIndex:
    """Symbolic forms for a set of entities, computed once.

    The same bargain ``SurfaceIndex`` makes. A daily build asks for
    overlapping member sets and reads each tile's glyph, label and value
    several times over, so the mapping is worth owning; and owning it makes
    the index the record of what the tiles were, so a board regenerates
    identically and a curator can ask why an entity was ruled unplayable.
    """

    def __init__(
        self,
        entities: Iterable[EntityView] = (),
        *,
        policy: SymbolPolicy | None = None,
    ) -> None:
        self._policy = policy or SymbolPolicy()
        self._forms: dict[str, SymbolForm] = {}
        self.add(entities)

    @property
    def policy(self) -> SymbolPolicy:
        return self._policy

    def add(self, entities: Iterable[EntityView]) -> None:
        for entity in entities:
            if entity.entity_id not in self._forms:
                self._forms[entity.entity_id] = form_for(entity, self._policy)

    def __len__(self) -> int:
        return len(self._forms)

    def __contains__(self, entity_id: object) -> bool:
        return entity_id in self._forms

    def get(self, entity_id: str) -> SymbolForm | None:
        return self._forms.get(entity_id)

    def form(self, entity_id: str) -> SymbolForm:
        """The form for ``entity_id``, or :class:`KeyError`.

        No default, for the reason ``SurfaceIndex.form`` has none: an entity
        the index was never given is a bug in what the caller passed, and a
        blank form would turn it into a board with an empty tile.
        """
        try:
            return self._forms[entity_id]
        except KeyError:
            raise KeyError(f"no symbolic form for {entity_id}") from None

    def glyph(self, entity_id: str) -> str:
        return self.form(entity_id).glyph

    def spoken(self, entity_id: str) -> str:
        return self.form(entity_id).spoken

    def forms(self) -> tuple[SymbolForm, ...]:
        """Every form held, in entity id order."""
        return tuple(self._forms[key] for key in sorted(self._forms))

    def playable(self) -> tuple[SymbolForm, ...]:
        return tuple(form for form in self.forms() if form.playable)

    def unplayable(self) -> tuple[SymbolForm, ...]:
        """Forms with no glyph, with the reason each was refused."""
        return tuple(form for form in self.forms() if not form.playable)

    def ascending(self, entity_ids: Sequence[str] | None = None) -> tuple[str, ...]:
        """The given entities in ordinal order, ties broken by id.

        Ties break on the id rather than being left to the sort's input order,
        because the input order is whatever the content service issued and a
        board laid out by it would not reproduce.

        An entity with no ordinal is a caller error here rather than something
        sorted to one end. "Put the unordered ones last" is a presentation
        choice some game will want and this is not the place to make it: a
        sort that quietly accepts a mixed board is how an unordered tile ends
        up presented as if it had a position.
        """
        chosen = list(entity_ids) if entity_ids is not None else sorted(self._forms)
        forms = [self.form(entity_id) for entity_id in chosen]
        unordered = [form.entity_id for form in forms if not form.ordered]
        if unordered:
            raise ValueError(
                f"cannot order entities that carry no {VALUE_PREDICATE!r}: "
                f"{sorted(unordered)[:3]}"
            )
        return tuple(
            form.entity_id
            for form in sorted(forms, key=lambda f: (f.value(), f.entity_id))
        )

    def glyph_collisions(self) -> dict[str, tuple[str, ...]]:
        """Glyphs more than one entity draws, with the entities that share them.

        The symbolic counterpart of a lemma collision, and a sharper problem:
        two words that spell the same thing are a curiosity, while two tiles
        drawn identically are a board where the player cannot tell which tile
        they clicked. A game checks this before it publishes; nothing here
        refuses it, because a build is allowed to hold a collision as long as
        no board draws both halves of it.
        """
        grouped: dict[str, list[str]] = {}
        for form in self.playable():
            grouped.setdefault(form.glyph, []).append(form.entity_id)
        return {
            glyph: tuple(sorted(found))
            for glyph, found in sorted(grouped.items())
            if len(found) > 1
        }

    def fingerprint(self) -> str:
        """Identity of the policy and of every form under it.

        Two indexes with the same fingerprint answer every question about
        glyphs, labels and order the same way, which is what a symbolic
        board's reproducibility rests on.
        """
        return stable_hash(
            {
                "policy": self._policy.fingerprint(),
                "forms": [
                    [
                        form.entity_id,
                        form.glyph,
                        form.spoken,
                        form.kind,
                        form.source,
                        form.ordinal,
                    ]
                    for form in self.forms()
                ],
            }
        )


__all__ = [
    "GLYPH_PREDICATE",
    "KIND_PREDICATE",
    "MAX_GLYPH_LENGTH",
    "SPOKEN_PREDICATE",
    "VALUE_PREDICATE",
    "GlyphSource",
    "MissingGlyph",
    "SymbolForm",
    "SymbolIndex",
    "SymbolKind",
    "SymbolPolicy",
    "form_for",
    "glyph_for",
    "kind_for",
    "ordinal_for",
    "spoken_for",
]
