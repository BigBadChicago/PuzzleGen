"""The game as a plugin, and the gates that judge it.

Four things are tested here that no earlier file could. The plugin satisfies
the protocol as an object. The engine's gates reject four games that each
break one promise on purpose. A whole day runs from board to share artifact
against the real snapshot where one has been built. And the thing this game is
in the repository to demonstrate is shown on real content: that the same ten
entities serve a digits game and a numbers game, because a shared graph over
content that is not words is the claim the whole platform rests on.
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
from puzzlegen_digits import build_plugin
from puzzlegen_digits.content import CANDIDATES, MINIMUM_DIGITS, TAXONOMY
from puzzlegen_digits.descriptor import CODE_LENGTHS, DESCRIPTOR, GAME_ID
from puzzlegen_digits.generate import unusable_reason
from puzzlegen_digits.lock import space_for, survivors
from puzzlegen_digits.plugin import DigitLockGame
from puzzlegen_digits.verify import digit_facts, surviving_codes

from boards import CODE, GUESSES, context, digits, make_puzzle
from broken_digits import BROKEN_GAMES, GenerousGame
from paths import SNAPSHOT, has_snapshot

DAY = "2026-10-01"
NOW = dt.datetime(2026, 10, 10, tzinfo=dt.timezone.utc)
SALT = "staging"


def puzzle_from(game: DigitLockGame, band: DifficultyBand = DifficultyBand.MEDIUM):
    """One assembled board, through the plugin's own methods."""
    built = context(band=band)
    candidate = game.generate_candidates(built)[0]
    return game.assemble(candidate, DeterministicRng(derive_seed(DAY, GAME_ID)))


@pytest.fixture
def game() -> DigitLockGame:
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
            c.candidate_id for c in salted.generate_candidates(context(salt=SALT))
        ] != [c.candidate_id for c in plain.generate_candidates(context())]

    def test_it_declares_what_it_needs(self, game):
        requirements = game.get_content_requirements(
            difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key=DAY
        )
        assert [r.name for r in requirements] == [CANDIDATES]
        assert requirements[0].minimum == MINIMUM_DIGITS
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
            puzzle, {"code": puzzle.solution["code"]}, {}
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

    def test_a_partial_enumeration_is_refused_for_its_completeness(self):
        """This game promises COMPLETE, so a partial enumeration is a protocol
        violation rather than a weaker claim. Game four declares the weaker
        promise and is refused by uniqueness instead, which is the distinction
        between the two."""
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

    def test_the_incurious_game_misses_a_board_with_two_consistent_codes(self):
        """The board ``UNIQUE_DEDUCTION`` promises not to ship: the solver
        reasons correctly to the end and is left with two codes and a coin."""
        puzzle = make_puzzle(guesses=GUESSES[:1])
        assert build_plugin().verify(puzzle).solution_count > 1
        broken = BROKEN_GAMES["incurious"][0]()
        assert broken.verify(puzzle).solution_count == 1
        outcome = PuzzleVerifier().evaluate(
            puzzle, build_plugin(), DESCRIPTOR, DifficultyBand.MEDIUM
        )
        assert outcome.reason in (
            RejectionReason.MULTIPLE_SOLUTIONS,
            RejectionReason.UNIQUENESS_CONTRACT_VIOLATED,
        )

    def test_the_engine_does_not_promise_a_games_grading_is_right(self):
        generous = GenerousGame()
        puzzle = puzzle_from(generous)
        code = tuple(puzzle.solution["code"])
        wrong = tuple(reversed(code))
        assert wrong != code
        assert generous.grade_move(puzzle, {"code": list(wrong)}, {}).solved
        assert not build_plugin().grade_move(puzzle, {"code": list(wrong)}, {}).solved

    def test_the_honest_verification_is_internally_consistent(self, game):
        assert consistency_problem(game.verify(puzzle_from(game))) is None


class TestTheSymbolicForms:
    def test_a_digits_glyph_is_its_digit_and_its_label_is_the_word(self):
        index = SymbolIndex(digits())
        form = index.form("entity:7")
        assert form.glyph == "7"
        assert form.spoken == "seven"
        assert form.kind is SymbolKind.DIGIT

    def test_the_glyph_comes_from_the_name_without_any_curation(self):
        """The case the subsystem's name-as-glyph fallback exists for: a
        digit's name is one character, so it is its own glyph."""
        index = SymbolIndex(digits())
        assert all(form.glyph == form.entity_id.split(":")[1] for form in index.forms())

    def test_no_two_digits_share_a_glyph(self):
        assert SymbolIndex(digits()).glyph_collisions() == {}


@pytest.mark.skipif(not has_snapshot(), reason="no snapshot built")
class TestAWholeDayAgainstRealContent:
    """The pipeline, the real graph, and no hand-built anything."""

    @pytest.fixture
    def repos(self):
        from puzzlegen.graph import GraphRepositories, SqliteDocumentStore

        found = GraphRepositories(SqliteDocumentStore(SNAPSHOT))
        try:
            yield found
        finally:
            found.close()

    @pytest.fixture
    def pipeline(self, repos):
        from puzzlegen.graph import InMemoryDocumentStore

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
        return GenerationPipeline(
            content=content,
            engine=EngineRepositories(InMemoryDocumentStore()),
            registry=registry,
            dependencies=repos.dependencies,
            snapshot=meta,
            now=NOW,
            verifier=PuzzleVerifier(require_on_target=True),
        )

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

    def test_the_whole_space_was_examined(self, pipeline):
        from math import perm

        outcome = pipeline.generate(GAME_ID, DAY)
        length = outcome.puzzle.payload["code_length"]
        assert outcome.evaluation.verification.states_examined == perm(10, length)

    def test_the_board_is_a_real_board(self, pipeline):
        outcome = pipeline.generate(GAME_ID, DAY)
        payload = outcome.puzzle.payload
        assert payload["code_length"] in CODE_LENGTHS
        assert sorted(payload["digits"]) == list(range(10))
        assert payload["clues"]
        assert len(outcome.puzzle.solution["code"]) == payload["code_length"]

    def test_the_clues_entail_the_code(self, pipeline):
        """Rerun from the whole space against the finished board, which is
        the contract restated as an assertion."""
        outcome = pipeline.generate(GAME_ID, DAY)
        assert surviving_codes(outcome.puzzle) == (
            tuple(outcome.puzzle.solution["code"]),
        )

    def test_the_board_declares_every_digit_it_could_name(self, pipeline):
        outcome = pipeline.generate(GAME_ID, DAY)
        assert len(outcome.puzzle.fact_refs) >= 10

    def test_the_digit_facts_on_the_board_are_the_graphs(self, pipeline):
        """The whole reason the facts travel with the board: the clue, the
        elimination and the enumeration are one set of facts."""
        outcome = pipeline.generate(GAME_ID, DAY)
        facts = digit_facts(outcome.puzzle)
        assert {digit for digit, fact in facts.items() if fact.prime} == {2, 3, 5, 7}

    def test_a_month_of_days_all_generate(self, pipeline):
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
        assert first.puzzle.solution["code"] != second.puzzle.solution["code"]

    def test_the_digits_and_the_numbers_are_the_same_entities(self, repos):
        """What this game is in the repository to demonstrate.

        The snapshot holds a digits taxonomy over nought to nine and a numbers
        taxonomy over nought to ninety-nine, and where they overlap they name
        the same records. A shared graph over content that is not words is the
        claim the whole platform rests on, and this is it holding.
        """
        from puzzlegen.content.query import ContentQuery, Operation

        sealed = max(repos.snapshots.sealed(), key=lambda m: m.created_at)
        content = ContentService(
            repos,
            policy=PolicyService(
                ContentPolicy(name="platform", minimum_confidence=0.7)
            ),
            snapshot=sealed,
            now=NOW,
        )

        def ids(taxonomy: str, category: str) -> set[str]:
            found = content.execute(
                ContentQuery(
                    operation=Operation.FIND_CATEGORY_MEMBERS,
                    category=category,
                    taxonomy=taxonomy,
                    limit=200,
                )
            )
            return {view.entity_id for view in found.entities}

        as_digits = ids("digits", "digit")
        as_numbers = ids("numbers", "number")
        assert len(as_digits) == 10
        assert as_digits <= as_numbers
