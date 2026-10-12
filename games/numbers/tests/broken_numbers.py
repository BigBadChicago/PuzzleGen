"""Four broken odd_number_out games, each breaking one promise on purpose.

They exist to prove the engine catches what it claims to. A gate that has
never rejected anything is a gate nobody has tested, and the cheapest way for
an engine to look correct is to be handed only correct games.

Each subclass changes exactly one thing, so a test that fails tells you which
promise went unenforced rather than that something somewhere broke. They live
under ``tests`` and not in the shipped package, because a broken game inside
the distribution would eventually be copied by somebody building a real one.

They are failing by design. Fixing them is not the goal; a day when one of
them passes the engine's gates is a day the engine stopped working.

The failure this game is most exposed to is the one its contract is unusual
about. Its answer is a tile and a board can have one answer for several
reasons, so a verifier that counted property-and-tile pairs rather than tiles
would report a perfectly good board as having three solutions and have it
refused. The mirror of that is worse and is what ``IncuriousGame`` does: a
verifier that reports its own intent never notices the board where a *second*
tile is also readable, which is the board that marks a correct player wrong.
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
from puzzlegen_numbers.plugin import OddNumberOutGame


class IncuriousGame(OddNumberOutGame):
    """Counts its own answer and never looks for a second readable tile.

    The failure the uniqueness contract exists for. A board with two readable
    tiles looks perfect from the inside: the generator's own property is
    there, it is true of all but one tile, and a verifier reporting its intent
    rather than its search would publish a board where a player who found the
    other reading is marked wrong.
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


class IncompleteGame(OddNumberOutGame):
    """Returns a partial enumeration, which this game's contract permits.

    Included to show the one place the completeness machinery behaves
    differently here than on the figure game. This game declares
    SOUND_INCOMPLETE, so returning it is not a protocol violation -- but an
    incomplete enumeration satisfies no uniqueness contract at all, because
    the branch it did not reach is where the second answer would be. So the
    board is still refused, by the uniqueness gate rather than the
    completeness one, and that is the distinction this fixture pins.
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


class OverreachingGame(OddNumberOutGame):
    """Reports a search beyond the bound it declared.

    The bound is what makes SOUND_INCOMPLETE a promise rather than a shrug: a
    game that could claim any amount of searching and still publish would be
    declaring nothing. Refused for its completeness here, since the bound is
    only checked on an incomplete result.
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


class GenerousGame(OddNumberOutGame):
    """Marks the wrong number correct.

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


class LoudShareGame(OddNumberOutGame):
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
        "counts its own answer and never scans the vocabulary",
    ),
    "incomplete": (
        IncompleteGame,
        "returns a partial enumeration, which establishes no uniqueness",
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
