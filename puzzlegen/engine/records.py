"""Records the engine owns.

Separate from the graph models because they have a different lifecycle: graph
records are governed content that changes as the world does, while these are
the output of a generation run and, once published, never change at all.

The manifest is the centre of the system's reproducibility claim. It records
every input that could alter the bytes of a puzzle, so a puzzle can be
regenerated years later and the result compared. A manifest missing one of
those inputs is worse than no manifest, because it claims a reproducibility it
cannot deliver.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..core import ids
from ..core.hashing import stable_hash
from ..core.types import (
    DifficultyBand,
    UniquenessContract,
    VerificationCompleteness,
)
from ..core.versions import (
    CONTENT_SCHEMA_VERSION,
    ENGINE_VERSION,
    PUZZLE_FORMAT_VERSION,
    SHARE_FORMAT_VERSION,
)


class PuzzleStatus(StrEnum):
    """Lifecycle of a generated puzzle."""

    DRAFT = "DRAFT"
    VERIFIED = "VERIFIED"
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"
    #: A dependency was retracted after publication. The puzzle stays readable
    #: for the record; it is never silently deleted or quietly regenerated.
    INVALIDATED = "INVALIDATED"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware UTC")
    return value.astimezone(dt.timezone.utc)


class VersionSet(_Frozen):
    """Every version that can change a generated puzzle's bytes."""

    engine: str = ENGINE_VERSION
    game: str
    content_schema: int = CONTENT_SCHEMA_VERSION
    puzzle_format: int = PUZZLE_FORMAT_VERSION
    share_format: int = SHARE_FORMAT_VERSION
    protocol: str
    #: source_id to version, copied from the snapshot at generation time.
    providers: dict[str, str] = Field(default_factory=dict)
    embedding_model: str | None = None
    embedding_model_version: str | None = None
    frequency_source: str | None = None
    frequency_source_version: str | None = None


class GenerationInputs(_Frozen):
    """The complete recipe for regenerating one puzzle."""

    day_key: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    game_id: str
    seed_hex: str = Field(min_length=32, max_length=128)
    seed_salt: str = ""
    snapshot_id: str
    snapshot_content_hash: str
    difficulty_target: DifficultyBand
    locale: str = "en"
    #: The candidate the game selected, so the attempt can be located in the
    #: trace rather than re-derived by guesswork.
    candidate_id: str = ""
    configuration: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.snapshot_id, ids.SNAPSHOT)
        return self


class VerificationSummary(_Frozen):
    """What verification proved, recorded rather than asserted."""

    solvable: bool
    solution_count: int = Field(ge=0)
    completeness: VerificationCompleteness
    states_examined: int = Field(default=0, ge=0)
    uniqueness_contract: UniquenessContract
    contract_satisfied: bool
    #: Hash of the intended solution. Lets a regenerated puzzle be compared
    #: against the published one without the manifest carrying the answer.
    solution_hash: str = ""
    detail: str = ""


class DifficultySummary(_Frozen):
    target: DifficultyBand
    measured_band: DifficultyBand
    score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    features: dict[str, float] = Field(default_factory=dict)

    @property
    def on_target(self) -> bool:
        return self.target is self.measured_band


class PuzzleRecord(_Frozen):
    """An assembled puzzle and its answer, before or after publication.

    Holds the solution; the manifest does not. Publishing a manifest therefore
    cannot leak an answer even by accident, because the answer is not in the
    document that gets published.
    """

    record: Literal["puzzle"] = "puzzle"
    id: str
    game_id: str
    day_key: str
    status: PuzzleStatus = PuzzleStatus.DRAFT
    payload: dict[str, Any]
    solution: dict[str, Any]
    presentation: dict[str, Any] = Field(default_factory=dict)
    fact_refs: tuple[str, ...]
    inputs: GenerationInputs
    versions: VersionSet
    created_at: dt.datetime
    schema_version: int = PUZZLE_FORMAT_VERSION

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.id, ids.PUZZLE)
        if not self.fact_refs:
            raise ValueError("a puzzle must record the graph records it used")
        if len(set(self.fact_refs)) != len(self.fact_refs):
            raise ValueError("fact_refs must be distinct")
        object.__setattr__(self, "created_at", _utc(self.created_at))
        return self

    def content_hash(self) -> str:
        """Hash of the puzzle itself, excluding status and timestamps."""
        return stable_hash(
            {
                "game_id": self.game_id,
                "payload": self.payload,
                "solution": self.solution,
                "fact_refs": sorted(self.fact_refs),
            }
        )


class PuzzleManifest(_Frozen):
    """The immutable published record of one daily puzzle.

    Carries no solution and no payload. It is the reproducibility contract and
    the dependency ledger, not a copy of the puzzle.
    """

    record: Literal["manifest"] = "manifest"
    id: str
    puzzle_id: str
    game_id: str
    day_key: str
    generation_id: str
    inputs: GenerationInputs
    versions: VersionSet
    verification: VerificationSummary
    difficulty: DifficultySummary
    fact_refs: tuple[str, ...]
    #: Hash of the puzzle record this manifest published.
    puzzle_content_hash: str
    published_at: dt.datetime
    schema_version: int = PUZZLE_FORMAT_VERSION

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.id, ids.MANIFEST)
        ids.require(self.puzzle_id, ids.PUZZLE)
        if not self.verification.solvable:
            raise ValueError("an unsolvable puzzle cannot be published")
        if not self.verification.contract_satisfied:
            raise ValueError(
                "a puzzle whose uniqueness contract is unsatisfied cannot be published"
            )
        if not self.fact_refs:
            raise ValueError("a manifest must record its dependencies")
        object.__setattr__(self, "published_at", _utc(self.published_at))
        return self

    def manifest_hash(self) -> str:
        """Identity of this manifest's content, for tamper detection."""
        return stable_hash(
            self.model_dump(mode="json", exclude={"published_at"})
        )

    def public_view(self) -> dict[str, Any]:
        """The part of a manifest that is safe to show a player.

        A manifest holds no solution, but it does hold ``fact_refs``, and a
        dependency list can betray an answer: a puzzle whose refs include one
        seabird among three felines has told the reader which one is the odd
        one out. Manifests are therefore internal records, and anything shown
        to a player goes through this view, which omits the dependency ledger
        and the content hashes entirely.
        """
        return {
            "game_id": self.game_id,
            "day_key": self.day_key,
            "difficulty_band": str(self.difficulty.measured_band),
            "uniqueness_contract": str(self.verification.uniqueness_contract),
            "game_version": self.versions.game,
            "engine_version": self.versions.engine,
            "published_at": self.published_at.isoformat(),
        }

    def is_reproducible_from(self, other: "PuzzleManifest") -> bool:
        """Whether two manifests describe the same regeneration recipe."""
        return (
            self.inputs == other.inputs
            and self.versions == other.versions
            and self.puzzle_content_hash == other.puzzle_content_hash
        )


class RejectionTally(_Frozen):
    """Structured counts of why candidates died, by stage."""

    stage: str
    reasons: dict[str, int] = Field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.reasons.values())


class GenerationTrace(_Frozen):
    """Everything needed to explain one generation run.

    Written whether generation succeeded or failed. A failed day with no trace
    is a day nobody can diagnose, and generation failures are the normal way a
    content problem first becomes visible.
    """

    record: Literal["trace"] = "trace"
    id: str
    day_key: str
    game_id: str
    inputs: GenerationInputs
    versions: VersionSet
    #: Named content queries the game declared, summarised. The full query is
    #: recorded rather than the results, which are large and re-derivable.
    content_requests: dict[str, dict[str, Any]] = Field(default_factory=dict)
    content_rejections: dict[str, int] = Field(default_factory=dict)
    candidates_offered: int = 0
    candidates_rejected: tuple[RejectionTally, ...] = ()
    candidates_assembled: int = 0
    selected_candidate_id: str | None = None
    puzzle_id: str | None = None
    manifest_id: str | None = None
    succeeded: bool = False
    failure_reason: str | None = None
    port_queries: int = 0
    port_results: int = 0
    created_at: dt.datetime
    schema_version: int = PUZZLE_FORMAT_VERSION

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.id, ids.GENERATION)
        if self.succeeded and self.puzzle_id is None:
            raise ValueError("a successful generation must name its puzzle")
        if not self.succeeded and self.failure_reason is None:
            raise ValueError("a failed generation must record why")
        object.__setattr__(self, "created_at", _utc(self.created_at))
        return self

    def total_rejected(self) -> int:
        return sum(tally.total for tally in self.candidates_rejected)

    def rejection_summary(self) -> dict[str, int]:
        """All rejection reasons flattened, for dashboards and comparisons."""
        summary: dict[str, int] = dict(self.content_rejections)
        for tally in self.candidates_rejected:
            for reason, count in tally.reasons.items():
                summary[reason] = summary.get(reason, 0) + count
        return summary
