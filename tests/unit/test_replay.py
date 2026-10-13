"""Version-pinned replay semantics.

Three claims, and the third is the one the design turns on: a transcript
stamped with rules this release does not grade by is refused, not graded under
today's. The refusal is what keeps a replayed number comparable with the one
the player saw.
"""

from __future__ import annotations

import datetime as dt

import pytest

from puzzlegen.core.versions import (
    ENGINE_VERSION,
    SCORING_VERSION,
    SESSION_SCHEMA_VERSION,
)
from puzzlegen.engine.replay import ReplayService, RulesVersionError
from puzzlegen.engine.scoring import puzzle_from_record
from puzzlegen.engine.sessions import (
    MoveKind,
    MoveOutcome,
    MoveRecord,
    RulesVersion,
    SessionState,
)

from conftest import DAY, PLAY_START, build_published_day


@pytest.fixture
def replay(scoring, registry) -> ReplayService:
    return ReplayService(scoring, registry)


def played(sessions, engine_repos, identity, *, correct: bool = True):
    """One finished transcript on the day's published board."""
    manifest = build_published_day(engine_repos)
    player = identity.create_player().player
    session = sessions.start(player.id, manifest.id)
    session = session.with_move(
        MoveRecord(
            sequence=1,
            kind=MoveKind.SUBMIT,
            outcome=MoveOutcome.CORRECT if correct else MoveOutcome.INCORRECT,
            server_at=PLAY_START + dt.timedelta(seconds=20),
            payload={"choice": "opt_d"},
        )
    )
    session = session.terminated(SessionState.COMPLETED, session.last_activity_at)
    return session, manifest


class TestTheStamp:
    def test_a_started_session_records_the_rules_it_will_be_graded_by(
        self, sessions, engine_repos, identity
    ):
        session, manifest = played(sessions, engine_repos, identity)
        assert session.rules == RulesVersion(
            engine=ENGINE_VERSION,
            # From the manifest, not from the registry: the session names the
            # release of the game its board was built by.
            game=manifest.versions.game,
            scoring=SCORING_VERSION,
            session_schema=SESSION_SCHEMA_VERSION,
        )

    def test_the_stamp_is_the_manifests_game_version_not_the_registrys(
        self, sessions, engine_repos, identity, registry
    ):
        """A board built by 1.0.0 stays a 1.0.0 transcript.

        Written because the obvious implementation reads the registry, which
        holds whatever is loaded now, and would stamp a newer version on a
        replay of an older board.
        """
        session, _ = played(sessions, engine_repos, identity)
        assert session.rules.game == "1.0.0"
        assert registry.get(session.game_id).descriptor.game_version == "1.0.0"


class TestHonouring:
    def test_matching_rules_are_honoured(
        self, replay, sessions, engine_repos, identity
    ):
        session, _ = played(sessions, engine_repos, identity)
        assert replay.can_honour(session) is True

    @pytest.mark.parametrize(
        "field, value",
        [
            ("engine", "9.9.9"),
            ("game", "2.0.0"),
            ("scoring", "9.9.9"),
            ("session_schema", 99),
        ],
    )
    def test_any_differing_component_is_refused(
        self, replay, sessions, engine_repos, identity, field, value
    ):
        session, _ = played(sessions, engine_repos, identity)
        stale = session.model_copy(
            update={"rules": session.rules.model_copy(update={field: value})}
        )
        assert replay.can_honour(stale) is False
        with pytest.raises(RulesVersionError, match=field):
            replay.require_honoured(stale)

    def test_a_withdrawn_game_is_refused_rather_than_crashing(
        self, scoring, registry, sessions, engine_repos, identity
    ):
        """An archive sweep must not stop at the first game that is gone."""
        session, _ = played(sessions, engine_repos, identity)
        empty = ReplayService(scoring, type(registry)())
        assert empty.can_honour(session) is False
        with pytest.raises(RulesVersionError, match="cannot load"):
            empty.require_honoured(session)


class TestGrading:
    def test_a_round_trip_under_matching_rules_reproduces_the_outcome(
        self, replay, scoring, sessions, engine_repos, identity, game
    ):
        session, manifest = played(sessions, engine_repos, identity)
        record = engine_repos.puzzles.require(session.puzzle_id)
        puzzle = puzzle_from_record(record)
        original = scoring.evaluate(
            session,
            game,
            puzzle,
            difficulty_score=manifest.difficulty.score,
            scored_at=session.ended_at,
        )
        replayed = replay.grade(session, puzzle, manifest)
        assert replayed == original

    def test_grading_refuses_before_it_scores(
        self, replay, sessions, engine_repos, identity
    ):
        """The refusal is the entry point's first act, not a post-hoc check."""
        session, manifest = played(sessions, engine_repos, identity)
        record = engine_repos.puzzles.require(session.puzzle_id)
        stale = session.model_copy(
            update={"rules": session.rules.model_copy(update={"scoring": "0.0.1"})}
        )
        with pytest.raises(RulesVersionError):
            replay.grade(stale, puzzle_from_record(record), manifest)


class TestRulesVersion:
    def test_honouring_is_exact_equality(self):
        """There is no compatible scoring change.

        A change that cannot alter a grade does not bump these versions, so a
        difference here is a difference in the number.
        """
        recorded = RulesVersion(game="1.0.0")
        assert recorded.is_honoured_by(RulesVersion(game="1.0.0"))
        assert not recorded.is_honoured_by(RulesVersion(game="1.0.1"))

    def test_a_session_cannot_be_written_without_its_rules(self):
        from puzzlegen.engine.sessions import SessionRecord

        with pytest.raises(Exception, match="rules"):
            SessionRecord(id="x", player_id="y", manifest_id="z", puzzle_id="w",
                          game_id="g", day_key=DAY, started_at=PLAY_START,
                          last_activity_at=PLAY_START, expires_at=PLAY_START)
