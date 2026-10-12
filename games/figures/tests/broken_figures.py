"""Five broken shape_trio games, each breaking one promise on purpose.

They exist to prove the engine catches what it claims to. A gate that has
never rejected anything is a gate nobody has tested, and the cheapest way for
an engine to look correct is to be handed only correct games.

Each subclass changes exactly one thing, so a test that fails tells you which
promise went unenforced rather than that something somewhere broke. They live
under ``tests`` and not in the shipped package, because a broken game inside
the distribution would eventually be copied by somebody building a real one.

They are failing by design. Fixing them is not the goal; a day when one of
them passes the engine's gates is a day the engine stopped working.

This game is exposed to one failure the lexical games are not, and it has its
own fixture here: it declares COMPLETE verification, which is a promise no
other game in the repository makes. A verifier that quietly returned a partial
enumeration would be claiming to have examined every triple while examining
some of them, and the engine's completeness check is the only thing standing
between that and a published board whose uniqueness nobody established.
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
from puzzlegen_figures.plugin import ShapeTrioGame


class IncuriousGame(ShapeTrioGame):
    """Counts its own answer and never looks for a second trio.

    The failure the uniqueness contract exists for. A board with two trios
    looks perfect from the inside: the generator's own trio is there, it
    satisfies the rule, and a verifier that reported its intent rather than
    its search would publish a board where a player who found the other trio
    is marked wrong.
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


class IncompleteGame(ShapeTrioGame):
    """Declares a complete enumeration and returns a partial one.

    The failure specific to this game, because it is the only one that
    promises COMPLETE. Silently degrading from a complete enumeration to a
    sound but partial one is exactly what ``VerificationCompleteness`` exists
    to prevent: the triples left unexamined are where a second trio hides.
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


class OverreachingGame(ShapeTrioGame):
    """Reports a search beyond the bound it declared.

    The bound is a ceiling that makes a board larger than this game was built
    for fail loudly rather than slowly. A game that could overrun it and still
    publish would have a bound in name only.
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


class GenerousGame(ShapeTrioGame):
    """Marks a triple that is not a trio correct.

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


class LoudShareGame(ShapeTrioGame):
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
        "counts its own answer and never enumerates the triples",
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
