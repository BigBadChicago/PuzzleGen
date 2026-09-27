"""Enumerations shared across every layer.

These are in ``core`` rather than ``graph`` because the engine, the content
service and the plugin protocol all speak them. A value that crosses the
plugin boundary must be a stable string, so every enum is a StrEnum and every
member's value is its own name.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field

#: Confidence in a fact, on [0, 1]. Not a probability in any calibrated sense;
#: it is an ordering used by gates and by review prioritisation.
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]

#: Similarity on [0, 1] after normalisation. Raw Leacock-Chodorow and Resnik
#: scores are unbounded above, so strategies normalise before returning.
Similarity = Annotated[float, Field(ge=0.0, le=1.0)]


class ProvenanceClass(StrEnum):
    """How an assertion came to be believed.

    The distinction drives review policy: JUDGED assertions cannot go active
    without a human, COMPUTED assertions are invalidated automatically when
    their inputs are, and SOURCED assertions are re-verified on a freshness
    schedule.
    """

    SOURCED = "SOURCED"
    COMPUTED = "COMPUTED"
    JUDGED = "JUDGED"


class SourceKind(StrEnum):
    STRUCTURED_PUBLIC = "STRUCTURED_PUBLIC"
    LEXICAL = "LEXICAL"
    CURATED_INTERNAL = "CURATED_INTERNAL"
    COMPUTED = "COMPUTED"
    HUMAN = "HUMAN"


class ReviewStatus(StrEnum):
    """One lifecycle for every governed record.

    Entities, facts, relationships and categories share this enum rather than
    each having their own. A single lifecycle means one set of transition
    rules, one curator dashboard, and one definition of "eligible for a
    puzzle", which is ACTIVE and nothing else.
    """

    CANDIDATE = "CANDIDATE"
    AUTO_VALIDATED = "AUTO_VALIDATED"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


#: The only status from which content may enter a puzzle. Defined once so no
#: gate can disagree with another about what "usable" means.
USABLE_STATUS = frozenset({ReviewStatus.ACTIVE})

#: Statuses that indicate a record was deliberately withdrawn. Content in
#: these states invalidates any puzzle that depends on it.
WITHDRAWN_STATUS = frozenset(
    {ReviewStatus.QUARANTINED, ReviewStatus.REJECTED, ReviewStatus.RETIRED}
)


class FreshnessClass(StrEnum):
    """How fast a fact is expected to go wrong.

    Applied per fact, not per entity: a mountain's elevation is STATIC while
    the same mountain's visitor count is PERIODIC.
    """

    STATIC = "STATIC"
    SLOW_CHANGING = "SLOW_CHANGING"
    PERIODIC = "PERIODIC"
    TIME_SENSITIVE = "TIME_SENSITIVE"
    VOLATILE = "VOLATILE"


class FrequencyBand(StrEnum):
    """Coarse bands over Zipf-style scores.

    Bands rather than raw scores cross the plugin boundary, because the
    underlying frequency dataset is replaceable and its absolute scale is not
    part of the contract.
    """

    VERY_COMMON = "VERY_COMMON"
    COMMON = "COMMON"
    UNCOMMON = "UNCOMMON"
    RARE = "RARE"
    OBSCURE = "OBSCURE"


class DifficultyBand(StrEnum):
    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"
    EXPERT = "EXPERT"


class UniquenessContract(StrEnum):
    """What a game promises about its solution space.

    Declared by the plugin, enforced by the engine's verifier. A plugin cannot
    both declare EXACTLY_ONE and publish a puzzle with two solutions, because
    the engine checks the contract against the verifier's own count rather
    than against the plugin's claim.
    """

    EXACTLY_ONE = "EXACTLY_ONE"
    EXACTLY_N = "EXACTLY_N"
    UNIQUE_GROUPING = "UNIQUE_GROUPING"
    UNIQUE_ORDERING = "UNIQUE_ORDERING"
    UNIQUE_DEDUCTION = "UNIQUE_DEDUCTION"
    NO_ALTERNATE_INTERPRETATION = "NO_ALTERNATE_INTERPRETATION"
    UNIQUE_UP_TO_TOLERANCE = "UNIQUE_UP_TO_TOLERANCE"
    UNIQUE_MINIMAL_PATH = "UNIQUE_MINIMAL_PATH"


class VerificationCompleteness(StrEnum):
    """Whether a verifier enumerated the whole solution space.

    A puzzle may only be published with SOUND_INCOMPLETE if its game declared
    that mode in advance and supplied a bound. Silently degrading from
    COMPLETE to SOUND_INCOMPLETE is the failure this enum exists to prevent.
    """

    COMPLETE = "COMPLETE"
    SOUND_INCOMPLETE = "SOUND_INCOMPLETE"


class ValueKind(StrEnum):
    """The type of a fact's object when it is a literal rather than an entity."""

    STRING = "STRING"
    INTEGER = "INTEGER"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    DATE = "DATE"


class DependencyRefKind(StrEnum):
    """What kind of graph record a published puzzle depends on."""

    ENTITY = "ENTITY"
    FACT = "FACT"
    RELATIONSHIP = "RELATIONSHIP"
    CATEGORY = "CATEGORY"
