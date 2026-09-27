"""A deliberately minimal game, used only to exercise the engine.

It exists so phase 4 can be tested without the real reference games, and it
doubles as the smallest possible worked example of the plugin contract: every
method is implemented, nothing is stubbed, and it is subject to the same gates
a third-party game would face.

It lives under ``tests`` rather than ``puzzlegen/games`` on purpose. A test
fixture in the shipped package would be shipped, and its shortcuts would
eventually be copied by someone building a real game.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from puzzlegen.content.query import ContentQuery, ContentRequirement, Operation
from puzzlegen.core.rng import DeterministicRng
from puzzlegen.core.types import (
    DifficultyBand,
    UniquenessContract,
    VerificationCompleteness,
)
from puzzlegen.engine.plugin import (
    AccessibilityDeclaration,
    DifficultyMeasurement,
    DifficultyThresholds,
    GameDescriptor,
    GenerationContext,
    PresentationModel,
    Puzzle,
    PuzzleCandidate,
    Score,
    SessionTelemetry,
    ShareArtifact,
    VerificationResult,
)


class OddOneOutGame:
    """Pick the entity that does not share the group's category."""

    def __init__(
        self,
        *,
        game_id: str = "oddoneout",
        version: str = "1.0.0",
        candidate_count: int = 6,
    ) -> None:
        self._game_id = game_id
        self._version = version
        self._candidate_count = candidate_count

    def describe(self) -> GameDescriptor:
        return GameDescriptor(
            game_id=self._game_id,
            display_name="Odd One Out",
            game_version=self._version,
            uniqueness_contract=UniquenessContract.EXACTLY_ONE,
            supported_bands=(DifficultyBand.EASY, DifficultyBand.MEDIUM),
            # Four options scores 0.4, which lands in MEDIUM with this cutoff.
            difficulty_thresholds=DifficultyThresholds(cutoffs=(0.3,)),
            verification_completeness=VerificationCompleteness.COMPLETE,
            accessibility=AccessibilityDeclaration(
                element_kinds=("option",),
                keyboard_model="list",
                announcements={"selected": "{label} selected"},
            ),
            description="Four entities share a category; one does not.",
        )

    def get_content_requirements(
        self, *, difficulty_target: DifficultyBand, locale: str, day_key: str
    ) -> Sequence[ContentRequirement]:
        return [
            ContentRequirement(
                name="family",
                query=ContentQuery(
                    operation=Operation.FIND_CATEGORY_MEMBERS,
                    category="feline",
                    lang=locale,
                    limit=20,
                ),
                minimum=3,
            ),
            ContentRequirement(
                name="outsiders",
                query=ContentQuery(
                    operation=Operation.FIND_CATEGORY_MEMBERS,
                    category="seabird",
                    lang=locale,
                    limit=20,
                ),
                minimum=1,
            ),
        ]

    def generate_candidates(
        self, context: GenerationContext
    ) -> Sequence[PuzzleCandidate]:
        family = list(context.result("family").entities)
        outsiders = list(context.result("outsiders").entities)
        rng = context.derive("pick")

        candidates: list[PuzzleCandidate] = []
        for index in range(self._candidate_count):
            picker = rng.derive(f"draw:{index}")
            members = picker.sample(family, 3)
            odd = picker.choice(outsiders)
            refs: list[str] = []
            for view in (*members, odd):
                refs.extend(view.dependency_ids())
            candidates.append(
                PuzzleCandidate(
                    candidate_id=f"{context.day_key}:{index}",
                    payload={
                        "members": [v.entity_id for v in members],
                        "odd": odd.entity_id,
                        "labels": {
                            v.entity_id: v.name for v in (*members, odd)
                        },
                        "category": members[0].types[0] if members[0].types else "",
                    },
                    fact_refs=tuple(dict.fromkeys(refs)),
                    rationale=f"three felines and one seabird, draw {index}",
                )
            )
        return candidates

    def assemble(self, candidate: PuzzleCandidate, rng: DeterministicRng) -> Puzzle:
        payload = dict(candidate.payload)
        options = [*payload["members"], payload["odd"]]
        shuffled = rng.shuffled(sorted(options))
        return Puzzle(
            game_id=self._game_id,
            payload={
                "options": shuffled,
                "labels": payload["labels"],
                "prompt": f"Which one is not a {payload['category']}?",
            },
            solution={"answer": payload["odd"]},
            fact_refs=candidate.fact_refs,
            presentation={"layout": "list"},
            candidate_id=candidate.candidate_id,
        )

    def verify(self, puzzle: Puzzle) -> VerificationResult:
        """Exhaustive: try every option as the answer."""
        options = list(puzzle.payload["options"])
        answer = puzzle.solution["answer"]
        solutions = [{"answer": option} for option in options if option == answer]
        return VerificationResult(
            solvable=bool(solutions),
            solution_count=len(solutions),
            completeness=VerificationCompleteness.COMPLETE,
            states_examined=len(options),
            solutions=tuple(solutions),
            clue_validity={"prompt": bool(puzzle.payload.get("prompt"))},
        )

    def measure_difficulty(
        self, puzzle: Puzzle, verification: VerificationResult
    ) -> DifficultyMeasurement:
        options = len(puzzle.payload["options"])
        return DifficultyMeasurement(
            score=min(1.0, options / 10.0),
            confidence=0.8,
            features={
                "option_count": float(options),
                "states_examined": float(verification.states_examined),
            },
        )

    def score(self, puzzle: Puzzle, telemetry: SessionTelemetry) -> Score:
        if not telemetry.completed:
            return Score(points=0, breakdown={"base": 0})
        base = 100
        penalty = min(base, telemetry.mistakes * 20 + telemetry.hints_used * 10)
        return Score(
            points=base - penalty,
            breakdown={"base": base, "penalty": -penalty},
        )

    def render(
        self, puzzle: Puzzle, state: Mapping[str, Any], locale: str
    ) -> PresentationModel:
        labels = puzzle.payload["labels"]
        return PresentationModel(
            elements=tuple(
                {
                    "kind": "option",
                    "id": option,
                    "label": labels[option],
                    "selected": state.get("selected") == option,
                }
                for option in puzzle.payload["options"]
            ),
            layout="list",
            labels={"prompt": puzzle.payload["prompt"]},
            state=dict(state),
        )

    def create_share_artifact(
        self, puzzle: Puzzle, telemetry: SessionTelemetry, day_key: str
    ) -> ShareArtifact:
        mark = "correct" if telemetry.completed else "missed"
        return ShareArtifact(
            game_id=self._game_id,
            day_key=day_key,
            outcome=mark,
            tokens=((mark,),),
            headline=f"Odd One Out {day_key}",
            detail={"attempts": telemetry.attempts},
        )


class FabricatingGame(OddOneOutGame):
    """A game that invents a dependency it was never shown."""

    def generate_candidates(
        self, context: GenerationContext
    ) -> Sequence[PuzzleCandidate]:
        candidates = super().generate_candidates(context)
        return [
            PuzzleCandidate(
                candidate_id=c.candidate_id,
                payload=c.payload,
                fact_refs=(*c.fact_refs, "entity:invented"),
                rationale=c.rationale,
            )
            for c in candidates
        ]


class RefLessGame(OddOneOutGame):
    """A game that omits its dependencies."""

    def generate_candidates(
        self, context: GenerationContext
    ) -> Sequence[PuzzleCandidate]:
        return [
            PuzzleCandidate(
                candidate_id=c.candidate_id, payload=c.payload, fact_refs=()
            )
            for c in super().generate_candidates(context)
        ]


class ExplodingGame(OddOneOutGame):
    """A game whose assembly raises."""

    def assemble(self, candidate: PuzzleCandidate, rng: DeterministicRng) -> Puzzle:
        raise RuntimeError("assembly exploded")


class WideningGame(OddOneOutGame):
    """A game that adds unscreened references during assembly."""

    def assemble(self, candidate: PuzzleCandidate, rng: DeterministicRng) -> Puzzle:
        puzzle = super().assemble(candidate, rng)
        return Puzzle(
            game_id=puzzle.game_id,
            payload=puzzle.payload,
            solution=puzzle.solution,
            fact_refs=(*puzzle.fact_refs, "entity:sneaked-in"),
            presentation=puzzle.presentation,
            candidate_id=puzzle.candidate_id,
        )


class ImpatientGame(OddOneOutGame):
    """A game demanding content the snapshot cannot supply."""

    def get_content_requirements(
        self, *, difficulty_target: DifficultyBand, locale: str, day_key: str
    ) -> Sequence[ContentRequirement]:
        return [
            ContentRequirement(
                name="family",
                query=ContentQuery(
                    operation=Operation.FIND_CATEGORY_MEMBERS, category="feline"
                ),
                minimum=999,
            )
        ]
