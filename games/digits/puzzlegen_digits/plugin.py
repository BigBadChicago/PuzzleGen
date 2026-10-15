"""The eleven methods and the one optional twelfth, bound to the modules
that implement them.

A thin class on purpose. Everything it does lives in a module the engine gates
separately, and keeping the plugin itself trivial is what stops the protocol
surface from growing logic of its own: a bug found here would be a bug in the
binding, and there is almost nothing here to be wrong.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from puzzlegen.content.query import ContentRequirement
from puzzlegen.core.rng import DeterministicRng
from puzzlegen.core.types import DifficultyBand
from puzzlegen.engine.plugin import (
    DifficultyMeasurement,
    GameDescriptor,
    GenerationContext,
    Hint,
    MoveJudgement,
    PresentationModel,
    Puzzle,
    PuzzleCandidate,
    Score,
    SessionTelemetry,
    ShareArtifact,
    VerificationResult,
)
from puzzlegen.engine.witness import DeductionWitness

# Functions, not modules. The package exports ``verify`` and ``assemble`` as
# names, so ``from . import verify`` would bind the function that shadows the
# submodule rather than the submodule itself, and the failure surfaces as
# "'function' object has no attribute 'verify'" at the first call.
from .assemble import assemble as _assemble
from .content import content_requirements as _content_requirements
from .descriptor import DESCRIPTOR
from .difficulty import measure_difficulty as _measure_difficulty
from .generate import generate_candidates as _generate_candidates
from .play import create_share_artifact as _create_share_artifact
from .play import get_hint as _get_hint
from .play import grade_move as _grade_move
from .play import render as _render
from .play import score as _score
from .verify import verify as _verify
from .witness import witness_for as _witness_for


class DigitLockGame:
    """A secret code of digits, and the clues that entail it."""

    def __init__(self, *, salt: str = "") -> None:
        #: Disjoint puzzle streams for staging or tests, without changing any
        #: other input. Carried here rather than read from the environment,
        #: because a game reading its environment is a game whose output
        #: depends on where it ran.
        self._salt = salt

    def describe(self) -> GameDescriptor:
        return DESCRIPTOR

    def get_content_requirements(
        self, *, difficulty_target: DifficultyBand, locale: str, day_key: str
    ) -> Sequence[ContentRequirement]:
        return _content_requirements(
            difficulty_target=difficulty_target,
            locale=locale,
            day_key=day_key,
            salt=self._salt,
        )

    def generate_candidates(
        self, context: GenerationContext
    ) -> Sequence[PuzzleCandidate]:
        # The same salt the requirements were asked with, so the size order
        # generation ranks is the order the content was fetched in.
        return _generate_candidates(context, salt=self._salt)

    def assemble(self, candidate: PuzzleCandidate, rng: DeterministicRng) -> Puzzle:
        return _assemble(candidate, rng)

    def verify(self, puzzle: Puzzle) -> VerificationResult:
        return _verify(puzzle)

    def measure_difficulty(
        self, puzzle: Puzzle, verification: VerificationResult
    ) -> DifficultyMeasurement:
        return _measure_difficulty(puzzle, verification)

    def score(self, puzzle: Puzzle, telemetry: SessionTelemetry) -> Score:
        return _score(puzzle, telemetry)

    def grade_move(
        self, puzzle: Puzzle, payload: Mapping[str, Any], state: Mapping[str, Any]
    ) -> MoveJudgement:
        return _grade_move(puzzle, payload, state)

    def get_hint(
        self, puzzle: Puzzle, state: Mapping[str, Any], hints_used: int
    ) -> Hint:
        return _get_hint(puzzle, state, hints_used)

    def witness(self, puzzle: Puzzle) -> DeductionWitness:
        return _witness_for(puzzle)

    def render(
        self, puzzle: Puzzle, state: Mapping[str, Any], locale: str
    ) -> PresentationModel:
        return _render(puzzle, state, locale)

    def create_share_artifact(
        self, puzzle: Puzzle, telemetry: SessionTelemetry, day_key: str
    ) -> ShareArtifact:
        return _create_share_artifact(puzzle, telemetry, day_key)


__all__ = ["DigitLockGame"]
