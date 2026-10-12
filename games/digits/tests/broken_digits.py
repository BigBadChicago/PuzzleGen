"""Four broken digit_lock games, each breaking one promise on purpose.

They exist to prove the engine catches what it claims to. A gate that has
never rejected anything is a gate nobody has tested, and the cheapest way for
an engine to look correct is to be handed only correct games.

Each subclass changes exactly one thing, so a test that fails tells you which
promise went unenforced rather than that something somewhere broke. They live
under ``tests`` and not in the shipped package, because a broken game inside
the distribution would eventually be copied by somebody building a real one.

They are failing by design. Fixing them is not the goal; a day when one of
them passes the engine's gates is a day the engine stopped working.

The failure this game is most exposed to is the one its contract is strongest
about. ``UNIQUE_DEDUCTION`` promises the clues *entail* the code, so the board
that breaks it is not one with a wrong answer but one with a second consistent
code: the solver reasons correctly to the end and is left with two codes and a
coin. That board looks perfect from the inside -- the intended code is there,
every clue is true of it -- and only an enumeration of the whole space finds
the other one, which is exactly what ``IncuriousGame`` refuses to do.
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
from puzzlegen_digits.plugin import DigitLockGame


class IncuriousGame(DigitLockGame):
    """Counts its own answer and never enumerates the space.

    The failure ``UNIQUE_DEDUCTION`` exists for. A board with two consistent
    codes looks perfect from the inside, and a verifier reporting its intent
    rather than its search would publish one, leaving the solver to guess
    between two codes both of which the clues allow.
    """

    def verify(self, puzzle: Puzzle) -> VerificationResult:
        return VerificationResult(
            solvable=True,
            solution_count=1,
            completeness=VerificationCompleteness.COMPLETE,
            states_examined=0,
            solutions=(dict(puzzle.solution),),
            detail="answer known in advance, triples never enumerated",
        )


class IncompleteGame(DigitLockGame):
    """Declares a complete enumeration and returns a partial one.

    This game promises COMPLETE, so a partial enumeration is a protocol
    violation here and not merely a weaker claim: the codes it did not examine
    are exactly where a second consistent one would be. Refused by the
    completeness gate, which is the distinction between this game and game
    four -- that one declares the weaker promise and is refused by uniqueness
    instead.
    """

    def verify(self, puzzle: Puzzle) -> VerificationResult:
        return VerificationResult(
            solvable=True,
            solution_count=1,
            completeness=VerificationCompleteness.SOUND_INCOMPLETE,
            states_examined=3,
            solutions=(dict(puzzle.solution),),
            detail="stopped after three triples",
        )


class OverreachingGame(DigitLockGame):
    """Reports a search beyond the bound it declared.

    The bound is a ceiling that makes a code length this game was not built
    for fail loudly rather than slowly. Checked only on an incomplete result,
    so this fixture returns one.
    """

    def verify(self, puzzle: Puzzle) -> VerificationResult:
        return VerificationResult(
            solvable=True,
            solution_count=1,
            completeness=VerificationCompleteness.SOUND_INCOMPLETE,
            states_examined=10_000_000,
            solutions=(dict(puzzle.solution),),
            detail="examined far more triples than the board holds",
        )


class GenerousGame(DigitLockGame):
    """Marks the wrong code correct.

    Nothing in the engine can catch this on its own, and that is the point of
    including it: grading is the game's, and what the engine guarantees is
    that the ledger replays to the same judgement rather than that the
    judgement is right. The test that uses this one asserts what the engine
    does and does not promise, so the boundary is written down.
    """

    def grade_move(
        self, puzzle: Puzzle, payload: Mapping[str, Any], state: Mapping[str, Any]
    ) -> MoveJudgement:
        return MoveJudgement(
            correct=True,
            complete=True,
            solved=True,
            state={"solved": True, "complete": True},
            note="anything you like",
        )


class LoudShareGame(DigitLockGame):
    """Emits a share token the descriptor never declared.

    The share vocabulary is the complete set a game's artifacts may use, which
    is what lets the engine hold a codepoint allowlist for share text. A token
    outside it is a protocol error rather than a content problem.
    """

    def create_share_artifact(
        self, puzzle: Puzzle, telemetry: SessionTelemetry, day_key: str
    ) -> ShareArtifact:
        return ShareArtifact(
            game_id=puzzle.game_id,
            day_key=day_key,
            outcome="solved",
            tokens=(("undeclared",),),
            headline="found it",
        )


BROKEN_GAMES = {
    "incurious": (
        IncuriousGame,
        "counts its own answer and never enumerates the space",
    ),
    "incomplete": (
        IncompleteGame,
        "declares a complete enumeration and returns a partial one",
    ),
    "overreaching": (
        OverreachingGame,
        "reports a search beyond the bound it declared",
    ),
    "loud_share": (
        LoudShareGame,
        "emits a share token the descriptor never declared",
    ),
}


__all__ = [
    "BROKEN_GAMES",
    "GenerousGame",
    "IncompleteGame",
    "IncuriousGame",
    "LoudShareGame",
    "OverreachingGame",
]
