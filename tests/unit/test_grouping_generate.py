"""Generation and assembly for game 1.

Built on synthetic content views rather than a real snapshot, so a failure
points at the game rather than at whichever word the lexicon happened to have.
The end-to-end run against real content is batch 6's job.
"""

from __future__ import annotations

import pytest

from puzzlegen.content.query import ContentResult, EntityView, GroupView, Operation
from puzzlegen.core.rng import DeterministicRng, derive_seed
from puzzlegen.core.types import DifficultyBand
from puzzlegen.engine.plugin import GenerationContext, PuzzleCandidate
from puzzlegen.games.grouping import content as content_module
from puzzlegen.games.grouping import generate as generate_module
from puzzlegen.games.grouping.assemble import (
    assemble,
    board_tiles,
    hidden_members,
    leaks_solution,
    solution_groups,
    tile_order,
)
from puzzlegen.games.grouping.descriptor import GAME_ID, VISIBLE_GROUPS, group_size_for
from puzzlegen.games.grouping.generate import (
    BoardPlan,
    borrowings_for,
    generate_candidates,
    has_no_cross_membership,
    is_disjoint,
    member_ids,
    plans_for,
    temptation_of,
    unusable_reason,
)

DAY = "2026-09-28"
SIZE = 5


def entity(name: str, *types: str) -> EntityView:
    return EntityView(
        entity_id=f"entity:{name}",
        name=name,
        types=tuple(t.removeprefix("category:") for t in types),
        type_ids=tuple(types),
    )


def group(category: str, members, *, secondary: str | None = None) -> GroupView:
    return GroupView(
        members=tuple(members),
        shared_category=category.removeprefix("category:"),
        shared_category_id=category,
        secondary_category=(
            secondary.removeprefix("category:") if secondary else None
        ),
        secondary_category_id=secondary,
    )


def visible_group(index: int, size: int = SIZE, *, extra: str = "") -> GroupView:
    """A group of ``size`` tiles named g{index}_0 and so on.

    ``extra`` adds a shared ancestor to every member, which is how a board is
    given cross-group temptation without making any tile ambiguous.
    """
    category = f"category:g{index}"
    ancestors = (category,) + ((extra,) if extra else ())
    return group(category, [entity(f"g{index}_{n}", *ancestors) for n in range(size)])


def hidden_group(
    borrowed: list[tuple[int, int]],
    *,
    category: str = "category:overlay",
    secondary: str | None = None,
) -> GroupView:
    """An overlay group made of tiles that already sit in visible groups."""
    members = [
        entity(f"g{group_index}_{tile}", f"category:g{group_index}", category)
        for group_index, tile in borrowed
    ]
    return group(category, members, secondary=secondary)


def board(size: int = SIZE, *, extra: str = "category:shared") -> dict:
    """Four visible groups plus a hidden group touching each of them."""
    visible = [visible_group(i, size, extra=extra) for i in range(VISIBLE_GROUPS)]
    borrowed = [(i, 0) for i in range(VISIBLE_GROUPS)]
    borrowed += [(i % VISIBLE_GROUPS, 1) for i in range(size - VISIBLE_GROUPS)]
    return {"visible": visible, "hidden": hidden_group(borrowed)}


def context_for(
    visible, hidden, *, enriched=(), day_key: str = DAY, budget: int = 64
) -> GenerationContext:
    content = {
        content_module.VISIBLE: ContentResult(
            operation=Operation.FIND_GROUPS, groups=tuple(visible)
        ),
        content_module.HIDDEN: ContentResult(
            operation=Operation.FIND_GROUPS, groups=tuple(hidden)
        ),
        content_module.HIDDEN_ENRICHED: ContentResult(
            operation=Operation.FIND_INTERSECTING_GROUPS, groups=tuple(enriched)
        ),
    }
    return GenerationContext(
        day_key=day_key,
        game_version="1.0.0",
        difficulty_target=DifficultyBand.MEDIUM,
        locale="en",
        rng=DeterministicRng(derive_seed(day_key, GAME_ID)),
        content=content,
        candidate_budget=budget,
    )


@pytest.fixture
def day() -> str:
    """A day whose drawn group size is 5, so the fixtures line up with it."""
    for offset in range(1, 400):
        candidate = f"2026-{1 + offset // 28:02d}-{1 + offset % 28:02d}"
        if group_size_for(candidate) == SIZE:
            return candidate
    raise AssertionError("no day draws a group size of 5")


class TestBoardConstraints:
    def test_disjoint_groups_pass(self):
        assert is_disjoint([visible_group(i) for i in range(VISIBLE_GROUPS)])

    def test_a_repeated_tile_fails_disjointness(self):
        one = visible_group(0)
        assert not is_disjoint([one, one])

    def test_a_tile_carrying_another_groups_category_is_refused(self):
        """The intended partition must be the only reading.

        A tile in group A that also carries B's category makes the player who
        puts it in B not wrong, and a puzzle where the player is not wrong and
        still marked wrong is the worst failure this game has.
        """
        groups = [visible_group(i) for i in range(VISIBLE_GROUPS)]
        traitor = entity("g0_0", "category:g0", "category:g1")
        groups[0] = group("category:g0", [traitor, *groups[0].members[1:]])
        assert not has_no_cross_membership(groups)

    def test_groups_sharing_only_an_unrelated_ancestor_are_fine(self):
        groups = [
            visible_group(i, extra="category:shared") for i in range(VISIBLE_GROUPS)
        ]
        assert has_no_cross_membership(groups)

    def test_temptation_counts_shared_ancestry(self):
        bare = [visible_group(i, extra="") for i in range(VISIBLE_GROUPS)]
        shared = [
            visible_group(i, extra="category:shared") for i in range(VISIBLE_GROUPS)
        ]
        assert temptation_of(bare) == 0
        assert temptation_of(shared) > 0

    def test_temptation_ignores_the_defining_categories(self):
        """A group resembling itself is not temptation."""
        groups = [visible_group(i, extra="") for i in range(VISIBLE_GROUPS)]
        assert temptation_of(groups) == 0


class TestBorrowings:
    def test_each_hidden_member_is_placed_in_its_visible_group(self, day):
        made = board()
        found = borrowings_for(made["hidden"], made["visible"])
        assert found is not None
        assert len(found) == SIZE

    def test_every_visible_group_gives_up_a_tile(self):
        made = board()
        found = borrowings_for(made["hidden"], made["visible"])
        assert {index for index, _ in found} == set(range(VISIBLE_GROUPS))

    def test_a_hidden_member_not_on_the_board_fails(self):
        made = board()
        stranger = group(
            "category:overlay", [entity("elsewhere", "category:overlay")]
        )
        assert borrowings_for(stranger, made["visible"]) is None

    def test_a_hidden_group_touching_only_three_groups_fails(self):
        """The untouched group would give it away."""
        visible = [visible_group(i) for i in range(VISIBLE_GROUPS)]
        hidden = hidden_group([(0, 0), (0, 1), (1, 0), (2, 0), (2, 1)])
        assert borrowings_for(hidden, visible) is None


class TestPlans:
    def test_a_workable_board_produces_a_plan(self):
        made = board()
        plans = list(
            plans_for(
                made["hidden"], made["visible"], SIZE, enriched=False, budget=100
            )
        )
        assert plans

    def test_a_plan_carries_its_tiles_and_dependencies(self):
        made = board()
        plan = next(
            iter(
                plans_for(
                    made["hidden"], made["visible"], SIZE, enriched=False, budget=100
                )
            )
        )
        assert len(plan.tiles()) == SIZE * VISIBLE_GROUPS
        assert plan.dependency_ids()

    def test_the_candidate_id_does_not_depend_on_discovery_order(self):
        """The same board found two ways must not look like two candidates."""
        made = board()
        forward = BoardPlan(
            hidden=made["hidden"],
            visible=tuple(made["visible"]),
            borrowings=(),
            temptation=1,
            enriched=False,
        )
        backward = BoardPlan(
            hidden=made["hidden"],
            visible=tuple(reversed(made["visible"])),
            borrowings=(),
            temptation=1,
            enriched=False,
        )
        assert forward.candidate_id() == backward.candidate_id()

    def test_a_board_with_no_temptation_is_rejected(self):
        """Four unrelated piles sort themselves."""
        visible = [visible_group(i, extra="") for i in range(VISIBLE_GROUPS)]
        borrowed = [(i, 0) for i in range(VISIBLE_GROUPS)] + [(0, 1)]
        hidden = hidden_group(borrowed)
        assert (
            list(plans_for(hidden, visible, SIZE, enriched=False, budget=100)) == []
        )

    def test_the_budget_bounds_the_search(self):
        made = board()
        assert (
            list(
                plans_for(
                    made["hidden"], made["visible"], SIZE, enriched=False, budget=0
                )
            )
            == []
        )

    def test_too_few_visible_groups_produce_nothing(self):
        made = board()
        assert (
            list(
                plans_for(
                    made["hidden"], made["visible"][:2], SIZE, enriched=False, budget=99
                )
            )
            == []
        )


class TestGenerateCandidates:
    def test_a_workable_day_offers_candidates(self, day):
        made = board()
        found = generate_candidates(context_for(made["visible"], [made["hidden"]], day_key=day))
        assert found

    def test_every_candidate_declares_the_records_it_used(self, day):
        made = board()
        for candidate in generate_candidates(
            context_for(made["visible"], [made["hidden"]], day_key=day)
        ):
            assert candidate.fact_refs

    def test_every_candidate_explains_itself(self, day):
        made = board()
        for candidate in generate_candidates(
            context_for(made["visible"], [made["hidden"]], day_key=day)
        ):
            assert "hidden" in candidate.rationale

    def test_candidates_are_unique(self, day):
        made = board()
        found = generate_candidates(
            context_for(made["visible"], [made["hidden"]], day_key=day)
        )
        assert len({c.candidate_id for c in found}) == len(found)

    def test_generation_is_deterministic(self, day):
        made = board()
        first = generate_candidates(
            context_for(made["visible"], [made["hidden"]], day_key=day)
        )
        second = generate_candidates(
            context_for(made["visible"], [made["hidden"]], day_key=day)
        )
        assert [c.candidate_id for c in first] == [c.candidate_id for c in second]

    def test_a_two_axis_hidden_group_is_offered_first(self, day):
        """It gives the hint system something true to say that is not the
        answer."""
        made = board()
        plain = made["hidden"]
        rich = hidden_group(
            [(i, 0) for i in range(VISIBLE_GROUPS)] + [(0, 1)],
            category="category:overlay2",
            secondary="category:kitchen",
        )
        found = generate_candidates(
            context_for(
                made["visible"], [plain], enriched=[rich], day_key=day
            )
        )
        assert found
        assert found[0].payload["enriched"] is True

    def test_the_budget_caps_how_many_are_offered(self, day):
        made = board()
        found = generate_candidates(
            context_for(made["visible"], [made["hidden"]], day_key=day, budget=1)
        )
        assert len(found) <= 1

    def test_a_hidden_group_of_the_wrong_size_is_skipped(self, day):
        """Equal size is what stops it being identified by counting."""
        made = board()
        short = hidden_group([(i, 0) for i in range(VISIBLE_GROUPS)])
        assert generate_candidates(
            context_for(made["visible"], [short], day_key=day)
        ) == []

    def test_no_visible_groups_produce_nothing(self, day):
        made = board()
        assert generate_candidates(context_for([], [made["hidden"]], day_key=day)) == ()

    def test_generation_draws_no_randomness(self, day):
        """The day's shape is already decided and the choice among candidates
        belongs to the engine. Two sources of randomness for one board is one
        too many."""
        made = board()
        context = context_for(made["visible"], [made["hidden"]], day_key=day)
        before = context.rng.consumed
        generate_candidates(context)
        assert context.rng.consumed == before


class TestDiagnosis:
    def test_a_workable_day_has_no_complaint(self, day):
        made = board()
        assert unusable_reason(context_for(made["visible"], [made["hidden"]], day_key=day)) is None

    def test_missing_visible_groups_are_named(self, day):
        made = board()
        assert "no visible groups" in unusable_reason(
            context_for([], [made["hidden"]], day_key=day)
        )

    def test_too_few_sized_groups_are_counted(self, day):
        made = board()
        reason = unusable_reason(
            context_for(made["visible"][:2], [made["hidden"]], day_key=day)
        )
        assert "need 4" in reason

    def test_a_missing_overlay_is_named(self, day):
        made = board()
        assert "no overlay groups" in unusable_reason(
            context_for(made["visible"], [], day_key=day)
        )

    def test_a_wrongly_sized_overlay_is_named(self, day):
        made = board()
        short = hidden_group([(i, 0) for i in range(VISIBLE_GROUPS)])
        assert "exactly 5 members" in unusable_reason(
            context_for(made["visible"], [short], day_key=day)
        )


@pytest.fixture
def candidate(day) -> PuzzleCandidate:
    made = board()
    found = generate_candidates(
        context_for(made["visible"], [made["hidden"]], day_key=day)
    )
    assert found
    return found[0]


@pytest.fixture
def rng() -> DeterministicRng:
    return DeterministicRng(derive_seed(DAY, GAME_ID))


class TestAssembly:
    def test_a_candidate_becomes_a_puzzle(self, candidate, rng):
        puzzle = assemble(candidate, rng)
        assert puzzle.game_id == GAME_ID
        assert puzzle.candidate_id == candidate.candidate_id

    def test_the_board_holds_every_tile_exactly_once(self, candidate, rng):
        tiles = board_tiles(assemble(candidate, rng))
        assert len(tiles) == SIZE * VISIBLE_GROUPS
        assert len(set(tiles)) == len(tiles)

    def test_every_tile_carries_a_label(self, candidate, rng):
        for tile in assemble(candidate, rng).payload["tiles"]:
            assert tile["label"]

    def test_the_tiles_are_not_in_group_order(self, candidate, rng):
        """A board presented in the order it was built is read left to right."""
        puzzle = assemble(candidate, rng)
        tiles = board_tiles(puzzle)
        grouped = [tile for g in puzzle.solution["groups"] for tile in g["members"]]
        assert list(tiles) != grouped

    def test_the_layout_is_reproducible(self, candidate, rng):
        first = board_tiles(assemble(candidate, rng))
        second = board_tiles(
            assemble(candidate, DeterministicRng(derive_seed(DAY, GAME_ID)))
        )
        assert first == second

    def test_a_different_day_lays_out_differently(self, candidate, rng):
        other = DeterministicRng(derive_seed("2026-10-01", GAME_ID))
        assert board_tiles(assemble(candidate, rng)) != board_tiles(
            assemble(candidate, other)
        )

    def test_the_solution_partitions_the_board(self, candidate, rng):
        puzzle = assemble(candidate, rng)
        groups = solution_groups(puzzle)
        assert len(groups) == VISIBLE_GROUPS
        union: set[str] = set()
        for members in groups:
            assert len(members) == SIZE
            assert not (members & union)
            union |= members
        assert union == set(board_tiles(puzzle))

    def test_the_hidden_group_is_drawn_from_the_board(self, candidate, rng):
        puzzle = assemble(candidate, rng)
        assert hidden_members(puzzle) <= set(board_tiles(puzzle))

    def test_the_hidden_group_touches_every_visible_group(self, candidate, rng):
        puzzle = assemble(candidate, rng)
        assignment = puzzle.solution["assignment"]
        assert {assignment[t] for t in hidden_members(puzzle)} == set(
            range(VISIBLE_GROUPS)
        )

    def test_the_puzzle_declares_its_dependencies(self, candidate, rng):
        assert assemble(candidate, rng).fact_refs == candidate.fact_refs

    def test_presentation_describes_a_rectangle(self, candidate, rng):
        presentation = assemble(candidate, rng).presentation
        assert presentation["columns"] * presentation["rows"] == SIZE * VISIBLE_GROUPS

    def test_the_instructions_name_the_shape_not_the_answer(self, candidate, rng):
        puzzle = assemble(candidate, rng)
        text = puzzle.presentation["instructions"]
        for group in puzzle.solution["groups"]:
            assert group["category"] not in text


class TestThePayloadDoesNotLeak:
    def test_no_category_name_reaches_the_payload(self, candidate, rng):
        """The rule the payload and solution split exists for."""
        assert leaks_solution(assemble(candidate, rng)) == ()

    def test_the_payload_carries_no_assignment(self, candidate, rng):
        payload = assemble(candidate, rng).payload
        assert "assignment" not in payload
        assert "groups" not in payload

    def test_the_payload_does_not_name_the_hidden_members(self, candidate, rng):
        puzzle = assemble(candidate, rng)
        rendered = repr(puzzle.payload)
        assert puzzle.solution["hidden"]["category"] not in rendered

    def test_the_payload_admits_a_hidden_group_exists(self, candidate, rng):
        """Saying so is not a leak. A player never told there is a fifth group
        cannot be expected to look for one."""
        payload = assemble(candidate, rng).payload
        assert payload["hidden_group_exists"] is True
        assert payload["hidden_group_size"] == SIZE

    def test_a_leak_would_be_detected(self, candidate, rng):
        """Non-vacuity: the detector catches a planted leak."""
        from dataclasses import replace

        puzzle = assemble(candidate, rng)
        leaky = replace(
            puzzle, payload={**puzzle.payload, "assignment": puzzle.solution["assignment"]}
        )
        assert leaks_solution(leaky)


class TestAssemblyRefusesABadCandidate:
    def broken(self, candidate: PuzzleCandidate, **changes) -> PuzzleCandidate:
        from dataclasses import replace

        return replace(candidate, payload={**candidate.payload, **changes})

    def test_a_missing_key_is_named(self, candidate, rng):
        from dataclasses import replace

        payload = dict(candidate.payload)
        payload.pop("hidden")
        with pytest.raises(ValueError, match="missing 'hidden'"):
            assemble(replace(candidate, payload=payload), rng)

    def test_the_wrong_number_of_visible_groups_is_refused(self, candidate, rng):
        broken = self.broken(candidate, visible=candidate.payload["visible"][:3])
        with pytest.raises(ValueError, match="needs 4 visible groups"):
            assemble(broken, rng)

    def test_a_short_visible_group_is_refused(self, candidate, rng):
        visible = [dict(g) for g in candidate.payload["visible"]]
        visible[0]["members"] = visible[0]["members"][:-1]
        visible[0]["names"] = visible[0]["names"][:-1]
        with pytest.raises(ValueError, match="expected 5"):
            assemble(self.broken(candidate, visible=visible), rng)

    def test_a_wrongly_sized_hidden_group_is_refused(self, candidate, rng):
        hidden = dict(candidate.payload["hidden"])
        hidden["members"] = hidden["members"][:-1]
        with pytest.raises(ValueError, match="identified by counting"):
            assemble(self.broken(candidate, hidden=hidden), rng)

    def test_a_tile_in_two_groups_is_refused(self, candidate, rng):
        visible = [dict(g) for g in candidate.payload["visible"]]
        visible[1]["members"] = list(visible[1]["members"])
        visible[1]["names"] = list(visible[1]["names"])
        visible[1]["members"][0] = visible[0]["members"][0]
        visible[1]["names"][0] = visible[0]["names"][0]
        with pytest.raises(ValueError, match="two visible groups"):
            assemble(self.broken(candidate, visible=visible), rng)

    def test_a_hidden_member_off_the_board_is_refused(self, candidate, rng):
        hidden = dict(candidate.payload["hidden"])
        hidden["members"] = ["entity:nowhere", *hidden["members"][1:]]
        with pytest.raises(ValueError, match="on the board"):
            assemble(self.broken(candidate, hidden=hidden), rng)

    def test_a_hidden_group_missing_a_visible_group_is_refused(self, candidate, rng):
        """The untouched group would give the hidden one away."""
        puzzle = assemble(candidate, rng)
        assignment = puzzle.solution["assignment"]
        first_group = {t for t, index in assignment.items() if index == 0}
        remaining = [t for t in assignment if t not in first_group]

        hidden = dict(candidate.payload["hidden"])
        hidden["members"] = remaining[: len(hidden["members"])]
        hidden["names"] = [t.removeprefix("entity:") for t in hidden["members"]]
        with pytest.raises(ValueError, match="every visible group"):
            assemble(self.broken(candidate, hidden=hidden), rng)


class TestTileOrder:
    def test_the_same_seed_orders_the_same_way(self):
        tiles = [f"t{n}" for n in range(20)]
        one = tile_order(tiles, DeterministicRng(derive_seed(DAY, GAME_ID)))
        two = tile_order(tiles, DeterministicRng(derive_seed(DAY, GAME_ID)))
        assert one == two

    def test_no_tile_is_lost_or_duplicated(self):
        tiles = [f"t{n}" for n in range(36)]
        shuffled = tile_order(tiles, DeterministicRng(derive_seed(DAY, GAME_ID)))
        assert sorted(shuffled) == sorted(tiles)

    def test_the_order_actually_changes(self):
        tiles = [f"t{n:02d}" for n in range(36)]
        assert tile_order(tiles, DeterministicRng(derive_seed(DAY, GAME_ID))) != tiles


class TestTheShortlistSpansParents:
    """One group per defining category before any category gets a second.

    Sibling grouping produces many overlapping groups from one parent, and
    they tie on how many hidden members they hold. Filled in rank order the
    shortlist is variations of a few parents, and the four-way combination
    search then spends its budget discovering that they share tiles.
    """

    def hidden(self):
        return group(
            "category:hidden",
            [entity(f"h{i}", "category:hidden") for i in range(SIZE)],
        )

    def visible(self, parent: str, count: int):
        """``count`` groups of one parent, each holding one hidden member."""
        return [
            group(
                f"category:{parent}",
                [entity(f"h{index}", f"category:{parent}")]
                + [entity(f"{parent}{index}x{n}", f"category:{parent}") for n in range(SIZE - 1)],
            )
            for index in range(count)
        ]

    def test_every_parent_is_present_before_any_repeats(self):
        pool = (
            self.visible("aaa", 5)
            + self.visible("bbb", 5)
            + self.visible("ccc", 5)
            + self.visible("ddd", 5)
        )

        shortlist = generate_module.shortlist_for(self.hidden(), pool, SIZE)

        assert len(shortlist) == generate_module.VISIBLE_SHORTLIST
        first_four = {g.shared_category_id for g in shortlist[:4]}
        assert first_four == {
            "category:aaa", "category:bbb", "category:ccc", "category:ddd"
        }

    def test_without_the_spread_one_parent_would_fill_the_front(self):
        """The control: rank order alone puts one parent's variants first."""
        pool = self.visible("aaa", 5) + self.visible("bbb", 5)
        ranked = sorted(
            pool,
            key=lambda g: (
                -len(member_ids(g) & member_ids(self.hidden())),
                g.shared_category_id,
            ),
        )

        assert [g.shared_category_id for g in ranked[:5]] == ["category:aaa"] * 5

    def test_a_more_useful_group_still_leads_within_its_parent(self):
        """Spreading reorders across parents, never within one."""
        rich = group(
            "category:aaa",
            [entity(f"h{i}", "category:aaa") for i in range(3)]
            + [entity(f"pad{i}", "category:aaa") for i in range(SIZE - 3)],
        )
        pool = self.visible("aaa", 2) + [rich] + self.visible("bbb", 1)

        shortlist = generate_module.shortlist_for(self.hidden(), pool, SIZE)

        assert shortlist[0] is rich

    def test_the_shortlist_is_still_capped(self):
        pool = [g for parent in "abcdefghij" for g in self.visible(parent, 4)]

        shortlist = generate_module.shortlist_for(self.hidden(), pool, SIZE)

        assert len(shortlist) == generate_module.VISIBLE_SHORTLIST

    def test_groups_holding_no_hidden_member_are_left_out(self):
        useless = group(
            "category:zzz", [entity(f"u{i}", "category:zzz") for i in range(SIZE)]
        )

        shortlist = generate_module.shortlist_for(
            self.hidden(), self.visible("aaa", 2) + [useless], SIZE
        )

        assert useless not in shortlist


class TestTheFloorIsASumOverPairs:
    """The game's own function, agreeing with the coverage tool's model of it."""

    def visible(self, name: str, *shared: str):
        return group(
            f"category:{name}",
            [entity(f"{name}{i}", f"category:{name}", *shared) for i in range(SIZE)],
        )

    def test_one_pair_sharing_a_root_meets_the_floor(self):
        groups = [
            self.visible("a", "category:root"),
            self.visible("b", "category:root"),
            self.visible("c"),
            self.visible("d"),
        ]

        assert temptation_of(groups) >= generate_module.MINIMUM_TEMPTATION

    def test_four_unrelated_groups_do_not(self):
        groups = [self.visible(n) for n in "abcd"]

        assert temptation_of(groups) < generate_module.MINIMUM_TEMPTATION

    def test_the_defining_categories_themselves_do_not_count(self):
        """Two groups whose only shared category is one of the four chosen."""
        groups = [
            self.visible("a", "category:c"),
            self.visible("b", "category:c"),
            self.visible("c"),
            self.visible("d"),
        ]

        assert temptation_of(groups) < generate_module.MINIMUM_TEMPTATION
