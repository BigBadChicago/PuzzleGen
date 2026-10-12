"""Six broken grouping games, each breaking one promise on purpose.

They exist to prove the engine catches what it claims to. A gate that has
never rejected anything is a gate nobody has tested, and the cheapest way for
an engine to look correct is to be handed only correct games.

Each subclass changes exactly one thing, so a test that fails tells you which
promise went unenforced rather than that something somewhere broke. They live
under ``tests`` and not in the shipped package, because a broken game inside
``puzzlegen/games`` would eventually be copied by somebody building a real one.

They are failing by design. Fixing them is not the goal; a day when one of
them passes the engine's gates is a day the engine stopped working.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from puzzlegen.core.types import VerificationCompleteness
from puzzlegen.engine.plugin import (
    MoveJudgement,
    Puzzle,
    SessionTelemetry,
    ShareArtifact,
    VerificationResult,
)
from puzzlegen.engine.uniqueness import COLLAPSED_SOLUTIONS
from puzzlegen_grouping.plugin import GroupingGame


class SilentToleranceGame(GroupingGame):
    """Claims uniqueness up to tolerance without saying what it collapsed.

    The contract obliges a game to report ``collapsed_solutions`` precisely so
    the engine can tell a collapse from a game that never applied its own
    tolerance rule. Without the number, "one solution" might mean the board is
    unique or might mean nothing was ever compared, and those look identical
    from outside.
    """

    def verify(self, puzzle: Puzzle) -> VerificationResult:
        honest = super().verify(puzzle)
        metrics = {
            key: value
            for key, value in honest.metrics.items()
            if key != COLLAPSED_SOLUTIONS
        }
        return VerificationResult(
            solvable=honest.solvable,
            solution_count=honest.solution_count,
            completeness=honest.completeness,
            states_examined=honest.states_examined,
            solutions=honest.solutions,
            solutions_truncated=honest.solutions_truncated,
            metrics=metrics,
            detail="tolerance applied, evidence withheld",
        )


class UnexaminedGame(GroupingGame):
    """Claims a complete enumeration having examined nothing.

    The dangerous version of an incomplete search: not a game that admits it
    ran out of budget, but one that reports COMPLETE with a state count that
    cannot possibly have produced an answer. The engine checks the claim
    against the evidence rather than taking the completeness flag on trust.
    """

    def verify(self, puzzle: Puzzle) -> VerificationResult:
        return VerificationResult(
            solvable=True,
            solution_count=1,
            completeness=VerificationCompleteness.COMPLETE,
            states_examined=0,
            solutions=(
                {
                    "groups": [
                        sorted(group["members"])
                        for group in puzzle.solution["groups"]
                    ]
                },
            ),
            metrics={COLLAPSED_SOLUTIONS: 0.0},
            detail="answer known in advance, search skipped",
        )


class PhantomSolutionGame(GroupingGame):
    """Publishes an answer nothing verified, by claiming a truncated list.

    The dangerous relative of ``UnexaminedGame``. That one claims a complete
    enumeration having examined nothing, which the engine catches on the state
    count. This one reports an honest state count and an honest solution count
    and simply declines to say what it found, marking the list truncated.

    Truncation is a legitimate state: a verifier that finds thousands of
    solutions caps what it lists. What makes this a lie is listing none of
    them, because the engine compares the board's stated answer against the
    listed solutions, and an empty list is a comparison against nothing. A
    board whose answer is pure invention then verifies, satisfies its
    contract, publishes, and marks a correct player wrong.
    """

    def verify(self, puzzle: Puzzle) -> VerificationResult:
        honest = super().verify(puzzle)
        return VerificationResult(
            solvable=honest.solvable,
            solution_count=honest.solution_count,
            completeness=honest.completeness,
            states_examined=honest.states_examined,
            solutions=(),
            solutions_truncated=True,
            metrics=dict(honest.metrics),
            detail="answer withheld behind a truncation that lists nothing",
        )


class DriftingGame(GroupingGame):
    """Grades the same move differently on a second look.

    The failure ``DeterminismError`` exists for. A counter kept on the plugin
    survives across calls, so replaying a ledger produces different outcomes
    from the ones recorded, and a session's stored history stops describing
    the game that was played.

    Nothing about this is exotic: memoising a feasibility check across calls
    instead of within one replay pass does exactly this, which is why the real
    game passes its cache down rather than keeping one.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._calls = 0

    def grade_move(
        self, puzzle: Puzzle, payload: Mapping[str, Any], state: Mapping[str, Any]
    ) -> MoveJudgement:
        self._calls += 1
        honest = super().grade_move(puzzle, payload, state)
        if self._calls % 2 == 0:
            return MoveJudgement(
                correct=not honest.correct,
                state=honest.state,
                note="graded from a counter rather than from the moves",
            )
        return honest


class GenerousGame(GroupingGame):
    """Marks a wrong answer correct.

    The worst failure a grader can have, because everything downstream looks
    healthy: the session completes, the score is computed, the share is built,
    and the only thing wrong is the answer. Only a test that submits a known
    wrong group and expects a refusal catches it.
    """

    def grade_move(
        self, puzzle: Puzzle, payload: Mapping[str, Any], state: Mapping[str, Any]
    ) -> MoveJudgement:
        honest = super().grade_move(puzzle, payload, state)
        if honest.correct:
            return honest
        return MoveJudgement(
            correct=True,
            state=honest.state,
            note="close enough",
        )


class LoudShareGame(GroupingGame):
    """Emits a share token the descriptor never declared.

    The share vocabulary is closed so the engine can hold a codepoint
    allowlist for share text: an undeclared token is a protocol error rather
    than a content rejection. A game that invents one is a game whose shares
    cannot be rendered safely, and the failure would otherwise surface as a
    stray glyph in somebody's paste.
    """

    def create_share_artifact(
        self, puzzle: Puzzle, telemetry: SessionTelemetry, day_key: str
    ) -> ShareArtifact:
        honest = super().create_share_artifact(puzzle, telemetry, day_key)
        return ShareArtifact(
            game_id=honest.game_id,
            day_key=honest.day_key,
            outcome=honest.outcome,
            tokens=(*honest.tokens, ("jackpot",)),
            headline=honest.headline,
            detail=honest.detail,
        )


#: Every broken game, with the promise it breaks. Kept as data so a test can
#: assert the set is complete rather than trusting that five classes above are
#: the five that were meant.
BROKEN_GAMES = {
    "phantom_solution": (
        PhantomSolutionGame,
        "withholds every solution behind a truncated list, so the board's "
        "stated answer is compared against nothing",
    ),
    "silent_tolerance": (
        SilentToleranceGame,
        "claims a tolerance collapse without reporting collapsed_solutions",
    ),
    "unexamined": (
        UnexaminedGame,
        "claims a complete enumeration having examined zero states",
    ),
    "drifting": (
        DriftingGame,
        "grades the same move differently on a second look",
    ),
    "generous": (
        GenerousGame,
        "marks a wrong answer correct",
    ),
    "loud_share": (
        LoudShareGame,
        "emits a share token the descriptor never declared",
    ),
}


__all__ = [
    "BROKEN_GAMES",
    "DriftingGame",
    "GenerousGame",
    "LoudShareGame",
    "PhantomSolutionGame",
    "SilentToleranceGame",
    "UnexaminedGame",
]
