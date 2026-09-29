"""Playing game 1.

``grade_move`` carries a hard constraint that most of these tests exist to
protect: it must be a pure function of the puzzle and the moves before it,
because the session layer replays the whole ledger on every submission and
raises ``DeterminismError`` when a regrade disagrees with a recorded outcome.
"""

from __future__ import annotations

import pytest
from .test_grouping_verify import clean_board

from puzzlegen.engine.plugin import SessionTelemetry
from puzzlegen.games.grouping.descriptor import DESCRIPTOR, VISIBLE_GROUPS
from puzzlegen.games.grouping.play import (
    OVERLAY,
    PENALTY_PER_HINT,
    PENALTY_PER_MISTAKE,
    POINTS_PER_GROUP,
    TAXONOMIC,
    create_share_artifact,
    empty_state,
    get_hint,
    grade_move,
    hidden_group,
    remaining_tiles,
    render,
    score,
    still_partitionable,
)

SIZE = 5


@pytest.fixture
def puzzle():
    return clean_board(SIZE)


def groups_of(puzzle) -> list[list[str]]:
    return [list(group["members"]) for group in puzzle.solution["groups"]]


def play(puzzle, moves: list[list[str]]):
    """Replay a ledger from scratch, as the session layer does."""
    state = empty_state(puzzle)
    judgements = []
    for move in moves:
        judgement = grade_move(puzzle, {"tiles": move}, state)
        judgements.append(judgement)
        state = dict(judgement.state)
    return judgements, state


def solve_visible(puzzle):
    return play(puzzle, groups_of(puzzle))


class TestGradingTheFirstAxis:
    def test_a_correct_group_is_accepted(self, puzzle):
        judgements, _ = play(puzzle, [groups_of(puzzle)[0]])
        assert judgements[0].correct

    def test_the_group_is_named_back(self, puzzle):
        judgements, _ = play(puzzle, [groups_of(puzzle)[0]])
        assert puzzle.solution["groups"][0]["category"] in judgements[0].note

    def test_a_wrong_group_is_rejected_and_counted(self, puzzle):
        wrong = [groups_of(puzzle)[0][0], *groups_of(puzzle)[1][:4]]
        judgements, state = play(puzzle, [wrong])
        assert not judgements[0].correct
        assert state["mistakes"] == 1

    def test_one_tile_away_is_said_so(self, puzzle):
        near = [*groups_of(puzzle)[0][:4], groups_of(puzzle)[1][0]]
        judgements, _ = play(puzzle, [near])
        assert judgements[0].note == "One tile away"

    def test_a_wide_miss_is_not_called_near(self, puzzle):
        wide = [groups_of(puzzle)[i % VISIBLE_GROUPS][i] for i in range(SIZE)]
        judgements, _ = play(puzzle, [wide])
        assert judgements[0].note == "Not a group"

    def test_the_wrong_number_of_tiles_is_refused(self, puzzle):
        judgements, state = play(puzzle, [groups_of(puzzle)[0][:3]])
        assert not judgements[0].correct
        assert "exactly 5" in judgements[0].note
        assert state["mistakes"] == 0

    def test_a_repeated_tile_is_refused(self, puzzle):
        tile = groups_of(puzzle)[0][0]
        judgements, _ = play(puzzle, [[tile] * SIZE])
        assert "exactly 5 different" in judgements[0].note

    def test_a_tile_not_on_the_board_is_refused(self, puzzle):
        judgements, _ = play(puzzle, [["nowhere", *groups_of(puzzle)[0][1:]]])
        assert "not on the board" in judgements[0].note

    def test_a_malformed_payload_raises(self, puzzle):
        with pytest.raises(ValueError, match="list of tiles"):
            grade_move(puzzle, {"tiles": "g0_0"}, empty_state(puzzle))

    def test_resubmitting_a_solved_group_is_refused(self, puzzle):
        first = groups_of(puzzle)[0]
        judgements, _ = play(puzzle, [first, first])
        assert not judgements[1].correct
        assert "already in a group" in judgements[1].note

    def test_a_refusal_does_not_count_as_a_mistake(self, puzzle):
        """Choosing the wrong number of tiles is not a wrong answer."""
        _, state = play(puzzle, [groups_of(puzzle)[0][:2]])
        assert state["mistakes"] == 0


class TestTheAxisSwitch:
    def test_it_does_not_fire_while_groups_remain(self, puzzle):
        judgements, state = play(puzzle, groups_of(puzzle)[:2])
        assert state["axis"] == TAXONOMIC

    def test_it_fires_when_no_taxonomic_group_remains(self, puzzle):
        _, state = solve_visible(puzzle)
        assert state["axis"] == OVERLAY

    def test_the_switch_is_announced_in_the_note(self, puzzle):
        judgements, _ = solve_visible(puzzle)
        assert "No groups remain on that axis" in judgements[-1].note

    def test_the_note_says_how_many_tiles_the_hidden_group_has(self, puzzle):
        judgements, _ = solve_visible(puzzle)
        assert str(SIZE) in judgements[-1].note

    def test_solved_groups_stay_solved_across_the_switch(self, puzzle):
        _, state = solve_visible(puzzle)
        assert len(state["solved"]) == VISIBLE_GROUPS

    def test_feasibility_is_existence_only(self, puzzle):
        tiles = remaining_tiles(puzzle, empty_state(puzzle))
        assert still_partitionable(puzzle, tiles)

    def test_an_empty_board_is_not_partitionable(self, puzzle):
        assert not still_partitionable(puzzle, [])

    def test_tiles_that_cannot_be_grouped_are_not_partitionable(self, puzzle):
        mixed = [groups_of(puzzle)[i % VISIBLE_GROUPS][i] for i in range(SIZE)]
        assert not still_partitionable(puzzle, mixed)

    def test_the_feasibility_cache_lives_for_one_call(self, puzzle):
        """A cache outliving one replay pass would make the second replay of
        the same move disagree with the first."""
        tiles = remaining_tiles(puzzle, empty_state(puzzle))
        cache: dict = {}
        still_partitionable(puzzle, tiles, cache=cache)
        assert cache
        assert still_partitionable(puzzle, tiles, cache={}) is still_partitionable(
            puzzle, tiles, cache=cache
        )


class TestGradingTheSecondAxis:
    def test_the_hidden_group_completes_the_puzzle(self, puzzle):
        _, state = solve_visible(puzzle)
        judgement = grade_move(
            puzzle, {"tiles": sorted(hidden_group(puzzle))}, state
        )
        assert judgement.correct and judgement.complete and judgement.solved

    def test_the_hidden_category_is_named_only_at_the_end(self, puzzle):
        judgements, state = solve_visible(puzzle)
        assert all(
            puzzle.solution["hidden"]["category"] not in j.note for j in judgements
        )
        final = grade_move(puzzle, {"tiles": sorted(hidden_group(puzzle))}, state)
        assert puzzle.solution["hidden"]["category"] in final.note

    def test_a_wrong_hidden_guess_is_counted(self, puzzle):
        _, state = solve_visible(puzzle)
        wrong = sorted(set(remaining_or_all(puzzle)) - hidden_group(puzzle))[:SIZE]
        judgement = grade_move(puzzle, {"tiles": wrong}, state)
        assert not judgement.correct
        assert judgement.state["mistakes"] == state["mistakes"] + 1

    def test_one_tile_away_is_said_so(self, puzzle):
        _, state = solve_visible(puzzle)
        hidden = sorted(hidden_group(puzzle))
        other = next(t for t in remaining_or_all(puzzle) if t not in hidden)
        judgement = grade_move(puzzle, {"tiles": [*hidden[:-1], other]}, state)
        assert judgement.note == "One tile away"

    def test_a_move_after_completion_is_refused(self, puzzle):
        _, state = solve_visible(puzzle)
        done = grade_move(puzzle, {"tiles": sorted(hidden_group(puzzle))}, state)
        again = grade_move(
            puzzle, {"tiles": sorted(hidden_group(puzzle))}, done.state
        )
        assert not again.correct
        assert "already finished" in again.note


def remaining_or_all(puzzle) -> list[str]:
    return [tile["id"] for tile in puzzle.payload["tiles"]]


class TestPurity:
    def test_replaying_a_ledger_gives_the_same_outcomes(self, puzzle):
        """The property the session layer enforces with DeterminismError."""
        moves = groups_of(puzzle) + [sorted(hidden_group(puzzle))]
        first, _ = play(puzzle, moves)
        second, _ = play(puzzle, moves)
        assert [(j.correct, j.note) for j in first] == [
            (j.correct, j.note) for j in second
        ]

    def test_the_same_move_from_the_same_state_grades_the_same(self, puzzle):
        state = empty_state(puzzle)
        one = grade_move(puzzle, {"tiles": groups_of(puzzle)[0]}, state)
        two = grade_move(puzzle, {"tiles": groups_of(puzzle)[0]}, state)
        assert (one.correct, one.note, one.state) == (two.correct, two.note, two.state)

    def test_grading_does_not_mutate_the_state_it_was_given(self, puzzle):
        state = empty_state(puzzle)
        before = repr(state)
        grade_move(puzzle, {"tiles": groups_of(puzzle)[0]}, state)
        assert repr(state) == before

    def test_an_empty_state_is_accepted_as_a_fresh_board(self, puzzle):
        judgement = grade_move(puzzle, {"tiles": groups_of(puzzle)[0]}, {})
        assert judgement.correct

    def test_order_changes_the_ledger_but_not_the_verdicts(self, puzzle):
        forward, _ = play(puzzle, groups_of(puzzle))
        backward, _ = play(puzzle, list(reversed(groups_of(puzzle))))
        assert all(j.correct for j in forward)
        assert all(j.correct for j in backward)


class TestHints:
    def test_the_first_hint_names_a_theme_not_a_tile(self, puzzle):
        hint = get_hint(puzzle, empty_state(puzzle), 0)
        assert hint.reveals == ()
        assert hint.text

    def test_the_second_hint_names_one_tile(self, puzzle):
        assert len(get_hint(puzzle, empty_state(puzzle), 1).reveals) == 1

    def test_the_third_hint_names_two_and_stops(self, puzzle):
        hint = get_hint(puzzle, empty_state(puzzle), 2)
        assert len(hint.reveals) == 2
        assert hint.exhausted

    def test_no_hint_names_a_whole_group(self, puzzle):
        for used in range(4):
            assert len(get_hint(puzzle, empty_state(puzzle), used).reveals) < SIZE

    def test_hints_cost_something(self, puzzle):
        assert get_hint(puzzle, empty_state(puzzle), 0).cost > 0

    def test_hints_move_on_as_groups_are_solved(self, puzzle):
        _, state = play(puzzle, groups_of(puzzle)[:1])
        first = puzzle.solution["groups"][0]["category"]
        assert first not in get_hint(puzzle, state, 0).text

    def test_the_overlay_phase_hints_at_the_hidden_group(self, puzzle):
        _, state = solve_visible(puzzle)
        hint = get_hint(puzzle, state, 1)
        assert set(hint.reveals) <= hidden_group(puzzle)

    def test_a_finished_first_axis_still_offers_something(self, puzzle):
        _, state = solve_visible(puzzle)
        assert get_hint(puzzle, state, 0).text


class TestScoring:
    def telemetry(self, **changes) -> SessionTelemetry:
        base = {
            "completed": True,
            "attempts": 5,
            "elapsed_ms": 60_000,
            "mistakes": 0,
            "hints_used": 0,
            "efficiency": 1.0,
            "difficulty_score": 0.5,
        }
        base.update(changes)
        return SessionTelemetry(**base)

    def test_a_clean_solve_scores(self, puzzle):
        assert score(puzzle, self.telemetry()).points > 0

    def test_mistakes_cost_points(self, puzzle):
        clean = score(puzzle, self.telemetry()).points
        messy = score(puzzle, self.telemetry(mistakes=3, attempts=8)).points
        assert messy < clean

    def test_hints_cost_points(self, puzzle):
        clean = score(puzzle, self.telemetry()).points
        helped = score(puzzle, self.telemetry(hints_used=2)).points
        assert helped == clean - 2 * PENALTY_PER_HINT

    def test_a_harder_board_is_worth_more(self, puzzle):
        easy = score(puzzle, self.telemetry(difficulty_score=0.1)).points
        hard = score(puzzle, self.telemetry(difficulty_score=0.9)).points
        assert hard > easy

    def test_an_unfinished_game_still_scores_its_groups(self, puzzle):
        points = score(
            puzzle, self.telemetry(completed=False, attempts=2, mistakes=0)
        ).points
        assert points == 2 * POINTS_PER_GROUP

    def test_a_score_never_goes_negative(self, puzzle):
        points = score(
            puzzle, self.telemetry(completed=False, attempts=20, mistakes=20)
        ).points
        assert points == 0

    def test_the_breakdown_explains_the_total(self, puzzle):
        result = score(puzzle, self.telemetry(mistakes=1, attempts=6))
        assert sum(result.breakdown.values()) == result.points

    def test_visible_groups_are_capped(self, puzzle):
        """Attempts beyond the four groups are the hidden round, not a fifth
        visible one."""
        many = score(puzzle, self.telemetry(attempts=9)).breakdown["groups"]
        assert many == VISIBLE_GROUPS * POINTS_PER_GROUP

    def test_mistakes_are_priced_as_declared(self, puzzle):
        clean = score(puzzle, self.telemetry()).points
        one = score(puzzle, self.telemetry(mistakes=1, attempts=6)).points
        assert clean - one == PENALTY_PER_MISTAKE


class TestRendering:
    def test_every_tile_is_an_element(self, puzzle):
        model = render(puzzle, empty_state(puzzle), "en")
        assert len(model.elements) == SIZE * VISIBLE_GROUPS

    def test_an_unsolved_tile_is_idle(self, puzzle):
        model = render(puzzle, empty_state(puzzle), "en")
        assert {e["state"] for e in model.elements} == {"idle"}

    def test_a_solved_tile_says_so(self, puzzle):
        _, state = play(puzzle, groups_of(puzzle)[:1])
        model = render(puzzle, state, "en")
        solved = [e for e in model.elements if e["state"] == "solved"]
        assert len(solved) == SIZE

    def test_every_state_it_uses_is_declared(self, puzzle):
        """A state the descriptor does not declare has no glyph and no spoken
        label, so a player reading shapes cannot see it at all."""
        declared = {s.name for s in DESCRIPTOR.accessibility.state_symbols}
        _, state = play(puzzle, groups_of(puzzle)[:1])
        model = render(puzzle, state, "en")
        assert {e["state"] for e in model.elements} <= declared

    def test_group_names_appear_only_once_earned(self, puzzle):
        empty = render(puzzle, empty_state(puzzle), "en")
        assert not [k for k in empty.labels if k.startswith("group_")]
        _, state = play(puzzle, groups_of(puzzle)[:2])
        later = render(puzzle, state, "en")
        assert len([k for k in later.labels if k.startswith("group_")]) == 2

    def test_the_render_never_names_an_unsolved_group(self, puzzle):
        model = render(puzzle, empty_state(puzzle), "en")
        rendered = repr(model)
        for group in puzzle.solution["groups"]:
            assert group["category"] not in rendered

    def test_the_axis_is_reported(self, puzzle):
        _, state = solve_visible(puzzle)
        assert render(puzzle, state, "en").state["axis"] == OVERLAY

    def test_the_model_is_data_not_markup(self, puzzle):
        rendered = repr(render(puzzle, empty_state(puzzle), "en"))
        assert "<" not in rendered


class TestSharing:
    def events(self, *rows) -> tuple[dict, ...]:
        return tuple(
            {"sequence": index, "kind": kind, "outcome": outcome, "offset_ms": 0}
            for index, (kind, outcome) in enumerate(rows)
        )

    def telemetry(self, events, *, completed=True, **changes) -> SessionTelemetry:
        base = {
            "completed": completed,
            "attempts": len([e for e in events if e["kind"] == "SUBMIT"]),
            "elapsed_ms": 1000,
            "mistakes": len(
                [e for e in events if e.get("outcome") == "INCORRECT"]
            ),
            "hints_used": len([e for e in events if e["kind"] == "HINT"]),
            "efficiency": 1.0,
            "difficulty_score": 0.5,
            "events": events,
        }
        base.update(changes)
        return SessionTelemetry(**base)

    def test_one_row_per_submission(self, puzzle):
        events = self.events(
            ("SUBMIT", "CORRECT"), ("SUBMIT", "INCORRECT"), ("SUBMIT", "CORRECT")
        )
        artifact = create_share_artifact(puzzle, self.telemetry(events), "2026-09-28")
        assert len(artifact.tokens) == 3

    def test_every_token_is_declared_by_the_descriptor(self, puzzle):
        """A token outside the vocabulary is a protocol error, which is what
        lets the engine hold a codepoint allowlist for share text."""
        declared = {t.name for t in DESCRIPTOR.share_tokens}
        events = self.events(
            ("SUBMIT", "CORRECT"),
            ("SUBMIT", "INCORRECT"),
            ("HINT", "NEUTRAL"),
            ("SUBMIT", "CORRECT"),
        )
        artifact = create_share_artifact(puzzle, self.telemetry(events), "2026-09-28")
        assert {token for row in artifact.tokens for token in row} <= declared

    def test_the_final_correct_row_is_the_hidden_group(self, puzzle):
        events = self.events(("SUBMIT", "CORRECT"), ("SUBMIT", "CORRECT"))
        artifact = create_share_artifact(puzzle, self.telemetry(events), "2026-09-28")
        assert artifact.tokens[-1] == ("hidden",)
        assert artifact.tokens[0] == ("solved",)

    def test_an_unfinished_game_has_no_hidden_row(self, puzzle):
        events = self.events(("SUBMIT", "CORRECT"), ("SUBMIT", "INCORRECT"))
        artifact = create_share_artifact(
            puzzle, self.telemetry(events, completed=False), "2026-09-28"
        )
        assert all(row != ("hidden",) for row in artifact.tokens)

    def test_a_hint_gets_its_own_row(self, puzzle):
        events = self.events(("HINT", "NEUTRAL"), ("SUBMIT", "CORRECT"))
        artifact = create_share_artifact(puzzle, self.telemetry(events), "2026-09-28")
        assert artifact.tokens[0] == ("hint",)

    def test_the_share_names_no_tile(self, puzzle):
        events = self.events(("SUBMIT", "CORRECT"), ("SUBMIT", "CORRECT"))
        artifact = create_share_artifact(puzzle, self.telemetry(events), "2026-09-28")
        rendered = repr(artifact)
        for tile in puzzle.payload["tiles"]:
            assert tile["label"] not in rendered

    def test_the_share_names_no_category(self, puzzle):
        events = self.events(("SUBMIT", "CORRECT"),)
        artifact = create_share_artifact(puzzle, self.telemetry(events), "2026-09-28")
        rendered = repr(artifact)
        for group in puzzle.solution["groups"]:
            assert group["category"] not in rendered
        assert puzzle.solution["hidden"]["category"] not in rendered

    def test_the_day_and_outcome_are_carried(self, puzzle):
        events = self.events(("SUBMIT", "CORRECT"),)
        artifact = create_share_artifact(puzzle, self.telemetry(events), "2026-09-28")
        assert artifact.day_key == "2026-09-28"
        assert artifact.outcome == "solved"

    def test_a_session_with_no_events_still_produces_something(self, puzzle):
        artifact = create_share_artifact(
            puzzle, self.telemetry((), completed=False, attempts=2), "2026-09-28"
        )
        assert len(artifact.tokens) == 2
