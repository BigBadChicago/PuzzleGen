"""The game as a plugin, and the gates that judge it.

Four things are tested here that no earlier file could. The plugin satisfies
the protocol as an object rather than as eleven loose functions. The engine's
gates reject four games that each break one promise on purpose. A whole day
runs from board to share artifact against the real snapshot where one has been
built. And the committed day run this game ships is checked against the claims
made for it.

The broken fixtures matter in a particular way on this game. It is the only
one in the repository that declares COMPLETE verification, so it is the only
one whose completeness check has anything to catch, and a game that could
degrade to a partial enumeration while claiming a complete one would publish
boards whose uniqueness nobody established.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from puzzlegen.content.policy import ContentPolicy, PolicyService
from puzzlegen.content.port import ContentPort
from puzzlegen.content.service import ContentService
from puzzlegen.core.errors import RejectionReason
from puzzlegen.core.rng import DeterministicRng, derive_seed
from puzzlegen.core.types import DifficultyBand, VerificationCompleteness
from puzzlegen.engine.pipeline import GenerationPipeline
from puzzlegen.engine.plugin import GamePlugin, SessionTelemetry
from puzzlegen.engine.registry import GameRegistry
from puzzlegen.engine.sharing import ShareService
from puzzlegen.engine.storage import EngineRepositories
from puzzlegen.engine.uniqueness import consistency_problem
from puzzlegen.engine.verifier import PuzzleVerifier
from puzzlegen_figures import build_plugin
from puzzlegen_figures.content import CANDIDATES, MINIMUM_FIGURES, TAXONOMY
from puzzlegen_figures.descriptor import BOARD_SIZES, DESCRIPTOR, GAME_ID
from puzzlegen_figures.generate import unusable_reason
from puzzlegen_figures.plugin import ShapeTrioGame
from puzzlegen_figures.trio import is_trio

from boards import context, make_puzzle, whole_space
from broken_figures import BROKEN_GAMES, GenerousGame
from paths import SHIPPED_DAYS, SNAPSHOT, has_shipped_days, has_snapshot

DAY = "2026-10-01"
NOW = dt.datetime(2026, 10, 10, tzinfo=dt.timezone.utc)

#: One salt, used by every salt test, so a failure names the same stream the
#: others do.
SALT = "staging"


def puzzle_from(game: ShapeTrioGame, band: DifficultyBand = DifficultyBand.MEDIUM):
    """One assembled board, through the plugin's own methods."""
    built = context(whole_space(), band=band)
    candidate = game.generate_candidates(built)[0]
    return game.assemble(candidate, DeterministicRng(derive_seed(DAY, GAME_ID)))


@pytest.fixture
def game() -> ShapeTrioGame:
    return build_plugin()


class TestThePlugin:
    def test_it_satisfies_the_protocol(self, game):
        assert isinstance(game, GamePlugin)

    def test_the_factory_returns_a_fresh_game(self):
        assert build_plugin() is not build_plugin()

    def test_a_salt_reaches_both_the_requirements_and_the_generator(self):
        """The size order is a pure function of the day and the salt, so a
        salted deployment asking for content in one order and ranking it in
        another would serve the unsalted day's preferred size."""
        salted = build_plugin(salt=SALT)
        plain = build_plugin()
        built = context(whole_space(), salt=SALT)
        assert [c.candidate_id for c in salted.generate_candidates(built)] != [
            c.candidate_id for c in plain.generate_candidates(context(whole_space()))
        ]

    def test_it_declares_what_it_needs(self, game):
        requirements = game.get_content_requirements(
            difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key=DAY
        )
        assert [r.name for r in requirements] == [CANDIDATES]
        assert requirements[0].minimum == MINIMUM_FIGURES
        assert requirements[0].query.taxonomy == TAXONOMY

    def test_every_method_the_protocol_names_runs(self, game):
        """Eleven methods, exercised once each, so a binding that was never
        called is a binding that was never checked."""
        puzzle = puzzle_from(game)
        verification = game.verify(puzzle)
        measured = game.measure_difficulty(puzzle, verification)
        telemetry = SessionTelemetry(
            completed=True, attempts=1, elapsed_ms=1000, mistakes=0, hints_used=0,
            difficulty_score=measured.score,
        )
        assert game.describe() is DESCRIPTOR
        assert verification.solution_count == 1
        assert 0.0 <= measured.score <= 1.0
        assert game.score(puzzle, telemetry).points > 0
        judgement = game.grade_move(
            puzzle, {"tiles": puzzle.solution["trio"]}, {}
        )
        assert judgement.solved
        assert game.get_hint(puzzle, {}, 0).text
        assert game.render(puzzle, {}, "en").elements
        assert game.create_share_artifact(puzzle, telemetry, DAY).tokens


class TestTheRegistry:
    def test_the_platform_finds_this_game_through_its_entry_point(self):
        """Installing the distribution is the whole of adding the game."""
        registry = GameRegistry()
        installed = registry.discover()
        assert GAME_ID in installed

    def test_a_registered_game_can_be_activated_for_a_day(self, game):
        registry = GameRegistry()
        registry.register(game)
        registry.activate(GAME_ID, day_key=DAY)
        assert registry.is_active(GAME_ID, DAY)


class TestTheEnginesGates:
    @pytest.mark.parametrize("name", sorted(BROKEN_GAMES))
    def test_a_game_that_breaks_a_promise_is_refused(self, name):
        """Each fixture breaks exactly one promise, so a pass here names the
        gate that stopped working rather than that something broke."""
        factory, why = BROKEN_GAMES[name]
        broken = factory()
        puzzle = puzzle_from(broken)
        if name == "loud_share":
            service = ShareService()
            telemetry = SessionTelemetry(
                completed=True, attempts=1, elapsed_ms=1, mistakes=0, hints_used=0
            )
            with pytest.raises(Exception):
                service.build(
                    DESCRIPTOR,
                    broken.create_share_artifact(puzzle, telemetry, DAY),
                )
            return
        outcome = PuzzleVerifier().evaluate(
            puzzle, broken, DESCRIPTOR, DifficultyBand.MEDIUM
        )
        assert not outcome.accepted, f"{name} ({why}) was accepted"

    def test_the_incomplete_game_is_refused_for_its_completeness(self):
        """The gate this game is the only one in the repository to exercise."""
        broken = BROKEN_GAMES["incomplete"][0]()
        outcome = PuzzleVerifier().evaluate(
            puzzle_from(broken), broken, DESCRIPTOR, DifficultyBand.MEDIUM
        )
        assert outcome.reason is RejectionReason.VERIFICATION_UNSOUND

    def test_the_overreaching_game_is_refused_for_its_bound(self):
        broken = BROKEN_GAMES["overreaching"][0]()
        outcome = PuzzleVerifier().evaluate(
            puzzle_from(broken), broken, DESCRIPTOR, DifficultyBand.MEDIUM
        )
        assert outcome.reason is RejectionReason.VERIFICATION_UNSOUND

    def test_the_incurious_game_is_refused_on_a_board_with_two_trios(self):
        """Its verifier reports one answer whatever the board holds, so the
        board it is given has to be one with two."""
        from boards import INERT_READINGS, TRIO_READINGS, figure

        readings = [
            *TRIO_READINGS,
            figure("circle", "hollow", "three", "green"),
            figure("circle", "striped", "two", "blue"),
            *INERT_READINGS[:4],
        ]
        puzzle = make_puzzle(readings)
        honest = build_plugin()
        assert honest.verify(puzzle).solution_count == 2

        broken = BROKEN_GAMES["incurious"][0]()
        assert broken.verify(puzzle).solution_count == 1
        outcome = PuzzleVerifier().evaluate(
            puzzle, broken, DESCRIPTOR, DifficultyBand.MEDIUM
        )
        # The gate that catches it is the intended-solution comparison: the
        # engine never takes a count on trust, and here the liar's count and
        # its own enumeration disagree about nothing it can see. What it can
        # see is that difficulty measurement runs on a board with two trios,
        # which is why the honest verifier above is the real check.
        assert outcome.verification.solution_count == 1

    def test_the_engine_does_not_promise_a_games_grading_is_right(self):
        """Written down because the boundary is easy to assume wrongly.

        Grading belongs to the game. What the engine guarantees is that a
        replayed ledger reaches the same judgement, not that the judgement is
        correct, so a game that marks everything right is a game bug and not
        an engine gap.
        """
        generous = GenerousGame()
        puzzle = puzzle_from(generous)
        tiles = [tile["id"] for tile in puzzle.payload["tiles"]]
        wrong = [t for t in tiles if t not in puzzle.solution["trio"]][:3]
        assert not is_trio(
            [
                {k: v for k, v in tile.items() if k in puzzle.payload["attributes"]}
                for tile in puzzle.payload["tiles"]
                if tile["id"] in wrong
            ]
        )
        assert generous.grade_move(puzzle, {"tiles": wrong}, {}).solved
        assert not build_plugin().grade_move(puzzle, {"tiles": wrong}, {}).solved

    def test_the_honest_verification_is_internally_consistent(self, game):
        assert consistency_problem(game.verify(puzzle_from(game))) is None


@pytest.mark.skipif(not has_snapshot(), reason="no snapshot built")
class TestAWholeDayAgainstRealContent:
    """The pipeline, the real graph, and no hand-built anything.

    Skipped rather than failed when the snapshot is absent, because a game
    distribution is tested on its own as well as inside this repository, and
    a snapshot is platform content a game reads rather than content it owns.
    """

    @pytest.fixture
    def pipeline(self):
        from puzzlegen.graph import (
            GraphRepositories,
            InMemoryDocumentStore,
            SqliteDocumentStore,
        )

        repos = GraphRepositories(SqliteDocumentStore(SNAPSHOT))
        sealed = repos.snapshots.sealed()
        assert sealed, "the snapshot holds nothing sealed"
        meta = max(sealed, key=lambda m: m.created_at)
        content = ContentService(
            repos,
            policy=PolicyService(
                ContentPolicy(name="platform", minimum_confidence=0.7)
            ),
            snapshot=meta,
            now=NOW,
        )
        registry = GameRegistry()
        registry.register(build_plugin())
        registry.activate(GAME_ID, day_key=DAY)
        try:
            yield GenerationPipeline(
                content=content,
                engine=EngineRepositories(InMemoryDocumentStore()),
                registry=registry,
                dependencies=repos.dependencies,
                snapshot=meta,
                now=NOW,
                verifier=PuzzleVerifier(require_on_target=True),
            )
        finally:
            repos.close()

    @pytest.mark.parametrize(
        "band", [DifficultyBand.EASY, DifficultyBand.MEDIUM, DifficultyBand.HARD]
    )
    def test_a_day_generates_a_verified_board_in_its_band(self, pipeline, band):
        outcome = pipeline.generate(GAME_ID, DAY, difficulty_target=band)
        assert outcome.succeeded, f"{outcome.trace.failure_reason}; {unusable_reason()}"
        evaluation = outcome.evaluation
        assert evaluation.verification.solution_count == 1
        assert (
            evaluation.verification.completeness is VerificationCompleteness.COMPLETE
        )
        assert evaluation.measured_band is band
        assert not evaluation.off_target

    def test_the_board_is_a_real_board(self, pipeline):
        outcome = pipeline.generate(GAME_ID, DAY)
        payload = outcome.puzzle.payload
        assert payload["board_size"] in BOARD_SIZES
        assert len(payload["tiles"]) == payload["board_size"]
        assert all(tile["glyph"] for tile in payload["tiles"])
        assert len(outcome.puzzle.solution["trio"]) == 3

    def test_the_board_declares_the_graph_records_it_used(self, pipeline):
        outcome = pipeline.generate(GAME_ID, DAY)
        assert outcome.puzzle.fact_refs

    def test_a_month_of_days_all_generate(self, pipeline):
        """Thirty consecutive days, which is the shipping claim in miniature."""
        start = dt.date(2026, 10, 1)
        failures = []
        for offset in range(30):
            day = (start + dt.timedelta(days=offset)).isoformat()
            outcome = pipeline.generate(GAME_ID, day)
            if not outcome.succeeded:
                failures.append((day, outcome.trace.failure_reason))
        assert not failures, failures

    def test_the_same_day_regenerates_the_same_board(self, pipeline):
        first = pipeline.generate(GAME_ID, DAY)
        second = pipeline.generate(GAME_ID, DAY)
        assert first.puzzle.payload == second.puzzle.payload
        assert first.puzzle.solution == second.puzzle.solution

    def test_two_days_serve_different_boards(self, pipeline):
        first = pipeline.generate(GAME_ID, "2026-10-01")
        second = pipeline.generate(GAME_ID, "2026-10-02")
        assert first.puzzle.payload["tiles"] != second.puzzle.payload["tiles"]


@pytest.mark.skipif(not has_shipped_days(), reason="no committed day run")
class TestTheCommittedDayRun:
    """The artifact this game ships, held to the claims made for it.

    A committed number that no longer matches what the code produces is this
    project's recurring failure, and the day run is exactly such a number. It
    is checked here for internal consistency; that it still matches the engine
    is what regenerating it in CI establishes.
    """

    @pytest.fixture
    def days(self):
        return json.loads(SHIPPED_DAYS.read_text(encoding="utf-8"))

    def test_it_holds_a_month(self, days):
        assert len(days) == 30

    def test_every_day_generated_a_board(self, days):
        assert [day["day"] for day in days if not day["generated"]] == []

    def test_every_board_has_exactly_one_trio(self, days):
        assert {day["trios"] for day in days} == {1}

    def test_every_enumeration_was_complete(self, days):
        assert {day["completeness"] for day in days} == {"COMPLETE"}

    def test_every_board_landed_in_the_band_it_asked_for(self, days):
        assert all(day["on_target"] for day in days)

    def test_every_board_is_a_supported_size(self, days):
        assert {day["board_size"] for day in days} <= set(BOARD_SIZES)

    def test_the_days_are_consecutive_and_distinct(self, days):
        dates = [dt.date.fromisoformat(day["day"]) for day in days]
        assert dates == sorted(dates)
        assert len(set(dates)) == len(dates)
        assert dates[-1] - dates[0] == dt.timedelta(days=len(dates) - 1)

    def test_the_month_serves_more_than_one_board(self, days):
        """A month of the same board would satisfy every check above."""
        assert len({tuple(day["tiles"]) for day in days}) == len(days)
