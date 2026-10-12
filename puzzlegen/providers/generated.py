"""Providers whose content is a declaration rather than a file.

Every provider before this one reads something: a WordNet export, a frequency
table, a hand-authored JSON document. That is the right shape for knowledge
somebody had to collect, and the wrong shape for knowledge nobody collected.
The integers from zero to ninety-nine are not a dataset. Neither is the set of
simple figures built from three shapes, three fills, three colours and three
counts. Both are the product of a rule, and both are needed before a game can
be built over digits, numbers or simple objects.

Writing them out as curated files would work exactly once. Eighty-one figures
and a hundred numbers is several thousand committed JSON lines whose every
value this module could have computed, which is this project's recurring
failure in its purest form: an artifact that can drift from the code that
should have produced it. So the declaration is the content, the provider is
the generator, and the snapshot's content hash is a function of the
declaration.

Three things make that safe rather than merely convenient:

* **Determinism.** The bundle is a pure function of the constructor
  arguments. Two builds of the same declaration produce the same keys, the
  same names and the same facts in the same order, which is what the
  snapshot's reproducibility claim rests on.
* **Honest provenance.** The source kind is COMPUTED and names the
  declaration it came from, so a curator reading a fact can see it was
  asserted by arithmetic rather than by a person or a corpus.
* **The ordinary protocol.** These emit ``Raw*`` records like every other
  provider and go through the same normaliser, the same activation policy and
  the same gates. Nothing downstream can tell a generated entity from an
  imported one, which is the property that lets a number sit in the same
  graph as a word and be grouped by the same queries.

Why ``ProvenanceClass.SOURCED`` and not COMPUTED, since this is plainly a
computation. COMPUTED means derived from other records in the graph, and the
model enforces that by requiring ``derived_from``: a COMPUTED fact is
invalidated when its inputs are. These facts have no inputs in the graph. The
source is the generator itself, which is what ``SourceKind.COMPUTED`` on the
descriptor records, and a derivation list naming the entity its own fact is
about would be a formality standing in for information.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass

from ..core.types import FreshnessClass, ProvenanceClass, SourceKind
from .base import (
    ProviderBundle,
    ProviderDescriptor,
    ProviderError,
    ProviderFreshness,
    ProviderKey,
    RawCategory,
    RawEntity,
    RawFact,
)

#: Predicates the generated content asserts.
#:
#: The first four are the symbolic form subsystem's own (``puzzlegen.symbols``
#: names the same strings), which is deliberate and is the only coupling
#: between the two: a provider that emits glyphs and a subsystem that reads
#: them have to agree on the predicate, and the alternative is a mapping table
#: in a third place that can disagree with both.
GLYPH = "glyph"
SPOKEN = "spoken"
VALUE = "value"
SYMBOL_KIND = "symbol_kind"

#: Confidence on everything here.
#:
#: One, which no imported fact ever gets and which is correct for exactly this
#: content: that seven is odd is not a claim a source made and might have got
#: wrong. The activation policy's floor and the platform policy's floor are
#: both below it, so generated content activates without a special case.
CERTAIN = 1.0

#: Arithmetic does not go stale.
FRESHNESS = FreshnessClass.STATIC

_ONES = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
_TENS = (
    "",
    "",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
)


def spoken_number(value: int) -> str:
    """``value`` as the words a reader says.

    Here rather than in the symbolic form subsystem because it is content: the
    spoken label is a fact the provider asserts, and the subsystem reads facts
    rather than computing them. Supports zero to 9,999, which is the range
    this provider will generate; outside it the provider refuses rather than
    emitting a label somebody would have to read as "1000".
    """
    if value < 0:
        raise ValueError("negative numbers have no spoken form here")
    if value < 20:
        return _ONES[value]
    if value < 100:
        tens, ones = divmod(value, 10)
        return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")
    if value < 1000:
        hundreds, rest = divmod(value, 100)
        head = f"{_ONES[hundreds]} hundred"
        return head if not rest else f"{head} and {spoken_number(rest)}"
    if value < 10_000:
        thousands, rest = divmod(value, 1000)
        head = f"{_ONES[thousands]} thousand"
        return head if not rest else f"{head} {spoken_number(rest)}"
    raise ValueError(f"no spoken form for {value}: the range stops at 9,999")


def is_prime(value: int) -> bool:
    if value < 2:
        return False
    if value % 2 == 0:
        return value == 2
    limit = math.isqrt(value)
    return all(value % factor for factor in range(3, limit + 1, 2))


def is_square(value: int) -> bool:
    return value >= 0 and math.isqrt(value) ** 2 == value


def is_triangular(value: int) -> bool:
    """Whether ``value`` is ``n(n+1)/2`` for some whole ``n``.

    Solved rather than searched: ``8v + 1`` is a perfect square exactly when
    ``v`` is triangular, so the test is one integer square root regardless of
    how large the range gets.
    """
    if value < 0:
        return False
    return is_square(8 * value + 1)


def is_fibonacci(value: int) -> bool:
    """Whether ``value`` appears in 0, 1, 1, 2, 3, 5, 8, ...

    The identity test (``5v^2 ± 4`` a perfect square) is the usual trick and
    is wrong for nothing in range, but a generated walk is as cheap here and
    needs no justification, so the walk is what this does.
    """
    if value < 0:
        return False
    previous, current = 0, 1
    while previous < value:
        previous, current = current, previous + current
    return previous == value


def digit_sum(value: int) -> int:
    return sum(int(char) for char in str(abs(value)))


@dataclass(frozen=True, slots=True)
class NumberCategory:
    """One generated category and the test that decides its members."""

    #: Suffix of the provider key, and the slug the category is keyed by.
    slug: str
    #: The category's canonical name, which is what a game displays and, for a
    #: game that spells it, what gets spelled.
    name: str
    gloss: str


#: The categories a number range emits, in the order they are emitted.
#:
#: Each is a property a player can state in a short phrase, which is the
#: criterion rather than mathematical interest: a game whose answer is a
#: category's name needs names a solver would actually say. "Multiple of
#: three" is here and "non-residue modulo seven" is not.
NUMBER_CATEGORIES: tuple[NumberCategory, ...] = (
    NumberCategory("even", "even number", "divisible by two"),
    NumberCategory("odd", "odd number", "not divisible by two"),
    NumberCategory("prime", "prime number", "divisible only by one and itself"),
    NumberCategory("square", "square number", "a whole number times itself"),
    NumberCategory("triangular", "triangular number", "a sum of consecutive counts from one"),
    NumberCategory("fibonacci", "Fibonacci number", "each the sum of the two before it"),
    NumberCategory("multiple_of_three", "multiple of three", "divisible by three"),
    NumberCategory("multiple_of_five", "multiple of five", "divisible by five"),
    NumberCategory("multiple_of_ten", "multiple of ten", "divisible by ten"),
    NumberCategory("one_digit", "single-digit number", "written with one digit"),
    NumberCategory("two_digit", "two-digit number", "written with two digits"),
    NumberCategory("three_digit", "three-digit number", "written with three digits"),
)

_MEMBERSHIP = {
    "even": lambda v: v % 2 == 0,
    "odd": lambda v: v % 2 == 1,
    "prime": is_prime,
    "square": is_square,
    "triangular": is_triangular,
    "fibonacci": is_fibonacci,
    "multiple_of_three": lambda v: v % 3 == 0,
    "multiple_of_five": lambda v: v % 5 == 0,
    "multiple_of_ten": lambda v: v % 10 == 0,
    "one_digit": lambda v: len(str(v)) == 1,
    "two_digit": lambda v: len(str(v)) == 2,
    "three_digit": lambda v: len(str(v)) == 3,
}


class NumberRangeProvider:
    """Every integer in a range, with the properties that sort it.

    One provider for digits and for numbers, with the range as the only
    difference between them, because they are the same content at two scales
    and a second class would be a second place for the arithmetic to be wrong.
    The taxonomy a build imports it under is the build's choice, as it is for
    every provider, so a digits taxonomy over nought to nine and a numbers
    taxonomy over nought to ninety-nine can both exist in one snapshot. Where
    they overlap they name the same entity -- ``seven`` is one entity with two
    memberships, not two sevens -- which is the shared-graph property working
    on content that is not words.
    """

    def __init__(
        self,
        low: int = 0,
        high: int = 99,
        *,
        name: str = "numbers",
        kind: str = "NUMBER",
        root_name: str = "number",
        now: dt.datetime | None = None,
    ) -> None:
        if low < 0:
            raise ProviderError("the range starts at zero or above")
        if high < low:
            raise ProviderError(f"empty range: {low} to {high}")
        if high > 9_999:
            raise ProviderError(
                f"the range stops at 9,999; {high} has no spoken form"
            )
        self._low = low
        self._high = high
        self._name = name
        self._kind = kind
        self._root = root_name
        self._now = now or dt.datetime.now(dt.timezone.utc)

    @property
    def values(self) -> range:
        return range(self._low, self._high + 1)

    def _key(self, value: int) -> ProviderKey:
        return f"{self._name}/{value}"

    def _category_key(self, slug: str) -> ProviderKey:
        return f"{self._name}/category/{slug}"

    def _version(self) -> str:
        """The declaration, as the version string.

        The range is the content, so the range is the version. A build that
        widened the range and kept the version would claim two different
        snapshots were built from the same source.
        """
        return f"{self._low}-{self._high}"

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            name=self._name,
            kind=SourceKind.COMPUTED,
            version=self._version(),
            retrieved_at=self._now,
        )

    def freshness_status(self) -> ProviderFreshness:
        """Always healthy, and the reason is worth stating.

        Every other provider's freshness is a question about an upstream that
        may have moved. There is no upstream here: the content is the range,
        the range is in the version string, and a change to it is a different
        source rather than a stale one.
        """
        return ProviderFreshness(
            healthy=True,
            checked_at=self._now,
            age_days=0,
            detail=f"generated from the range {self._version()}; no upstream to age",
        )

    def load(self) -> ProviderBundle:
        categories = [
            RawCategory(
                key=self._category_key("root"),
                name=self._root,
                gloss="a whole number",
            ),
            *(
                RawCategory(
                    key=self._category_key(category.slug),
                    name=category.name,
                    parent_keys=(self._category_key("root"),),
                    gloss=category.gloss,
                )
                for category in NUMBER_CATEGORIES
                if self._category_has_members(category.slug)
            ),
        ]
        entities: list[RawEntity] = []
        facts: list[RawFact] = []
        for value in self.values:
            key = self._key(value)
            entities.append(
                RawEntity(
                    key=key,
                    # The digits, which is what a tile shows and what makes
                    # the entity id readable. The word is the spoken label,
                    # asserted as a fact, so one entity serves a board that
                    # draws "7" and a reader that says "seven".
                    name=str(value),
                    aliases=(),
                    definition=f"the number {spoken_number(value)}",
                    category_keys=tuple(
                        self._category_key(category.slug)
                        for category in NUMBER_CATEGORIES
                        if _MEMBERSHIP[category.slug](value)
                        and self._category_has_members(category.slug)
                    ),
                    confidence=CERTAIN,
                )
            )
            facts.extend(self._facts(key, value))
        return ProviderBundle(
            descriptor=self.describe(),
            categories=tuple(categories),
            entities=tuple(entities),
            facts=tuple(facts),
        )

    def _category_has_members(self, slug: str) -> bool:
        """Whether this range has anything in that category.

        An empty category is worse than a missing one: a game asking the graph
        for groups would find a category with no members and have to tell that
        apart from a category whose members were all filtered out.
        """
        test = _MEMBERSHIP[slug]
        return any(test(value) for value in self.values)

    def _facts(self, key: ProviderKey, value: int) -> list[RawFact]:
        def fact(predicate: str, item: object) -> RawFact:
            return RawFact(
                subject_key=key,
                predicate=predicate,
                value=item,
                freshness_class=FRESHNESS,
                provenance_class=ProvenanceClass.SOURCED,
                confidence=CERTAIN,
            )

        return [
            fact(VALUE, value),
            fact(GLYPH, str(value)),
            fact(SPOKEN, spoken_number(value)),
            fact(SYMBOL_KIND, self._kind),
            fact("digit_count", len(str(value))),
            fact("digit_sum", digit_sum(value)),
            fact("parity", "even" if value % 2 == 0 else "odd"),
            fact("is_prime", is_prime(value)),
            fact("is_square", is_square(value)),
            fact("is_triangular", is_triangular(value)),
            fact("is_fibonacci", is_fibonacci(value)),
        ]

    def fetch_entity(self, key: ProviderKey) -> RawEntity | None:
        for entity in self.load().entities:
            if entity.key == key:
                return entity
        return None


#: The default attribute space for figures: four attributes, three values each.
#:
#: Three values each is not an arbitrary size. The rule a figure game asks a
#: player to see -- every attribute either all the same or all different
#: across a set -- is determinate only at three: any two figures then
#: determine exactly one third that completes the set, so a board's valid sets
#: are a property of the board rather than of how many values an attribute
#: happened to have. At four values per attribute the same sentence is still a
#: rule and no longer a construction, and a generator would be searching where
#: it could be computing.
#:
#: The attributes themselves are chosen so that none of them is colour alone.
#: Shape and fill are in the glyph, count is in how many glyphs a tile draws,
#: and colour is the fourth -- carried as a named attribute rather than as a
#: hue, so a client can draw it in colour and still label it, and a board
#: stays operable for a player who cannot see the difference. The engine's
#: accessibility gate refuses a game that conveys anything by colour alone,
#: and an attribute space of four colours would have made that unavoidable.
FIGURE_ATTRIBUTES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("shape", ("triangle", "circle", "square")),
    ("fill", ("solid", "hollow", "striped")),
    ("count", ("one", "two", "three")),
    ("colour", ("red", "green", "blue")),
)

#: The glyph for each shape at each fill.
#:
#: Content, and it belongs here rather than in a client, because two clients
#: drawing a hollow triangle differently would make the same board two
#: puzzles. One or two characters each, which is what the symbolic form
#: subsystem assumes a tile holds.
FIGURE_GLYPHS: dict[tuple[str, str], str] = {
    ("triangle", "solid"): "▲",
    ("triangle", "hollow"): "△",
    ("triangle", "striped"): "◮",
    ("circle", "solid"): "●",
    ("circle", "hollow"): "○",
    ("circle", "striped"): "◍",
    ("square", "solid"): "■",
    ("square", "hollow"): "□",
    ("square", "striped"): "▨",
}

#: How many of the shape a count attribute means.
FIGURE_COUNTS: dict[str, int] = {"one": 1, "two": 2, "three": 3}


class FigureProvider:
    """Simple objects: every combination of a small attribute space.

    The content is the attribute space, so the whole of what this provider
    knows is in ``FIGURE_ATTRIBUTES`` and the two tables beside it. Eighty-one
    figures from four attributes of three values is the default; the space is
    a constructor argument because a game over two attributes is a legitimate
    smaller thing and should not need a second provider.

    Each attribute becomes a subtree of the taxonomy: a parent named for the
    attribute and one child per value, so ``find_groups`` over the taxonomy
    asks for figures sharing a value and ``find_siblings`` asks for the values
    of one attribute. That is the ordinary shape of a taxonomy and the reason
    no query had to learn anything about figures.
    """

    def __init__(
        self,
        attributes: Sequence[tuple[str, Sequence[str]]] = FIGURE_ATTRIBUTES,
        *,
        name: str = "figures",
        root_name: str = "figure",
        now: dt.datetime | None = None,
    ) -> None:
        if not attributes:
            raise ProviderError("a figure needs at least one attribute")
        space: list[tuple[str, tuple[str, ...]]] = []
        for attribute, values in attributes:
            chosen = tuple(values)
            if len(chosen) < 2:
                raise ProviderError(
                    f"attribute {attribute!r} has {len(chosen)} values; an "
                    "attribute with one value distinguishes nothing"
                )
            if len(set(chosen)) != len(chosen):
                raise ProviderError(f"attribute {attribute!r} repeats a value")
            space.append((attribute, chosen))
        names = [attribute for attribute, _ in space]
        if len(set(names)) != len(names):
            raise ProviderError("two attributes share a name")
        if "shape" not in names or "fill" not in names:
            # The glyph is the shape at its fill, so both have to be present
            # for a figure to be drawable at all. Refused here rather than
            # producing figures with no glyph, which would import cleanly and
            # then fail every board.
            raise ProviderError(
                "a figure space needs a 'shape' and a 'fill' attribute, "
                "because the glyph is the one at the other"
            )
        self._space = tuple(space)
        self._name = name
        self._root = root_name
        self._now = now or dt.datetime.now(dt.timezone.utc)

    @property
    def attributes(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return self._space

    def combinations(self) -> list[dict[str, str]]:
        """Every figure in the space, in a fixed order.

        Built by widening one attribute at a time in declaration order rather
        than with a product helper, so the order is readable from the
        declaration and does not depend on any library's iteration.
        """
        rows: list[dict[str, str]] = [{}]
        for attribute, values in self._space:
            rows = [{**row, attribute: value} for row in rows for value in values]
        return rows

    def _figure_name(self, figure: dict[str, str]) -> str:
        """A readable name, which is also the spoken label.

        Attribute values in declaration order with the shape last, which is
        how English puts them: "two solid red triangles", not "triangle two
        solid red". Plural when the count is more than one, because a reader
        saying "two triangle" is a reader somebody stops trusting.
        """
        count = FIGURE_COUNTS.get(figure.get("count", "one"), 1)
        modifiers = [
            figure[attribute]
            for attribute, _ in self._space
            if attribute not in ("shape", "count")
        ]
        shape = figure["shape"] + ("s" if count > 1 else "")
        head = figure.get("count", "") if "count" in figure else ""
        parts = [part for part in (head, *modifiers, shape) if part]
        return " ".join(parts)

    def _key(self, figure: dict[str, str]) -> ProviderKey:
        return self._name + "/" + "-".join(
            figure[attribute] for attribute, _ in self._space
        )

    def _category_key(self, attribute: str, value: str | None = None) -> ProviderKey:
        if value is None:
            return f"{self._name}/category/{attribute}"
        return f"{self._name}/category/{attribute}/{value}"

    def _version(self) -> str:
        return "+".join(
            f"{attribute}:{len(values)}" for attribute, values in self._space
        )

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            name=self._name,
            kind=SourceKind.COMPUTED,
            version=self._version(),
            retrieved_at=self._now,
        )

    def freshness_status(self) -> ProviderFreshness:
        return ProviderFreshness(
            healthy=True,
            checked_at=self._now,
            age_days=0,
            detail=(
                f"generated from the attribute space {self._version()}; "
                "no upstream to age"
            ),
        )

    def load(self) -> ProviderBundle:
        root = self._category_key("root")
        categories: list[RawCategory] = [
            RawCategory(key=root, name=self._root, gloss="a simple figure")
        ]
        for attribute, values in self._space:
            categories.append(
                RawCategory(
                    key=self._category_key(attribute),
                    # Qualified by the attribute, so a value that is also a
                    # word in the lexicon ("square" is both a shape and a
                    # noun) cannot be mistaken for it by a reader of the
                    # taxonomy. Category identity already includes the
                    # taxonomy, so this is about legibility, not collision.
                    name=f"figure {attribute}",
                    parent_keys=(root,),
                    gloss=f"the {attribute} of a figure",
                )
            )
            for value in values:
                categories.append(
                    RawCategory(
                        key=self._category_key(attribute, value),
                        name=f"{value} {attribute}",
                        parent_keys=(self._category_key(attribute),),
                        gloss=f"a figure whose {attribute} is {value}",
                    )
                )

        entities: list[RawEntity] = []
        facts: list[RawFact] = []
        for figure in self.combinations():
            key = self._key(figure)
            glyph = FIGURE_GLYPHS.get((figure["shape"], figure["fill"]))
            if glyph is None:
                raise ProviderError(
                    f"no glyph for a {figure['fill']} {figure['shape']}; "
                    "FIGURE_GLYPHS needs one before that space can be built"
                )
            name = self._figure_name(figure)
            entities.append(
                RawEntity(
                    key=key,
                    name=name,
                    definition=f"a figure: {name}",
                    category_keys=tuple(
                        self._category_key(attribute, figure[attribute])
                        for attribute, _ in self._space
                    ),
                    confidence=CERTAIN,
                )
            )
            facts.extend(self._facts(key, figure, glyph, name))
        return ProviderBundle(
            descriptor=self.describe(),
            categories=tuple(categories),
            entities=tuple(entities),
            facts=tuple(facts),
        )

    def _facts(
        self, key: ProviderKey, figure: dict[str, str], glyph: str, name: str
    ) -> list[RawFact]:
        def fact(predicate: str, item: object) -> RawFact:
            return RawFact(
                subject_key=key,
                predicate=predicate,
                value=item,
                freshness_class=FRESHNESS,
                provenance_class=ProvenanceClass.SOURCED,
                confidence=CERTAIN,
            )

        produced = [
            fact(GLYPH, glyph),
            fact(SPOKEN, name),
            fact(SYMBOL_KIND, "FIGURE"),
        ]
        # Every attribute as its own fact as well as a membership. The
        # membership is how a query finds the figure; the fact is how a game
        # reads the attribute off a tile it already holds, without walking
        # category names and parsing them back into attribute and value.
        produced.extend(fact(attribute, figure[attribute]) for attribute, _ in self._space)
        if "count" in figure:
            produced.append(fact("count_value", FIGURE_COUNTS[figure["count"]]))
        return produced

    def fetch_entity(self, key: ProviderKey) -> RawEntity | None:
        for entity in self.load().entities:
            if entity.key == key:
                return entity
        return None


__all__ = [
    "CERTAIN",
    "FIGURE_ATTRIBUTES",
    "FIGURE_COUNTS",
    "FIGURE_GLYPHS",
    "GLYPH",
    "NUMBER_CATEGORIES",
    "SPOKEN",
    "SYMBOL_KIND",
    "VALUE",
    "FigureProvider",
    "NumberCategory",
    "NumberRangeProvider",
    "digit_sum",
    "is_fibonacci",
    "is_prime",
    "is_square",
    "is_triangular",
    "spoken_number",
]
