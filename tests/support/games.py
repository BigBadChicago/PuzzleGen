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
    DifficultyReduction,
    UniquenessContract,
    VerificationCompleteness,
)
from puzzlegen.engine.plugin import (
    AccessibilityDeclaration,
    PuzzlePart,
    DifficultyMeasurement,
    DifficultyThresholds,
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
    ShareTokenSpec,
    StateSymbol,
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
                announcements={
                    "selected": "{label} selected",
                    "incorrect": "Not it. {attempts} tries so far",
                    "complete": "Solved in {attempts} tries",
                },
                state_symbols=(
                    StateSymbol(
                        name="idle", symbol="\u25cb", label="not chosen"
                    ),
                    StateSymbol(
                        name="chosen", symbol="\u25c9", label="chosen", colour="#3b6"
                    ),
                    StateSymbol(
                        name="wrong", symbol="\u2715", label="wrong", colour="#c33"
                    ),
                ),
            ),
            share_tokens=(
                ShareTokenSpec(
                    name="correct", glyph="\U0001f7e9", plain="#", label="correct"
                ),
                ShareTokenSpec(
                    name="missed", glyph="\u2b1c", plain=".", label="missed"
                ),
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

    def grade_move(
        self, puzzle: Puzzle, payload: Mapping[str, Any], state: Mapping[str, Any]
    ) -> MoveJudgement:
        answer = puzzle.solution["answer"]
        chosen = payload.get("choice")
        if chosen not in puzzle.payload["options"]:
            return MoveJudgement(
                correct=False,
                state=dict(state),
                note="that option is not on the board",
            )
        tried = [*state.get("tried", []), chosen]
        correct = chosen == answer
        # Every wrong option tried means the only one left is the answer, so
        # the puzzle is over: continuing would be a guess with no choice in it.
        wrong_options = {
            option for option in puzzle.payload["options"] if option != answer
        }
        exhausted = wrong_options.issubset(set(tried))
        return MoveJudgement(
            correct=correct,
            complete=correct or exhausted,
            solved=correct,
            state={"tried": tried, "selected": chosen},
            note="" if correct else "shares the category",
        )

    def get_hint(
        self, puzzle: Puzzle, state: Mapping[str, Any], hints_used: int
    ) -> Hint:
        options = list(puzzle.payload["options"])
        answer = puzzle.solution["answer"]
        wrong = [option for option in options if option != answer]
        if hints_used >= len(wrong):
            return Hint(
                text="no hints left", cost=0, exhausted=True
            )
        eliminated = wrong[hints_used]
        return Hint(
            text="one of the four belongs with the rest",
            reveals=(eliminated,),
            cost=10,
            exhausted=hints_used + 1 >= len(wrong),
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


class ChainGame(OddOneOutGame):
    """A two-stage game, exercising protocol 1.3 end to end.

    Stage one is the odd one out. Stage two asks for the first letter of
    stage one's answer, which is the smallest honest gate there is: before the
    odd one is known, every offered letter is possible, and afterwards exactly
    one is. The engine checks both halves, so this game is what a test can
    point at to show that a chain is measured rather than described.

    It also asks two rounds of content questions, and the second depends on
    the first: whichever pool came back larger decides which category the
    decoys are drawn from. And it reports one difficulty axis per stage,
    reduced by maximum, because a board is as hard as its hardest stage.
    """

    def describe(self) -> GameDescriptor:
        base = super().describe()
        return GameDescriptor(
            game_id="chain",
            display_name="Chain",
            game_version=self._version,
            uniqueness_contract=UniquenessContract.EXACTLY_ONE,
            supported_bands=base.supported_bands,
            difficulty_thresholds=base.difficulty_thresholds,
            verification_completeness=VerificationCompleteness.COMPLETE,
            accessibility=base.accessibility,
            share_tokens=base.share_tokens,
            content_rounds=2,
            staged=True,
            difficulty_reduction=DifficultyReduction.MAXIMUM,
            description="Find the odd one, then its first letter.",
        )

    def refine_content_requirements(
        self,
        round_index: int,
        results: Mapping[str, Any],
        *,
        difficulty_target: DifficultyBand,
        locale: str,
        day_key: str,
    ) -> Sequence[ContentRequirement]:
        family = len(results["family"].entities)
        outsiders = len(results["outsiders"].entities)
        # The branch is the point: this query could not have been written
        # before round one came back.
        category = "seabird" if family >= outsiders else "feline"
        return [
            ContentRequirement(
                name="decoys",
                query=ContentQuery(
                    operation=Operation.FIND_CATEGORY_MEMBERS,
                    category=category,
                    lang=locale,
                    limit=10,
                ),
                minimum=1,
            )
        ]

    def generate_candidates(
        self, context: GenerationContext
    ) -> Sequence[PuzzleCandidate]:
        decoys = list(context.result("decoys").entities)
        candidates: list[PuzzleCandidate] = []
        for candidate in super().generate_candidates(context):
            payload = dict(candidate.payload)
            labels = dict(payload["labels"])
            initials = {labels[option][:1].upper() for option in labels}
            # Two stages need two distinct letters, or stage two is decided
            # before stage one and the chain is decoration.
            if len(initials) < 2:
                continue
            refs = list(candidate.fact_refs)
            for view in decoys[:1]:
                refs.extend(view.dependency_ids())
            payload["decoy_initials"] = sorted(
                {view.name[:1].upper() for view in decoys[:2]}
            )
            candidates.append(
                PuzzleCandidate(
                    candidate_id=candidate.candidate_id,
                    payload=payload,
                    fact_refs=tuple(dict.fromkeys(refs)),
                    rationale=f"{candidate.rationale}, then its initial",
                )
            )
        return candidates

    def assemble(self, candidate: PuzzleCandidate, rng: DeterministicRng) -> Puzzle:
        flat = super().assemble(candidate, rng)
        labels = dict(flat.payload["labels"])
        options = list(flat.payload["options"])
        answer = flat.solution["answer"]
        letter = labels[answer][:1].upper()
        letters = sorted(
            {labels[option][:1].upper() for option in options}
            | set(candidate.payload.get("decoy_initials", ()))
        )
        pick = PuzzlePart(
            part_id="pick",
            payload={"options": options, "labels": labels},
            solution={"answer": answer},
        )
        initial = PuzzlePart(
            part_id="initial",
            payload={"letters": letters},
            solution={"letter": letter},
            requires=("pick",),
        )
        return Puzzle(
            game_id="chain",
            payload={
                "options": options,
                "labels": labels,
                "letters": letters,
                "prompt": flat.payload["prompt"],
            },
            solution={"pick": dict(pick.solution), "initial": dict(initial.solution)},
            fact_refs=flat.fact_refs,
            presentation={"layout": "list"},
            candidate_id=candidate.candidate_id,
            parts=(pick, initial),
        )

    def verify(self, puzzle: Puzzle) -> VerificationResult:
        """The whole board: one answer per stage, so one answer overall."""
        solution = {
            "pick": dict(puzzle.solution["pick"]),
            "initial": dict(puzzle.solution["initial"]),
        }
        return VerificationResult(
            solvable=True,
            solution_count=1,
            completeness=VerificationCompleteness.COMPLETE,
            states_examined=len(puzzle.payload["options"])
            * len(puzzle.payload["letters"]),
            solutions=(solution,),
        )

    def verify_part(
        self,
        puzzle: Puzzle,
        part_id: str,
        known: Mapping[str, Mapping[str, Any]],
    ) -> VerificationResult:
        part = puzzle.part(part_id)
        if part_id == "pick":
            options = list(part.payload["options"])
            answer = part.solution["answer"]
            return VerificationResult(
                solvable=True,
                solution_count=1,
                completeness=VerificationCompleteness.COMPLETE,
                states_examined=len(options),
                solutions=({"answer": answer},),
            )

        letters = list(part.payload["letters"])
        if "pick" not in known:
            # Nothing is known about the odd one, so every letter offered is
            # still a possible answer. This is the count the engine reads as
            # evidence that the stage is really gated.
            return VerificationResult(
                solvable=True,
                solution_count=len(letters),
                completeness=VerificationCompleteness.COMPLETE,
                states_examined=len(letters),
                solutions=tuple({"letter": letter} for letter in letters),
            )
        labels = dict(puzzle.payload["labels"])
        letter = labels[known["pick"]["answer"]][:1].upper()
        return VerificationResult(
            solvable=True,
            solution_count=1,
            completeness=VerificationCompleteness.COMPLETE,
            states_examined=len(letters),
            solutions=({"letter": letter},),
        )

    def measure_difficulty(
        self, puzzle: Puzzle, verification: VerificationResult
    ) -> DifficultyMeasurement:
        axes = {
            "pick": min(1.0, len(puzzle.payload["options"]) / 10.0),
            "initial": min(1.0, len(puzzle.payload["letters"]) / 10.0),
        }
        return DifficultyMeasurement(
            score=max(axes.values()),
            confidence=0.7,
            features={"states_examined": float(verification.states_examined)},
            axes=axes,
        )


class UngatedChainGame(ChainGame):
    """A game whose second stage is already decided without the first."""

    def verify_part(
        self,
        puzzle: Puzzle,
        part_id: str,
        known: Mapping[str, Mapping[str, Any]],
    ) -> VerificationResult:
        if part_id == "initial":
            # Answers the stage regardless of what is known, which is what a
            # decorative dependency looks like from the engine's side.
            return super().verify_part(
                puzzle, part_id, {"pick": dict(puzzle.solution["pick"])}
            )
        return super().verify_part(puzzle, part_id, known)


class DeadEndChainGame(ChainGame):
    """A game whose second stage stays ambiguous once the first is solved."""

    def verify_part(
        self,
        puzzle: Puzzle,
        part_id: str,
        known: Mapping[str, Mapping[str, Any]],
    ) -> VerificationResult:
        if part_id == "initial":
            return super().verify_part(puzzle, part_id, {})
        return super().verify_part(puzzle, part_id, known)


class UndeclaredStageGame(ChainGame):
    """A game that emits stages without declaring that it is staged."""

    def describe(self) -> GameDescriptor:
        base = super().describe()
        return GameDescriptor(
            game_id=base.game_id,
            display_name=base.display_name,
            game_version=base.game_version,
            uniqueness_contract=base.uniqueness_contract,
            supported_bands=base.supported_bands,
            difficulty_thresholds=base.difficulty_thresholds,
            verification_completeness=base.verification_completeness,
            accessibility=base.accessibility,
            share_tokens=base.share_tokens,
            content_rounds=base.content_rounds,
            staged=False,
            difficulty_reduction=base.difficulty_reduction,
        )


class MisreducedAxesGame(ChainGame):
    """A game that bands against a number its own axes do not produce."""

    def measure_difficulty(
        self, puzzle: Puzzle, verification: VerificationResult
    ) -> DifficultyMeasurement:
        measured = super().measure_difficulty(puzzle, verification)
        return DifficultyMeasurement(
            # Half the hardest axis: wrong under every reduction, where
            # taking the gentler axis would read as right whenever the two
            # axes happened to agree.
            score=max(measured.axes.values()) / 2,
            confidence=measured.confidence,
            features=measured.features,
            axes=measured.axes,
        )


class RepeatingRefiningGame(ChainGame):
    """A game whose second round asks again for a name round one answered."""

    def refine_content_requirements(
        self,
        round_index: int,
        results: Mapping[str, Any],
        *,
        difficulty_target: DifficultyBand,
        locale: str,
        day_key: str,
    ) -> Sequence[ContentRequirement]:
        return [
            ContentRequirement(
                name="family",
                query=ContentQuery(
                    operation=Operation.FIND_CATEGORY_MEMBERS, category="seabird"
                ),
                minimum=1,
            )
        ]


class UnrefiningGame(OddOneOutGame):
    """A game declaring two content rounds and implementing none."""

    def describe(self) -> GameDescriptor:
        base = super().describe()
        return GameDescriptor(
            game_id=base.game_id,
            display_name=base.display_name,
            game_version=base.game_version,
            uniqueness_contract=base.uniqueness_contract,
            supported_bands=base.supported_bands,
            difficulty_thresholds=base.difficulty_thresholds,
            verification_completeness=base.verification_completeness,
            accessibility=base.accessibility,
            share_tokens=base.share_tokens,
            content_rounds=2,
        )
