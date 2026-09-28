"""Share artifacts.

A share is public text produced from a private puzzle by third-party code, so
every test here is a test of the boundary rather than of the formatting. The
three renderings are checked because a share with only glyphs excludes the
players who most need the alternative, and the redactor is checked with a
game that actively misbehaves, because a redactor only ever matters when the
game is wrong.
"""

from __future__ import annotations

import datetime as dt

import pytest

from puzzlegen.core import ids
from puzzlegen.engine.plugin import GameDescriptor, Puzzle, ShareArtifact, ShareTokenSpec
from puzzlegen.engine.sessions import (
    MoveKind,
    MoveOutcome,
    MoveRecord,
    SessionKind,
    SessionRecord,
    SessionState,
)
from puzzlegen.engine.sharing import (
    MAX_TOKENS_PER_ROW,
    RenderedShare,
    ShareLeak,
    ShareRedactor,
    ShareService,
    forbidden_terms,
    token_legend,
)

from conftest import DAY

NOW = dt.datetime(2026, 9, 27, 12, 0, 0, tzinfo=dt.timezone.utc)


def a_puzzle() -> Puzzle:
    return Puzzle(
        game_id="oddoneout",
        payload={
            "options": ["opt_a", "opt_b", "opt_c", "opt_d"],
            "labels": {
                "opt_a": "tiger",
                "opt_b": "lion",
                "opt_c": "puma",
                "opt_d": "eagle",
            },
            "prompt": "Three share a category",
        },
        solution={"answer": "opt_d"},
        fact_refs=("fact:one",),
    )


def a_session(*, state=SessionState.COMPLETED, kind=SessionKind.LIVE, wrong=1):
    player_id = ids.for_player("tok")
    manifest_id = ids.for_manifest(ids.for_puzzle(DAY, "oddoneout", "h"))
    session = SessionRecord(
        id=ids.for_session(player_id, manifest_id, kind.value),
        player_id=player_id,
        manifest_id=manifest_id,
        puzzle_id=ids.for_puzzle(DAY, "oddoneout", "h"),
        game_id="oddoneout",
        day_key=DAY,
        kind=kind,
        started_at=NOW,
        last_activity_at=NOW,
        expires_at=NOW + dt.timedelta(hours=12),
    )
    sequence = 1
    for index in range(wrong):
        session = session.with_move(
            MoveRecord(
                sequence=sequence,
                kind=MoveKind.SUBMIT,
                outcome=MoveOutcome.INCORRECT,
                server_at=NOW + dt.timedelta(seconds=index * 10),
                payload={"choice": "opt_a"},
            )
        )
        sequence += 1
    session = session.with_move(
        MoveRecord(
            sequence=sequence,
            kind=MoveKind.SUBMIT,
            outcome=MoveOutcome.CORRECT,
            server_at=NOW + dt.timedelta(seconds=95),
            payload={"choice": "opt_d"},
        )
    )
    if state is not SessionState.IN_PROGRESS:
        session = session.terminated(state, session.last_activity_at)
    return session


def misbehaving(game, **artifact_kwargs):
    """A game identical to the reference one except for its share artifact."""

    class Misbehaving:
        def describe(self):
            return game.describe()

        def create_share_artifact(self, puzzle, telemetry, day_key):
            defaults = {
                "game_id": "oddoneout",
                "day_key": day_key,
                "outcome": "correct",
                "tokens": (("correct",),),
            }
            defaults.update(artifact_kwargs)
            return ShareArtifact(**defaults)

    return Misbehaving()


class TestForbiddenTerms:
    def test_labels_and_the_prompt_are_collected(self):
        terms = forbidden_terms(a_puzzle())
        assert "tiger" in terms
        assert "eagle" in terms

    def test_short_words_and_stopwords_are_not(self):
        puzzle = Puzzle(
            game_id="g",
            payload={"prompt": "Which of these does not belong"},
            solution={"answer": "pangolin"},
            fact_refs=("fact:one",),
        )
        terms = forbidden_terms(puzzle)
        # Under four characters, so it could not carry a spoiler on its own.
        assert "not" not in terms
        # Common enough that banning it would make engine text unwritable.
        assert "which" not in terms
        assert "belong" not in terms
        assert "pangolin" in terms

    def test_the_solution_is_included(self):
        puzzle = Puzzle(
            game_id="g",
            payload={"prompt": "pick"},
            solution={"answer": "pangolin"},
            fact_refs=("fact:one",),
        )
        assert "pangolin" in forbidden_terms(puzzle)


class TestRenderedShare:
    def test_all_three_renderings_are_required(self):
        with pytest.raises(ValueError, match="all three"):
            RenderedShare(
                id=ids.for_share(ids.for_session("player:a", "manifest:b", "LIVE"), 1),
                game_id="g",
                day_key=DAY,
                outcome="correct",
                text="X",
                text_plain="",
                alt_text="something",
            )


class TestShareBuilding:
    def test_a_finished_session_renders_three_ways(self, share_service, game):
        share = share_service.build(
            a_session(), game, game.describe(), a_puzzle(), difficulty_score=0.4
        )
        assert "\U0001f7e9" in share.text
        assert "#" in share.text_plain
        assert share.alt_text.startswith("Odd One Out for 2026-09-27")
        assert share.day_key == DAY

    def test_the_counts_line_comes_from_the_ledger(self, share_service, game):
        share = share_service.build(
            a_session(wrong=2), game, game.describe(), a_puzzle(), difficulty_score=0.4
        )
        assert "3 tries" in share.text
        assert "1m 35s" in share.text

    def test_the_share_is_deterministic(self, share_service, game):
        session = a_session()
        first = share_service.build(
            session, game, game.describe(), a_puzzle(), difficulty_score=0.4
        )
        again = share_service.build(
            session, game, game.describe(), a_puzzle(), difficulty_score=0.4
        )
        assert first == again

    def test_no_identifier_reaches_the_text(self, share_service, game):
        session = a_session()
        share = share_service.build(
            session, game, game.describe(), a_puzzle(), difficulty_score=0.4
        )
        for rendering in (share.text, share.text_plain, share.alt_text):
            assert session.id not in rendering
            assert session.player_id not in rendering
            assert session.manifest_id not in rendering

    def test_no_puzzle_label_reaches_the_text(self, share_service, game):
        share = share_service.build(
            a_session(), game, game.describe(), a_puzzle(), difficulty_score=0.4
        )
        for label in ("tiger", "lion", "puma", "eagle"):
            assert label not in share.text.lower()
            assert label not in share.alt_text.lower()

    def test_an_open_session_has_no_share(self, share_service, game):
        with pytest.raises(ValueError, match="still open"):
            share_service.build(
                a_session(state=SessionState.IN_PROGRESS),
                game,
                game.describe(),
                a_puzzle(),
                difficulty_score=0.4,
            )

    def test_a_practice_session_has_no_share(self, share_service, game):
        with pytest.raises(ValueError, match="practice replay"):
            share_service.build(
                a_session(kind=SessionKind.PRACTICE),
                game,
                game.describe(),
                a_puzzle(),
                difficulty_score=0.4,
            )

    def test_a_lost_session_still_shares(self, share_service, game):
        share = share_service.build(
            a_session(state=SessionState.ABANDONED),
            game,
            game.describe(),
            a_puzzle(),
            difficulty_score=0.4,
        )
        assert "not solved" in share.alt_text

    def test_a_game_with_no_declared_tokens_cannot_share(self, share_service, game):
        descriptor = GameDescriptor(
            game_id="tokenless", display_name="Tokenless", game_version="1.0.0"
        )
        with pytest.raises(ShareLeak, match="declares no share tokens"):
            share_service.build(
                a_session(), game, descriptor, a_puzzle(), difficulty_score=0.4
            )

    def test_the_legend_is_the_declared_vocabulary(self, game):
        assert token_legend(game.describe()) == game.describe().share_tokens


class TestRedaction:
    def test_an_undeclared_token_is_a_protocol_error(self, share_service, game):
        plugin = misbehaving(game, tokens=(("sneaky",),))
        with pytest.raises(ShareLeak, match="undeclared_token"):
            share_service.build(
                a_session(), plugin, game.describe(), a_puzzle(), difficulty_score=0.4
            )

    def test_a_wrong_day_is_refused(self, share_service, game):
        plugin = misbehaving(game, day_key="2026-01-01")
        with pytest.raises(ShareLeak, match="built a share for"):
            share_service.build(
                a_session(), plugin, game.describe(), a_puzzle(), difficulty_score=0.4
            )

    def test_an_oversized_row_is_refused(self, share_service, game):
        plugin = misbehaving(
            game, tokens=(tuple(["correct"] * (MAX_TOKENS_PER_ROW + 1)),)
        )
        with pytest.raises(ShareLeak, match="over the"):
            share_service.build(
                a_session(), plugin, game.describe(), a_puzzle(), difficulty_score=0.4
            )

    def test_a_spoiler_in_the_outcome_is_refused(self, share_service, game):
        plugin = misbehaving(game, outcome="the answer was eagle")
        with pytest.raises(ShareLeak, match="spoil the day"):
            share_service.build(
                a_session(), plugin, game.describe(), a_puzzle(), difficulty_score=0.4
            )

    def test_a_spoiler_in_the_display_name_is_refused(self, share_service, game):
        descriptor = game.describe()
        spoiled = GameDescriptor(
            game_id="spoiler",
            display_name="Eagle Hunt",
            game_version="1.0.0",
            accessibility=descriptor.accessibility,
            share_tokens=descriptor.share_tokens,
        )
        with pytest.raises(ShareLeak, match="display name"):
            share_service.build(
                a_session(), game, spoiled, a_puzzle(), difficulty_score=0.4
            )

    def test_a_crashing_share_builder_is_caught(self, share_service, game):
        class Exploding:
            def describe(self):
                return game.describe()

            def create_share_artifact(self, puzzle, telemetry, day_key):
                raise RuntimeError("boom")

        with pytest.raises(ShareLeak, match="failed to build"):
            share_service.build(
                a_session(), Exploding(), game.describe(), a_puzzle(), difficulty_score=0.4
            )

    def test_a_wrong_return_type_is_caught(self, share_service, game):
        class WrongType:
            def describe(self):
                return game.describe()

            def create_share_artifact(self, puzzle, telemetry, day_key):
                return {"tokens": []}

        with pytest.raises(ShareLeak, match="create_share_artifact"):
            share_service.build(
                a_session(), WrongType(), game.describe(), a_puzzle(), difficulty_score=0.4
            )


class TestRedactorDirectly:
    def test_a_record_id_is_caught(self):
        with pytest.raises(ShareLeak, match="record id"):
            ShareRedactor().check(
                "played entity:tiger today",
                allowed_glyphs=(),
                banned_ids=(),
                where="test text",
            )

    def test_a_hash_run_is_caught(self):
        with pytest.raises(ShareLeak, match="hex run"):
            ShareRedactor().check(
                "seed abcdef0123456789",
                allowed_glyphs=(),
                banned_ids=(),
                where="test text",
            )

    def test_an_undeclared_glyph_is_caught(self):
        with pytest.raises(ShareLeak, match="declared token"):
            ShareRedactor().check(
                "score \U0001f7e5",
                allowed_glyphs=("\U0001f7e9",),
                banned_ids=(),
                where="test text",
            )

    def test_a_declared_glyph_passes(self):
        ShareRedactor().check(
            "score \U0001f7e9",
            allowed_glyphs=("\U0001f7e9",),
            banned_ids=(),
            where="test text",
        )

    def test_a_banned_identifier_is_caught(self):
        with pytest.raises(ShareLeak, match="identifier"):
            ShareRedactor().check(
                "from PLAYERabc",
                allowed_glyphs=(),
                banned_ids=("playerabc",),
                where="test text",
            )

    def test_the_length_cap_holds(self):
        with pytest.raises(ShareLeak, match="over the"):
            ShareRedactor(max_chars=10).check(
                "x" * 20, allowed_glyphs=(), banned_ids=(), where="test text"
            )

    def test_the_line_cap_holds(self):
        with pytest.raises(ShareLeak, match="lines"):
            ShareRedactor(max_lines=2).check(
                "a\nb\nc", allowed_glyphs=(), banned_ids=(), where="test text"
            )


class TestShareTokenSpec:
    def test_the_plain_rendering_is_one_ascii_character(self):
        with pytest.raises(ValueError, match="one ASCII"):
            ShareTokenSpec(name="x", glyph="\U0001f7e9", plain="##", label="x")
        with pytest.raises(ValueError, match="one ASCII"):
            ShareTokenSpec(name="x", glyph="\U0001f7e9", plain="\u25a0", label="x")

    def test_duplicate_glyphs_are_refused(self, game):
        with pytest.raises(ValueError, match="distinct glyph"):
            GameDescriptor(
                game_id="dupe",
                display_name="Dupe",
                game_version="1.0.0",
                share_tokens=(
                    ShareTokenSpec(name="a", glyph="X", plain="a", label="a"),
                    ShareTokenSpec(name="b", glyph="X", plain="b", label="b"),
                ),
            )

    def test_duplicate_plain_characters_are_refused(self):
        with pytest.raises(ValueError, match="distinct plain"):
            GameDescriptor(
                game_id="dupe2",
                display_name="Dupe",
                game_version="1.0.0",
                share_tokens=(
                    ShareTokenSpec(name="a", glyph="X", plain="z", label="a"),
                    ShareTokenSpec(name="b", glyph="Y", plain="z", label="b"),
                ),
            )
