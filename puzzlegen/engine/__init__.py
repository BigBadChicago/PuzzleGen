"""The engine: plugin contract, registry, generation, records and storage."""

from .pipeline import GenerationOutcome, GenerationPipeline, day_key_for
from .plugin import (
    AccessibilityDeclaration,
    DifficultyMeasurement,
    DifficultyThresholds,
    GameDescriptor,
    GamePlugin,
    GenerationContext,
    PresentationModel,
    Puzzle,
    PuzzleCandidate,
    Score,
    SessionTelemetry,
    ShareArtifact,
    VerificationResult,
)
from .records import (
    DifficultySummary,
    GenerationInputs,
    GenerationTrace,
    PuzzleManifest,
    PuzzleRecord,
    PuzzleStatus,
    RejectionTally,
    VerificationSummary,
    VersionSet,
)
from .registry import ActivationEvent, GameRegistry, RegisteredGame
from .runner import DailyRunner, DayResult
from .solvers import (
    SearchResult,
    collapse_equivalent,
    count_perfect_matchings,
    enumerate_partitions,
    enumerate_paths,
    minimal_paths,
)
from .storage import (
    EngineRepositories,
    ManifestRepository,
    PuzzleRepository,
    TraceRepository,
)

from .uniqueness import UniquenessVerdict
from .verifier import DifficultyCalibration, EvaluationOutcome, PuzzleVerifier

__all__ = [
    "AccessibilityDeclaration",
    "ActivationEvent",
    "DailyRunner",
    "DayResult",
    "DifficultyCalibration",
    "DifficultyMeasurement",
    "DifficultyThresholds",
    "EvaluationOutcome",
    "PuzzleVerifier",
    "SearchResult",
    "UniquenessVerdict",
    "collapse_equivalent",
    "count_perfect_matchings",
    "enumerate_partitions",
    "enumerate_paths",
    "minimal_paths",
    "DifficultySummary",
    "EngineRepositories",
    "GameDescriptor",
    "GamePlugin",
    "GameRegistry",
    "GenerationContext",
    "GenerationInputs",
    "GenerationOutcome",
    "GenerationPipeline",
    "GenerationTrace",
    "ManifestRepository",
    "PresentationModel",
    "Puzzle",
    "PuzzleCandidate",
    "PuzzleManifest",
    "PuzzleRecord",
    "PuzzleRepository",
    "PuzzleStatus",
    "RegisteredGame",
    "RejectionTally",
    "Score",
    "SessionTelemetry",
    "ShareArtifact",
    "TraceRepository",
    "VerificationResult",
    "VerificationSummary",
    "VersionSet",
    "day_key_for",
]
