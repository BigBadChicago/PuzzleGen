"""Four broken missing_letters games, each breaking one promise on purpose.

They exist to prove the engine catches what it claims to. A gate that has
never rejected anything is a gate nobody has tested, and the cheapest way for
an engine to look correct is to be handed only correct games.

Each subclass changes exactly one thing, so a test that fails tells you which
promise went unenforced rather than that something somewhere broke. They live
under ``tests`` and not in the shipped package, because a broken game inside
the distribution would eventually be copied by somebody building a real one.

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
from puzzlegen_letters.plugin import MissingLettersGame


class IncuriousGame(MissingLettersGame):
    """Counts its own answer and never looks for a second one.

    The failure the uniqueness contract exists for, and the one this game is
    most exposed to: the answer is a word, so a board with two true readings
    looks perfect from the inside. A verifier that reports its own intent
    rather than its own search would publish the board that marks a correct
    player wrong.
    """

    def verify(self, puzzle: Puzzle) -> VerificationResult:
        return VerificationResult(
            solvable=True,
            solution_count=1,
            completeness=VerificationCompleteness.COMPLETE,
            states_examined=0,
            solutions=(dict(puzzle.solution),),
            detail="answer known in advance, vocabulary never scanned",
        )


class OverreachingGame(MissingLettersGame):
    """Claims a complete enumeration of a vocabulary it could not have read.

    Declared SOUND_INCOMPLETE and reports a state count beyond its own bound,
    which is the claim the engine checks against the declaration rather than
    taking on trust.
    """

    def verify(self, puzzle: Puzzle) -> VerificationResult:
        honest = super().verify(puzzle)
        return VerificationResult(
            solvable=honest.solvable,
            solution_count=honest.solution_count,
            completeness=VerificationCompleteness.SOUND_INCOMPLETE,
            states_examined=self.describe().search_bound * 10,
            solutions=honest.solutions,
            solutions_truncated=honest.solutions_truncated,
            metrics=honest.metrics,
            detail="searched further than it declared it would",
        )


class GenerousGame(MissingLettersGame):
    """Marks a wrong word correct.

    The worst failure a grader can have, because everything downstream looks
    healthy: the session completes, the score is computed, the share is built,
    and the only thing wrong is the answer.
    """

    def grade_move(
        self, puzzle: Puzzle, payload: Mapping[str, Any], state: Mapping[str, Any]
    ) -> MoveJudgement:
        honest = super().grade_move(puzzle, payload, state)
        if honest.correct:
            return honest
        return MoveJudgement(
            correct=True,
            complete=True,
            solved=True,
            state={**honest.state, "complete": True, "solved": True},
            note="close enough",
        )


class LoudShareGame(MissingLettersGame):
    """Emits a share token the descriptor never declared.

    The share vocabulary is closed so the engine can hold a codepoint
    allowlist for share text: an undeclared token is a protocol error rather
    than a content rejection, and the failure would otherwise surface as a
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
#: assert the set is complete rather than trusting that the classes above are
#: the ones that were meant.
BROKEN_GAMES = {
    "incurious": (
        IncuriousGame,
        "counts its own answer and never looks for a second reading",
    ),
    "overreaching": (
        OverreachingGame,
        "reports a search beyond the bound it declared",
    ),
    "generous": (GenerousGame, "marks a wrong word correct"),
    "loud_share": (
        LoudShareGame,
        "emits a share token the descriptor never declared",
    ),
}


__all__ = [
    "BROKEN_GAMES",
    "GenerousGame",
    "IncuriousGame",
    "LoudShareGame",
    "OverreachingGame",
]
