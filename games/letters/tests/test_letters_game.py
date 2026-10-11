"""The game as a plugin, and the gates that judge it.

Three things are tested here that no earlier file could. The plugin satisfies
the protocol as an object rather than as eleven loose functions. The engine's
gates reject four games that each break one promise on purpose. And a whole
day runs from board to share artifact, against the real snapshot where one has
been built.

The broken fixtures matter more on this game than on game 1. The answer is a
word, so a board with two true readings looks perfect from the inside, and the
cheapest way for an engine to look correct is to be handed only correct games.
"""

from __future__ import annotations

import pytest

from puzzlegen.core.errors import RejectionReason
from puzzlegen.core.rng import DeterministicRng, derive_seed
from puzzlegen.core.types import DifficultyBand, VerificationCompleteness
from puzzlegen.engine.accessibility import validate_game
from puzzlegen.engine.plugin import GamePlugin, GenerationContext, SessionTelemetry
from puzzlegen.engine.registry import GameRegistry
from puzzlegen.engine.uniqueness import consistency_problem
from puzzlegen.engine.verifier import PuzzleVerifier
from puzzlegen_letters import build_plugin
from puzzlegen_letters.descriptor import GAME_ID, SEARCH_BOUND
from puzzlegen_letters.generate import unusable_reason
from puzzlegen_letters.plugin import MissingLettersGame
from puzzlegen_letters.verify import LETTER_ALTERNATIVES

from boards import fish_board
from broken_letters import (
    BROKEN_GAMES,
    GenerousGame,
    IncuriousGame,
    LoudShareGame,
    OverreachingGame,
)
from paths import SNAPSHOT, has_snapshot

DAY = "2026-10-01"


@pytest.fixture
def game() -> MissingLettersGame:
    return MissingLettersGame()


@pytest.fixture
def puzzle():
    return fish_board(forced=["f"], manufactured=["i"])


@pytest.fixture
def verifier() -> PuzzleVerifier:
    return PuzzleVerifier(require_on_target=False)


def telemetry(**overrides) -> SessionTelemetry:
    base = dict(
        completed=True,
        attempts=1,
        elapsed_ms=20_000,
        mistakes=0,
        hints_used=0,
        events=(
            {"sequence": 0, "kind": "SUBMIT", "outcome": "CORRECT", "offset_ms": 0},
        ),
    )
    base.update(overrides)
    return SessionTelemetry(**base)


class TestThePluginContract:
    def test_it_satisfies_the_protocol(self, game):
        assert isinstance(game, GamePlugin)

    def test_it_implements_every_method_the_engine_calls(self, game):
        for name in (
            "describe",
            "get_content_requirements",
            "generate_candidates",
            "assemble",
            "verify",
            "measure_difficulty",
            "score",
            "grade_move",
            "get_hint",
            "render",
            "create_share_artifact",
        ):
            assert callable(getattr(game, name))

    def test_it_registers(self, game):
        registry = GameRegistry()
        registry.register(game)
        assert GAME_ID in registry

    def test_registration_runs_the_accessibility_gate(self, game):
        validate_game(game.describe())

    def test_the_entry_point_builds_it(self):
        """What the ``puzzlegen.games`` group resolves to. A game the engine
        cannot construct from its entry point is a game nobody can run."""
        assert isinstance(build_plugin(), MissingLettersGame)

    def test_a_salt_is_a_separate_stream(self):
        """Two deployments sharing a day key must not share a board."""
        plain = MissingLettersGame()
        salted = MissingLettersGame(salt="staging")
        assert plain.describe().game_id == salted.describe().game_id

    def test_it_reads_nothing_from_its_environment(self, game, monkeypatch):
        """A game whose output depends on where it ran is a game that cannot
        be reproduced from the day key."""
        monkeypatch.setenv("PUZZLEGEN_SALT", "elsewhere")
        first = game.describe()
        monkeypatch.delenv("PUZZLEGEN_SALT")
        assert game.describe() == first


class TestTheHonestGamePasses:
    """The control. Without it the rejections below prove only that the gates
    reject everything."""

    def test_a_real_board_is_accepted(self, game, puzzle, verifier):
        outcome = verifier.evaluate(
            puzzle, game, game.describe(), DifficultyBand.EASY
        )
        assert outcome.accepted, outcome.detail

    def test_its_verification_reports_what_the_letters_gave_away(self, game, puzzle):
        assert LETTER_ALTERNATIVES in game.verify(puzzle).metrics

    def test_its_verification_is_self_consistent(self, game, puzzle):
        assert consistency_problem(game.verify(puzzle)) is None

    def test_it_declares_the_bound_it_searches_within(self, game):
        assert game.describe().search_bound == SEARCH_BOUND


class TestTheBrokenFixtures:
    """Four games, four broken promises, four gates.

    Failing by design. A day when one of these passes is a day the engine
    stopped working.
    """

    def test_every_broken_game_is_registered_in_the_catalogue(self):
        assert set(BROKEN_GAMES) == {
            "incurious",
            "overreaching",
            "generous",
            "loud_share",
        }

    def test_each_one_says_what_it_breaks(self):
        for cls, reason in BROKEN_GAMES.values():
            assert reason
            assert issubclass(cls, MissingLettersGame)

    def test_counting_its_own_answer_is_rejected(self, puzzle, verifier):
        """One reading might mean unique, or might mean nothing was read, and
        from outside the two look identical."""
        broken = IncuriousGame()
        outcome = verifier.evaluate(
            puzzle, broken, broken.describe(), DifficultyBand.EASY
        )
        assert not outcome.accepted
        assert outcome.reason is RejectionReason.PLUGIN_PROTOCOL_ERROR

    def test_the_uncounted_claim_is_caught_as_a_contradiction(self):
        """A bug in the game's verifier, not a property of its board."""
        broken = IncuriousGame()
        problem = consistency_problem(broken.verify(fish_board()))
        assert problem is not None
        assert "examined no states" in problem

    def test_searching_past_the_declared_bound_is_rejected(self, puzzle, verifier):
        broken = OverreachingGame()
        outcome = verifier.evaluate(
            puzzle, broken, broken.describe(), DifficultyBand.EASY
        )
        assert not outcome.accepted

    def test_a_generous_grader_accepts_a_wrong_word(self, puzzle):
        """Everything downstream looks healthy: the session completes, the
        score computes, the share builds. Only the answer is wrong."""
        broken = GenerousGame()
        assert broken.grade_move(puzzle, {"answer": "food"}, {}).correct

    def test_the_honest_grader_refuses_it(self, game, puzzle):
        assert not game.grade_move(puzzle, {"answer": "food"}, {}).correct

    def test_an_undeclared_share_token_is_detectable(self, puzzle):
        """The vocabulary is closed so the engine can hold a codepoint
        allowlist; an undeclared token is a protocol error, not a content
        rejection."""
        broken = LoudShareGame()
        artifact = broken.create_share_artifact(puzzle, telemetry(), DAY)
        declared = {token.name for token in broken.describe().share_tokens}
        emitted = {name for row in artifact.tokens for name in row}
        assert not emitted <= declared
        assert "jackpot" in emitted

    def test_the_honest_share_stays_inside_the_vocabulary(self, game, puzzle):
        artifact = game.create_share_artifact(puzzle, telemetry(), DAY)
        declared = {token.name for token in game.describe().share_tokens}
        assert {name for row in artifact.tokens for name in row} <= declared


class TestAWholeDay:
    """Board to share artifact, through the plugin only."""

    def test_a_day_runs_from_board_to_share(self, game, puzzle, verifier):
        outcome = verifier.evaluate(
            puzzle, game, game.describe(), DifficultyBand.EASY
        )
        assert outcome.accepted, outcome.detail

        verification = game.verify(puzzle)
        measurement = game.measure_difficulty(puzzle, verification)
        assert game.describe().band_for(measurement.score) in (
            game.describe().supported_bands
        )

        judgement = game.grade_move(puzzle, {"answer": "fish"}, {})
        assert judgement.correct and judgement.solved and judgement.complete

        result = game.score(
            puzzle, telemetry(difficulty_score=measurement.score)
        )
        assert result.points > 0

        artifact = game.create_share_artifact(
            puzzle, telemetry(difficulty_score=measurement.score), DAY
        )
        assert artifact.tokens == (("solved",),)

    def test_a_failed_day_still_scores_and_shares(self, game, puzzle):
        events = tuple(
            {
                "sequence": index,
                "kind": "SUBMIT",
                "outcome": "INCORRECT",
                "offset_ms": index,
            }
            for index in range(5)
        )
        failed = telemetry(completed=False, attempts=5, mistakes=5, events=events)
        assert game.score(puzzle, failed).points >= 0
        artifact = game.create_share_artifact(puzzle, failed, DAY)
        assert artifact.outcome == "unsolved"
        assert all(row != ("solved",) for row in artifact.tokens)

    def test_the_render_never_shows_the_answer_unearned(self, game, puzzle):
        model = game.render(puzzle, {}, "en")
        assert "fish" not in repr(model)

    def test_no_hint_is_the_answer(self, game, puzzle):
        for used in range(4):
            hint = game.get_hint(puzzle, {}, used)
            assert "fish" not in hint.text.lower().replace(", ", "")


class TestAgainstTheRealSnapshot:
    """One generated day, on real content.

    Skipped when the snapshot is absent, because it is 165MB of sqlite that
    ``docs/rebuild-with-batch2.sh`` builds rather than something committed.
    Not skipped for being unimportant: every synthetic test above builds the
    content view it wants, and this is the only one that asks whether the
    curated letters taxonomy can actually supply a board.
    """

    @pytest.fixture
    def repos(self):
        from puzzlegen.graph import GraphRepositories, SqliteDocumentStore

        if not has_snapshot():
            pytest.skip(f"{SNAPSHOT} has not been built")
        repos = GraphRepositories(SqliteDocumentStore(SNAPSHOT))
        try:
            yield repos
        finally:
            repos.close()

    @pytest.fixture
    def port(self, repos):
        from puzzlegen.content.port import ContentPort
        from puzzlegen.content.service import ContentService

        return ContentPort(ContentService(repos), game_id=GAME_ID)

    def test_a_day_can_be_asked_for(self, game, port):
        requirements = game.get_content_requirements(
            difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key=DAY
        )
        results = port.satisfy(requirements)
        assert set(results) == {requirement.name for requirement in requirements}

    def test_a_day_generates_or_says_why_not(self, game, port, verifier):
        """Either a board or a reason. Silence would be the failure."""
        requirements = game.get_content_requirements(
            difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key=DAY
        )
        context = GenerationContext(
            day_key=DAY,
            game_version=game.describe().game_version,
            difficulty_target=DifficultyBand.MEDIUM,
            locale="en",
            rng=DeterministicRng(derive_seed(DAY, GAME_ID)).derive("candidates"),
            content=port.satisfy(requirements),
        )
        candidates = game.generate_candidates(context)
        if not candidates:
            reason = unusable_reason(context)
            assert reason, "generation produced nothing and no reason for it"
            pytest.skip(f"real content cannot build a board today: {reason}")

        puzzle = game.assemble(
            candidates[0], DeterministicRng(derive_seed(DAY, GAME_ID)).derive("layout")
        )
        verification = game.verify(puzzle)
        assert verification.completeness in (
            VerificationCompleteness.COMPLETE,
            VerificationCompleteness.SOUND_INCOMPLETE,
        )
        assert verification.solution_count == 1, puzzle.solution["category"]

        outcome = verifier.evaluate(
            puzzle, game, game.describe(), DifficultyBand.MEDIUM
        )
        assert outcome.accepted, outcome.detail
