"""Board variety across days, and honest size reporting.

Separate from ``test_grouping_generate`` because these are regression guards
for one specific failure rather than tests of the generation rules: thirty
consecutive days produced a byte-identical board, and the diagnostic that was
supposed to notice reported the size each day drew instead of the size it was
served, so the run looked like it covered every supported size.

Fixtures are borrowed from the generation tests rather than rebuilt, so the two
files cannot drift into disagreeing about what a board is.
"""

from __future__ import annotations

import datetime as dt

import pytest

from paths import TOOLS

from puzzlegen_grouping.descriptor import GAME_ID, VISIBLE_GROUPS, group_size_for
from puzzlegen_grouping.generate import generate_candidates

from .test_grouping_generate import (
    SIZE,
    context_for,
    entity,
    group,
    visible_group,
)


def days_drawing(size: int, count: int) -> list[str]:
    """``count`` distinct day keys whose drawn group size is ``size``.

    Days are found rather than hard-coded: the draw is a pure function of the
    date, so a hard-coded day would silently stop testing what it claims to if
    the draw ever changed.
    """
    found: list[str] = []
    for offset in range(1, 2000):
        day = f"2026-{1 + offset // 28:02d}-{1 + offset % 28:02d}"
        if 1 <= 1 + offset // 28 <= 12 and group_size_for(day) == size:
            found.append(day)
            if len(found) == count:
                return found
    raise AssertionError(f"could not find {count} days drawing size {size}")


def rival_hidden_groups(size: int = SIZE, how_many: int = 4):
    """Several hidden groups that can all sit in the same visible pool.

    Each borrows one tile from every visible group, at a different tile index,
    so they are genuinely distinct boards rather than relabelled copies of one.
    The real snapshot has five feasible hidden groups at size 5 and served
    boards from exactly one of them; this is that situation in miniature.
    """
    visible = [visible_group(i, size, extra="category:shared") for i in range(VISIBLE_GROUPS)]
    hidden = []
    for n in range(how_many):
        category = f"category:overlay{n}"
        members = [
            entity(
                f"g{group_index}_{(n + group_index) % size}",
                f"category:g{group_index}",
                category,
            )
            for group_index in range(VISIBLE_GROUPS)
        ]
        # Pad to a full-size group from the remaining tiles of group 0, so the
        # hidden group is exactly as large as a visible one and cannot be
        # identified by counting.
        extra = size - VISIBLE_GROUPS
        for k in range(extra):
            index = (n + VISIBLE_GROUPS + k) % size
            members.append(entity(f"g0_{index}", "category:g0", category))
        hidden.append(group(category, members[:size]))
    return visible, hidden


def hidden_of(candidate) -> str:
    """The hidden group a candidate carries, by category name."""
    return str(candidate.payload["hidden"]["category"])


class TestVarietyAcrossDays:
    def test_two_days_at_one_size_differ(self):
        """The core Gap 2 guard.

        Identical content, identical served size, different dates. Before the
        day's RNG reached candidate ordering, the engine's verify-in-order
        always reached the same first survivor and these were byte-identical.
        """
        visible, hidden = rival_hidden_groups()
        first, second = days_drawing(SIZE, 2)
        a = generate_candidates(context_for(visible, hidden, day_key=first))
        b = generate_candidates(context_for(visible, hidden, day_key=second))
        assert a and b
        assert a[0].candidate_id != b[0].candidate_id

    def test_many_days_do_not_collapse_onto_one_board(self):
        """Variety has to survive a run, not just a pair.

        Two days differing could be luck. Thirty days is the length the
        handoff's own diagnostic ran, and it produced one distinct board.
        """
        visible, hidden = rival_hidden_groups()
        leads = {
            generate_candidates(context_for(visible, hidden, day_key=day))[0].candidate_id
            for day in days_drawing(SIZE, 30)
        }
        assert len(leads) > 1

    def test_hidden_groups_vary_across_days_not_only_boards(self):
        """Different boards wearing one hidden group is not variety.

        A player sees the hidden grouping, not the candidate id, so a run that
        varies the four visible groups while always hiding the same category
        reads as the same puzzle every day.
        """
        visible, hidden = rival_hidden_groups()
        hiddens = {
            hidden_of(generate_candidates(context_for(visible, hidden, day_key=day))[0])
            for day in days_drawing(SIZE, 30)
        }
        assert len(hiddens) > 1


class TestReproducibility:
    def test_the_same_day_is_the_same_list(self):
        """Variety must not cost determinism.

        A puzzle has to regenerate byte-identically years later, so the draw
        being day-dependent is only acceptable if it is day-*determined*.
        """
        visible, hidden = rival_hidden_groups()
        day = days_drawing(SIZE, 1)[0]
        first = generate_candidates(context_for(visible, hidden, day_key=day))
        second = generate_candidates(context_for(visible, hidden, day_key=day))
        assert [c.candidate_id for c in first] == [c.candidate_id for c in second]

    def test_ordering_preference_survives_the_shuffle(self):
        """Enriched hidden groups still lead.

        The shuffle runs before a stable sort, so it may only reorder within a
        rank. If it ever reorders across ranks, the hint system loses the
        two-axis hidden groups it depends on for something true to say.
        """
        visible, hidden = rival_hidden_groups()
        enriched = [
            group(
                "category:overlay_rich",
                [
                    entity(f"g{i}_0", f"category:g{i}", "category:overlay_rich")
                    for i in range(VISIBLE_GROUPS)
                ]
                + [
                    entity(f"g0_{k}", "category:g0", "category:overlay_rich")
                    for k in range(1, SIZE - VISIBLE_GROUPS + 1)
                ],
                secondary="category:second_axis",
            )
        ]
        for day in days_drawing(SIZE, 8):
            candidates = generate_candidates(
                context_for(visible, hidden, enriched=enriched, day_key=day)
            )
            assert candidates
            assert candidates[0].payload.get("enriched") is True


class TestBudgetSharing:
    def test_no_single_hidden_group_takes_the_whole_budget(self):
        """The round-robin guard.

        The old loop drained one hidden group's plans until the candidate
        budget was spent and returned from inside the inner loop, so later
        hidden groups were never reached at all. A budget smaller than one
        group's plan count is exactly the condition that triggered it.
        """
        visible, hidden = rival_hidden_groups(how_many=4)
        day = days_drawing(SIZE, 1)[0]
        candidates = generate_candidates(
            context_for(visible, hidden, day_key=day, budget=12)
        )
        assert candidates
        assert len({hidden_of(c) for c in candidates}) > 1

    def test_every_placeable_hidden_group_is_represented(self):
        """With budget to spare, none of them should be missing."""
        visible, hidden = rival_hidden_groups(how_many=4)
        day = days_drawing(SIZE, 1)[0]
        candidates = generate_candidates(
            context_for(visible, hidden, day_key=day, budget=256)
        )
        assert len({hidden_of(c) for c in candidates}) == 4


class TestServedSizeReporting:
    """``generate_days.py`` must report the size served, not the size drawn."""

    @pytest.fixture
    def tool(self):
        import importlib.util
        import sys
        from pathlib import Path

        path = TOOLS / "generate_days.py"
        spec = importlib.util.spec_from_file_location("tool_generate_days_variety", path)
        module = importlib.util.module_from_spec(spec)
        # Registered before execution: ``DayResult`` is a dataclass with
        # string annotations, and resolving them needs the module to be
        # findable in ``sys.modules`` while the class body runs.
        sys.modules[spec.name] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_served_size_comes_from_the_puzzle(self, tool):
        class Puzzle:
            payload = {"group_size": 5}

        class Outcome:
            puzzle = Puzzle()

        assert tool.served_size(Outcome(), 9) == 5

    def test_served_size_falls_back_to_the_draw_when_no_board_exists(self, tool):
        class Outcome:
            puzzle = None

        assert tool.served_size(Outcome(), 9) == 9

    def test_size_table_counts_drawn_and_served_separately(self, tool):
        """A size with draws and no boards must show as exactly that.

        This is the row that previously read as four boards at size 9 when the
        content could not build one.
        """
        results = [
            tool.DayResult(day="2026-10-04", group_size=5, generated=True, drawn_size=9),
            tool.DayResult(day="2026-10-05", group_size=5, generated=True, drawn_size=5),
        ]
        table = tool.by_size(results)
        assert table[9] == (0, 1)
        assert table[5] == (2, 1)


class TestTheDayChoosesAmongEquals:
    """Ranking by an exact content score outranks the day's own draw.

    The engine takes the first candidate that verifies, so a total order over
    content properties decides the board and the day's shuffle only breaks
    ties that exact scores rarely produce. Measured on build A2 after the
    visible pool was fixed to cover nine overlay categories: the generator
    offered boards from several of them and one won all ten days, by a single
    point of temptation. Banding is what gives the draw something to choose.
    """

    def payload(self, temptation: int, *, enriched: bool = False) -> dict:
        return {"temptation": temptation, "enriched": enriched}

    def candidate(self, name: str, temptation: int, *, enriched: bool = False):
        from puzzlegen.engine.plugin import PuzzleCandidate

        return PuzzleCandidate(
            candidate_id=name,
            payload=self.payload(temptation, enriched=enriched),
            fact_refs=("entity:x",),
            rationale="",
        )

    def order_for(self, day: str, candidates):
        from puzzlegen.core.rng import DeterministicRng, derive_seed
        from puzzlegen_grouping.generate import _ordered

        rng = DeterministicRng(derive_seed(day, GAME_ID))
        return [c.candidate_id for c in _ordered(list(candidates), rng)]

    def test_near_equal_boards_are_ordered_by_the_day(self):
        from puzzlegen_grouping.generate import TEMPTATION_BAND

        # Within one band, so no content property separates them.
        candidates = [
            self.candidate(f"c{n}", n) for n in range(TEMPTATION_BAND)
        ]
        days = [
            (dt.date(2026, 10, 1) + dt.timedelta(days=n)).isoformat()
            for n in range(20)
        ]
        leaders = {self.order_for(day, candidates)[0] for day in days}

        assert len(leaders) > 1

    def test_a_better_band_still_leads(self):
        from puzzlegen_grouping.generate import TEMPTATION_BAND

        candidates = [
            self.candidate("plain", 0),
            self.candidate("tempting", TEMPTATION_BAND * 2),
        ]
        for n in range(10):
            day = (dt.date(2026, 10, 1) + dt.timedelta(days=n)).isoformat()
            assert self.order_for(day, candidates)[0] == "tempting"

    def test_a_two_axis_hidden_group_still_leads(self):
        candidates = [
            self.candidate("plain", 24),
            self.candidate("two-axis", 0, enriched=True),
        ]
        for n in range(10):
            day = (dt.date(2026, 10, 1) + dt.timedelta(days=n)).isoformat()
            assert self.order_for(day, candidates)[0] == "two-axis"

    def test_the_same_day_orders_the_same_way(self):
        candidates = [self.candidate(f"c{n}", 1) for n in range(6)]
        day = "2026-10-05"

        assert self.order_for(day, candidates) == self.order_for(day, candidates)
