"""The daily run.

One call produces a day's puzzles: for each active game, generate, verify and
publish. Failures are isolated per game, because one game's content problem
must not cost the others their day.

Game order comes from the registry, which sorts by id, so a run is
reproducible regardless of registration order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ..core.errors import ContentError, PuzzleGenError
from ..core.types import DifficultyBand
from .pipeline import GenerationOutcome, GenerationPipeline
from .records import PuzzleManifest
from .verifier import DifficultyCalibration


@dataclass
class DayResult:
    """What one day's run produced, per game."""

    day_key: str
    manifests: dict[str, PuzzleManifest] = field(default_factory=dict)
    outcomes: dict[str, GenerationOutcome] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    calibration: dict[str, DifficultyCalibration] = field(default_factory=dict)

    @property
    def published(self) -> tuple[str, ...]:
        return tuple(sorted(self.manifests))

    @property
    def complete(self) -> bool:
        return not self.failures

    def summary(self) -> dict[str, object]:
        return {
            "day": self.day_key,
            "published": list(self.published),
            "failed": {game: reason for game, reason in sorted(self.failures.items())},
            "attempted": len(self.outcomes) + len(self.failures),
        }


class DailyRunner:
    """Runs every active game for a day."""

    def __init__(
        self,
        pipeline: GenerationPipeline,
        *,
        default_band: DifficultyBand = DifficultyBand.MEDIUM,
        band_by_game: Mapping[str, DifficultyBand] | None = None,
        retry_bands: Sequence[DifficultyBand] = (),
    ) -> None:
        self._pipeline = pipeline
        self._default_band = default_band
        self._band_by_game = dict(band_by_game or {})
        # A game whose content cannot reach the requested band today should
        # still ship a puzzle. Retrying at a neighbouring band is preferable
        # to publishing nothing, and far preferable to publishing off-target
        # while claiming to be on it.
        self._retry_bands = tuple(retry_bands)

    def run(self, day_key: str, *, locale: str = "en") -> DayResult:
        result = DayResult(day_key=day_key)
        registry = self._pipeline._registry  # noqa: SLF001 - same package

        for entry in registry.schedule_for(day_key):
            game_id = entry.game_id
            target = self._band_by_game.get(game_id, self._default_band)
            bands = self._bands_to_try(target, entry.descriptor.supported_bands)

            outcome = None
            for band in bands:
                outcome = self._pipeline.generate(
                    game_id, day_key, difficulty_target=band, locale=locale
                )
                if outcome.succeeded:
                    break

            if outcome is None or not outcome.succeeded:
                reason = (
                    outcome.trace.failure_reason
                    if outcome is not None
                    else "no generation attempted"
                )
                result.failures[game_id] = reason or "unknown failure"
                continue

            result.outcomes[game_id] = outcome
            if outcome.evaluation and outcome.evaluation.difficulty:
                calibration = result.calibration.setdefault(
                    game_id, DifficultyCalibration(game_id=game_id)
                )
                calibration.record(
                    outcome.evaluation.difficulty.score,
                    outcome.evaluation.measured_band or target,
                )

            try:
                result.manifests[game_id] = self._pipeline.publish_outcome(outcome)
            except (ContentError, PuzzleGenError) as exc:
                result.failures[game_id] = f"publication refused: {exc}"

        return result

    def _bands_to_try(
        self, target: DifficultyBand, supported: Sequence[DifficultyBand]
    ) -> tuple[DifficultyBand, ...]:
        ordered = [target, *[b for b in self._retry_bands if b != target]]
        return tuple(b for b in ordered if b in supported)
