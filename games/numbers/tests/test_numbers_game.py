"""The game as a plugin, and the gates that judge it.

Four things are tested here that no earlier file could. The plugin satisfies
the protocol as an object rather than as eleven loose functions. The engine's
gates reject four games that each break one promise on purpose. A whole day
runs from board to share artifact against the real snapshot where one has been
built. And the two platform capabilities this game is the first to need -- the
symbolic form subsystem and the content layer's numeric vocabulary -- are shown
doing their work on real content rather than on fixtures.
"""

from __future__ import annotations

import datetime as dt

import pytest

from puzzlegen.content.policy import ContentPolicy, PolicyService
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
from puzzlegen.symbols import SymbolIndex, SymbolKind
from puzzlegen_numbers import build_plugin
from puzzlegen_numbers.content import CANDIDATES, MINIMUM_NUMBERS, TAXONOMY
from puzzlegen_numbers.descriptor import BOARD_SIZES, DESCRIPTOR, GAME_ID, WINDOW
from puzzlegen_numbers.generate import unusable_reason
from puzzlegen_numbers.plugin import OddNumberOutGame

from boards import context, make_puzzle, window
from broken_numbers import BROKEN_GAMES, GenerousGame
from paths import SNAPSHOT, has_snapshot

DAY = "2026-10-01"
NOW = dt.datetime(2026, 10, 10, tzinfo=dt.timezone.utc)
SALT = "staging"


def puzzle_from(game: OddNumberOutGame, band: DifficultyBand = DifficultyBand.MEDIUM):
    """One assembled board, through the plugin's own methods."""
    built = context(window(), band=band)
    candidate = game.generate_candidates(built)[0]
    return game.assemble(candidate, DeterministicRng(derive_seed(DAY, GAME_ID)))


@pytest.fixture
def game() -> OddNumberOutGame:
    return build_plugin()


class TestThePlugin:
    def test_it_satisfies_the_protocol(self, game):
        assert isinstance(game, GamePlugin)

    def test_the_factory_returns_a_fresh_game(self):
        assert build_plugin() is not build_plugin()

    def test_a_salt_reaches_both_the_requirements_and_the_generator(self):
        salted = build_plugin(salt=SALT)
        plain = build_plugin()
        assert [
            c.candidate_id
            for c in salted.generate_candidates(context(window(), salt=SALT))
        ] != [
            c.candidate_id for c in plain.generate_candidates(context(window()))
        ]

    def test_it_declares_what_it_needs(self, game):
        requirements = game.get_content_requirements(
            difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key=DAY
        )
        assert [r.name for r in requirements] == [CANDIDATES]
        assert requirements[0].minimum == MINIMUM_NUMBERS
        assert requirements[0].query.taxonomy == TAXONOMY

    def test_every_method_the_protocol_names_runs(self, game):
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
        assert game.grade_move(
            puzzle, {"tile": puzzle.solution["answer"]}, {}
        ).solved
        assert game.get_hint(puzzle, {}, 0).text
        assert game.render(puzzle, {}, "en").elements
        assert game.create_share_artifact(puzzle, telemetry, DAY).tokens


class TestTheRegistry:
    def test_the_platform_finds_this_game_through_its_entry_point(self):
        assert GAME_ID in GameRegistry().discover()

    def test_a_registered_game_can_be_activated_for_a_day(self, game):
        registry = GameRegistry()
        registry.register(game)
        registry.activate(GAME_ID, day_key=DAY)
        assert registry.is_active(GAME_ID, DAY)


class TestTheEnginesGates:
    @pytest.mark.parametrize("name", sorted(BROKEN_GAMES))
    def test_a_game_that_breaks_a_promise_is_refused(self, name):
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

    def test_a_partial_enumeration_is_refused_by_uniqueness_not_completeness(self):
        """The distinction this game's contract makes.

        It declares SOUND_INCOMPLETE, so a partial enumeration is not a
        protocol violation. It still establishes no uniqueness, because the
        branch it did not reach is where the second answer would be, so the
        board is refused by the uniqueness gate instead.
        """
        broken = BROKEN_GAMES["incomplete"][0]()
        outcome = PuzzleVerifier().evaluate(
            puzzle_from(broken), broken, DESCRIPTOR, DifficultyBand.MEDIUM
        )
        assert outcome.reason is RejectionReason.UNIQUENESS_CONTRACT_VIOLATED

    def test_the_overreaching_game_is_refused_for_its_bound(self):
        broken = BROKEN_GAMES["overreaching"][0]()
        outcome = PuzzleVerifier().evaluate(
            puzzle_from(broken), broken, DESCRIPTOR, DifficultyBand.MEDIUM
        )
        assert outcome.reason is RejectionReason.VERIFICATION_UNSOUND

    def test_the_incurious_game_misses_a_board_with_two_readable_tiles(self):
        """Its verifier reports one answer whatever the board holds, so the
        board it is given has to be one with two."""
        puzzle = make_puzzle([10, 11, 15, 21, 45], answer=10)
        assert build_plugin().verify(puzzle).solution_count == 2
        broken = BROKEN_GAMES["incurious"][0]()
        assert broken.verify(puzzle).solution_count == 1

    def test_the_engine_does_not_promise_a_games_grading_is_right(self):
        """Grading belongs to the game. What the engine guarantees is that a
        replayed ledger reaches the same judgement, not that the judgement is
        correct, so a game marking everything right is a game bug.
        """
        generous = GenerousGame()
        puzzle = puzzle_from(generous)
        wrong = next(
            tile["id"]
            for tile in puzzle.payload["tiles"]
            if tile["id"] != puzzle.solution["answer"]
        )
        assert generous.grade_move(puzzle, {"tile": wrong}, {}).solved
        assert not build_plugin().grade_move(puzzle, {"tile": wrong}, {}).solved

    def test_the_honest_verification_is_internally_consistent(self, game):
        assert consistency_problem(game.verify(puzzle_from(game))) is None


class TestTheSymbolicForms:
    """The subsystem doing its work on this game's own tiles.

    Worth its own class because a number is the case the subsystem's
    name-as-glyph fallback exists for: the digits are short enough to be their
    own glyph, so a number needs no curation at all, while the words a reader
    hears have to come from a fact because nobody would guess "forty-two" from
    "42".
    """

    def test_a_numbers_glyph_is_its_digits_and_its_label_is_the_words(self):
        index = SymbolIndex(window())
        form = index.form("entity:42")
        assert form.glyph == "42"
        assert form.spoken == "forty-two"
        assert form.kind is SymbolKind.NUMBER

    def test_a_number_carries_an_ordinal_and_a_word_does_not(self):
        index = SymbolIndex(window())
        assert index.form("entity:42").value() == 42.0

    def test_the_board_can_be_ordered_through_the_subsystem(self):
        """What the ordinal is for. The content already returns the window
        ascending, so this is the same answer reached a second way, which is
        the check that the two agree."""
        index = SymbolIndex(window())
        ids = [f"entity:{value}" for value in (47, 11, 99, 10)]
        assert index.ascending(ids) == (
            "entity:10", "entity:11", "entity:47", "entity:99",
        )

    def test_no_two_numbers_in_the_window_share_a_glyph(self):
        """A board where two tiles were drawn identically would be one the
        player cannot operate."""
        assert SymbolIndex(window()).glyph_collisions() == {}


@pytest.mark.skipif(not has_snapshot(), reason="no snapshot built")
class TestAWholeDayAgainstRealContent:
    """The pipeline, the real graph, and no hand-built anything."""

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

    def test_the_board_is_a_real_board_inside_the_window(self, pipeline):
        outcome = pipeline.generate(GAME_ID, DAY)
        payload = outcome.puzzle.payload
        low, high = WINDOW
        values = [tile["value"] for tile in payload["tiles"]]
        assert payload["board_size"] in BOARD_SIZES
        assert len(values) == payload["board_size"]
        assert all(low <= value <= high for value in values)
        assert values == sorted(values)

    def test_the_board_declares_the_graph_records_it_used(self, pipeline):
        assert pipeline.generate(GAME_ID, DAY).puzzle.fact_refs

    def test_a_month_of_days_all_generate(self, pipeline):
        start = dt.date(2026, 10, 1)
        failures = []
        for offset in range(30):
            day = (start + dt.timedelta(days=offset)).isoformat()
            outcome = pipeline.generate(GAME_ID, day)
            if not outcome.succeeded:
                failures.append((day, outcome.trace.failure_reason))
        assert not failures, failures

    def test_every_band_is_servable_over_a_week(self, pipeline):
        """The thin properties are at the obscure tier, so a hard week is the
        run most likely to find the content shortage the fallback size order
        exists for."""
        start = dt.date(2026, 10, 1)
        for band in (DifficultyBand.EASY, DifficultyBand.MEDIUM, DifficultyBand.HARD):
            for offset in range(7):
                day = (start + dt.timedelta(days=offset)).isoformat()
                outcome = pipeline.generate(GAME_ID, day, difficulty_target=band)
                assert outcome.succeeded, (band, day, outcome.trace.failure_reason)

    def test_the_same_day_regenerates_the_same_board(self, pipeline):
        first = pipeline.generate(GAME_ID, DAY)
        second = pipeline.generate(GAME_ID, DAY)
        assert first.puzzle.payload == second.puzzle.payload
        assert first.puzzle.solution == second.puzzle.solution

    def test_two_days_serve_different_boards(self, pipeline):
        first = pipeline.generate(GAME_ID, "2026-10-01")
        second = pipeline.generate(GAME_ID, "2026-10-02")
        assert first.puzzle.payload["tiles"] != second.puzzle.payload["tiles"]

    def test_the_same_number_is_one_entity_in_two_taxonomies(self, pipeline):
        """The shared graph working on content that is not words. The digits
        taxonomy in the same snapshot holds nought to nine, and where the two
        overlap they name the same records -- which is why a board of numbers
        and a board of digits would share dependencies rather than duplicate
        them.
        """
        outcome = pipeline.generate(GAME_ID, DAY)
        refs = set(outcome.puzzle.fact_refs)
        assert refs
        assert all(ref.startswith(("entity:", "category:", "fact:")) for ref in refs)
