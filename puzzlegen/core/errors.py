"""Errors and structured rejection reasons.

Rejection reasons are data, not log strings. Generation throws away the large
majority of what it produces, and the only way to answer "why did today's
generation fail" is to count rejections by machine-readable reason.
"""

from __future__ import annotations

from enum import StrEnum


class PuzzleGenError(Exception):
    """Base class for every error raised by the engine."""


class ConfigurationError(PuzzleGenError):
    """The engine or a plugin is misconfigured. Not recoverable at runtime."""


class SchemaVersionError(PuzzleGenError):
    """A stored record or snapshot was written by an incompatible schema."""


class StorageError(PuzzleGenError):
    """The storage backend failed or was used incorrectly."""


class NotFoundError(StorageError):
    """A record was requested by id and does not exist."""

    def __init__(self, collection: str, record_id: str) -> None:
        super().__init__(f"{collection}/{record_id} not found")
        self.collection = collection
        self.record_id = record_id


class ConflictError(StorageError):
    """A write would violate a uniqueness or lifecycle constraint."""


class ContentError(PuzzleGenError):
    """The knowledge graph is in a state the engine will not build on."""


class PolicyError(PuzzleGenError):
    """An operation was refused by content policy."""


class BoundaryViolationError(PuzzleGenError):
    """A game plugin attempted something outside its capability grant.

    Raised by the engine side of the plugin boundary, never by a plugin. If a
    plugin can trigger this it has already been contained; the error exists so
    containment is observable rather than silent.
    """


class VerificationError(PuzzleGenError):
    """A puzzle failed verification and must not be published."""


class DeterminismError(PuzzleGenError):
    """Identical inputs produced differing outputs."""


class RejectionReason(StrEnum):
    """Why a candidate was discarded.

    Every gate in the generation pipeline maps its failures onto exactly one of
    these. New gates add members; members are never repurposed, because
    historical telemetry is compared across engine versions.
    """

    # Content quality gates
    SEMANTIC_DISTANCE_TOO_HIGH = "SEMANTIC_DISTANCE_TOO_HIGH"
    SEMANTIC_DISTANCE_TOO_LOW = "SEMANTIC_DISTANCE_TOO_LOW"
    SEMANTIC_SPREAD_TOO_WIDE = "SEMANTIC_SPREAD_TOO_WIDE"
    EMBEDDING_OUTLIER = "EMBEDDING_OUTLIER"
    TAXONOMY_DEPTH_MISMATCH = "TAXONOMY_DEPTH_MISMATCH"
    WORD_FREQUENCY_MISMATCH = "WORD_FREQUENCY_MISMATCH"
    FREQUENCY_SPREAD_TOO_WIDE = "FREQUENCY_SPREAD_TOO_WIDE"

    # Governance gates
    STALE_SOURCE = "STALE_SOURCE"
    STALE_FACT = "STALE_FACT"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    MISSING_PROVENANCE = "MISSING_PROVENANCE"
    UNREVIEWED_JUDGED_FACT = "UNREVIEWED_JUDGED_FACT"
    POLICY_REJECTED = "POLICY_REJECTED"
    QUARANTINED_DEPENDENCY = "QUARANTINED_DEPENDENCY"
    DEPRECATED_SOURCE = "DEPRECATED_SOURCE"

    # Structural gates
    INSUFFICIENT_CANDIDATES = "INSUFFICIENT_CANDIDATES"
    DUPLICATE_ENTITY = "DUPLICATE_ENTITY"
    STRUCTURAL_RULE_VIOLATION = "STRUCTURAL_RULE_VIOLATION"
    MISSING_FACT_REFS = "MISSING_FACT_REFS"

    # Solver and fairness gates
    NO_SOLUTION = "NO_SOLUTION"
    MULTIPLE_SOLUTIONS = "MULTIPLE_SOLUTIONS"
    SOLUTION_COUNT_MISMATCH = "SOLUTION_COUNT_MISMATCH"
    UNIQUENESS_CONTRACT_VIOLATED = "UNIQUENESS_CONTRACT_VIOLATED"
    UNFAIR_COMBINATION = "UNFAIR_COMBINATION"
    AMBIGUOUS_CLUE = "AMBIGUOUS_CLUE"
    DIFFICULTY_OUT_OF_RANGE = "DIFFICULTY_OUT_OF_RANGE"
    VERIFICATION_UNSOUND = "VERIFICATION_UNSOUND"

    # Plugin gates
    PLUGIN_TIMEOUT = "PLUGIN_TIMEOUT"
    PLUGIN_PROTOCOL_ERROR = "PLUGIN_PROTOCOL_ERROR"
    PLUGIN_NONDETERMINISTIC = "PLUGIN_NONDETERMINISTIC"
    SOLUTION_LEAK = "SOLUTION_LEAK"


class Rejected(PuzzleGenError):
    """Raised by a gate to reject one candidate with a structured reason."""

    def __init__(
        self,
        reason: RejectionReason,
        detail: str = "",
        **context: object,
    ) -> None:
        super().__init__(f"{reason}: {detail}" if detail else str(reason))
        self.reason = reason
        self.detail = detail
        self.context = context
