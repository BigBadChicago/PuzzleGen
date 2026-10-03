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
import datetime as dt

from puzzlegen.games.grouping.descriptor import (
    GAME_ID,
    group_sizes_for,
    GROUP_SIZES,
    VISIBLE_GROUPS,
    group_size_for,
)
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
    # Content is keyed by size, because a day asks for every supported size in
    # an order of preference and takes the first that works. These fixtures
    # supply only the day's own size, which is what they are about; the
    # fallback order has its own tests.
    size = group_size_for(day_key)
    content = {
        content_module.sized(content_module.VISIBLE, size): ContentResult(
            operation=Operation.FIND_GROUPS, groups=tuple(visible)
        ),
        content_module.sized(content_module.HIDDEN, size): ContentResult(
            operation=Operation.FIND_GROUPS, groups=tuple(hidden)
        ),
        content_module.sized(content_module.HIDDEN_ENRICHED, size): ContentResult(
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
            relatedness=0.1,
            enriched=False,
        )
        backward = BoardPlan(
            hidden=made["hidden"],
            visible=tuple(reversed(made["visible"])),
            borrowings=(),
            temptation=1,
            relatedness=0.1,
            enriched=False,
        )
        assert forward.candidate_id() == backward.candidate_id()

    def test_a_cross_domain_board_is_published_with_a_low_score(self):
        """Four unrelated piles are a valid board under Option B.

        The floor is 0, so a board whose groups share nothing is published
        rather than refused, and its relatedness score says plainly that the
        groups have little in common. A client may show that number and a
        curator may rank by it; it is a confidence, not a defect.
        """
        visible = [visible_group(i, extra="") for i in range(VISIBLE_GROUPS)]
        borrowed = [(i, 0) for i in range(VISIBLE_GROUPS)] + [(0, 1)]
        hidden = hidden_group(borrowed)

        plans = list(plans_for(hidden, visible, SIZE, enriched=False, budget=100))

        assert plans
        assert plans[0].temptation == 0
        assert plans[0].relatedness == 0.0

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
        ) == ()

    def test_no_visible_groups_produce_nothing(self, day):
        made = board()
        assert generate_candidates(context_for([], [made["hidden"]], day_key=day)) == ()

    def test_every_size_tried_is_named_in_the_refusals(self, day):
        """A day tries each supported size, so "nothing worked" has to say what
        stopped each one rather than reporting only the first."""
        made = board()

        generate_candidates(context_for([], [made["hidden"]], day_key=day))

        reasons = generate_module.last_rejections()
        assert len(reasons) == len(GROUP_SIZES)
        assert all(key.startswith("size ") for key in reasons)

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


class TestRelatednessScoresTheBoard:
    """Temptation is still measured; it is now a 0..1 score, not a gate.

    Under Option B a board is published whatever its relatedness, and the score
    ranks the related ahead of the unrelated rather than refusing the latter.
    """

    def visible(self, name: str, *shared: str):
        return group(
            f"category:{name}",
            [entity(f"{name}{i}", f"category:{name}", *shared) for i in range(SIZE)],
        )

    def test_shared_ancestry_raises_the_score(self):
        related = [
            self.visible("a", "category:root"),
            self.visible("b", "category:root"),
            self.visible("c", "category:root"),
            self.visible("d", "category:root"),
        ]
        unrelated = [self.visible(n) for n in "abcd"]

        assert generate_module.relatedness_of(
            related, SIZE
        ) > generate_module.relatedness_of(unrelated, SIZE)

    def test_four_unrelated_groups_score_zero(self):
        groups = [self.visible(n) for n in "abcd"]

        assert generate_module.relatedness_of(groups, SIZE) == 0.0

    def test_the_score_is_bounded_to_one(self):
        shared = [f"category:s{n}" for n in range(40)]
        groups = [self.visible(name, *shared) for name in "abcd"]

        assert generate_module.relatedness_of(groups, SIZE) == 1.0

    def test_a_defining_category_does_not_raise_the_score(self):
        """A category one group is built on is not resemblance to another."""
        groups = [
            self.visible("a", "category:c"),
            self.visible("b", "category:c"),
            self.visible("c"),
            self.visible("d"),
        ]

        assert generate_module.relatedness_of(groups, SIZE) == 0.0

    def test_the_floor_is_zero_so_nothing_is_refused_for_relatedness(self):
        assert generate_module.MINIMUM_TEMPTATION == 0


class TestRejectionsDoNotLeakBetweenDays:
    """Module state read by ``unusable_reason``. A run that returns early used
    to leave the previous run's refusals in place to be reported as its own."""

    def test_an_early_return_clears_the_last_days_refusals(self):
        from types import SimpleNamespace

        generate_module._LAST_REJECTIONS.update({"not_disjoint": 99})

        day = "2026-10-05"
        result = generate_module.generate_candidates(
            # Carries an rng because candidate ordering now draws one. The
            # stand-in stays a SimpleNamespace rather than a real context: the
            # point of this test is the path where no content exists at all,
            # and a real context would need content to build.
            SimpleNamespace(
                day_key=day,
                content={},
                rng=DeterministicRng(derive_seed(day, GAME_ID)),
            )
        )

        assert result == ()
        # The previous day's count is gone. What remains is this day's own
        # record of trying every size and finding no content for any of them.
        assert "not_disjoint" not in generate_module.last_rejections()
        assert set(generate_module.last_rejections()) == {
            f"size {size}: no visible groups" for size in GROUP_SIZES
        }


class TestFallingBackToAnotherSize:
    """A day names an order of sizes and takes the first the content serves.

    Measured on the real snapshot, 164 parents can supply five tiles and 62 can
    supply nine, so a day that draws 9 and fails is a day with no puzzle. A
    daily game that has a board one day in five is not a daily game.
    """

    def sized_content(self, sizes: dict[int, list], day_key: str) -> dict:
        content = {}
        for size, groups in sizes.items():
            made = board(size)
            content[content_module.sized(content_module.VISIBLE, size)] = ContentResult(
                operation=Operation.FIND_GROUPS, groups=tuple(groups)
            )
            content[content_module.sized(content_module.HIDDEN, size)] = ContentResult(
                operation=Operation.FIND_GROUPS,
                groups=(made["hidden"],) if groups else (),
            )
        return content

    def context(self, content: dict, day_key: str) -> GenerationContext:
        return GenerationContext(
            day_key=day_key,
            game_version="1.0.0",
            difficulty_target=DifficultyBand.MEDIUM,
            locale="en",
            rng=DeterministicRng(b"0123456789abcdef"),
            content=content,
            candidate_budget=64,
        )

    #: Enough consecutive days to contain one of every supported size.
    DAYS = tuple(
        (dt.date(2026, 10, 1) + dt.timedelta(days=n)).isoformat() for n in range(60)
    )

    def day_of(self, size: int) -> str:
        for day in self.DAYS:
            if group_size_for(day) == size:
                return day
        raise AssertionError(f"no day of size {size} in the fixtures")

    def test_the_days_own_size_is_used_when_it_works(self):
        day = self.day_of(SIZE)
        made = board(SIZE)
        content = self.sized_content({SIZE: made["visible"]}, day)
        content[content_module.sized(content_module.HIDDEN, SIZE)] = ContentResult(
            operation=Operation.FIND_GROUPS, groups=(made["hidden"],)
        )

        candidates = generate_candidates(self.context(content, day))

        assert candidates
        assert candidates[0].payload["group_size"] == SIZE

    def test_a_day_whose_own_size_has_no_content_falls_back(self):
        day = self.day_of(SIZE)
        other = next(s for s in GROUP_SIZES if s != SIZE)
        made = board(other)
        content = self.sized_content({SIZE: [], other: made["visible"]}, day)

        candidates = generate_candidates(self.context(content, day))

        assert candidates
        assert candidates[0].payload["group_size"] == other

    def test_the_fallback_order_is_the_days_size_then_ascending(self):
        day = self.day_of(7)

        assert group_sizes_for(day)[0] == 7
        assert list(group_sizes_for(day)[1:]) == sorted(
            s for s in GROUP_SIZES if s != 7
        )

    def test_the_order_is_a_pure_function_of_the_day(self):
        day = self.day_of(SIZE)

        assert group_sizes_for(day) == group_sizes_for(day)

    def test_every_supported_size_appears_exactly_once(self):
        for day in self.DAYS:
            order = group_sizes_for(day)
            assert sorted(order) == sorted(GROUP_SIZES)
            assert len(set(order)) == len(order)

    def test_the_earliest_workable_size_wins_not_the_smallest(self):
        """Two sizes can both work; the day's own preference decides."""
        day = self.day_of(SIZE)
        bigger = next(s for s in GROUP_SIZES if s > SIZE)
        content = self.sized_content(
            {SIZE: board(SIZE)["visible"], bigger: board(bigger)["visible"]}, day
        )

        candidates = generate_candidates(self.context(content, day))

        assert candidates[0].payload["group_size"] == SIZE

    def test_no_size_working_reports_each_one(self):
        day = self.day_of(SIZE)
        content = self.sized_content({size: [] for size in GROUP_SIZES}, day)

        assert generate_candidates(self.context(content, day)) == ()
        reasons = generate_module.last_rejections()
        assert {int(k.split()[1].rstrip(":")) for k in reasons} == set(GROUP_SIZES)

    def test_a_fallback_board_is_internally_consistent(self):
        """The hidden group must match the size actually built, not the day's."""
        day = self.day_of(SIZE)
        other = next(s for s in GROUP_SIZES if s != SIZE)
        content = self.sized_content({SIZE: [], other: board(other)["visible"]}, day)

        payload = generate_candidates(self.context(content, day))[0].payload

        assert payload["group_size"] == other
        assert all(len(g["members"]) == other for g in payload["visible"])
        tiles = {m for g in payload["visible"] for m in g["members"]}
        assert len(tiles) == other * VISIBLE_GROUPS


class TestCoveringQuadruples:
    """The four groups are built from the hidden words, not searched for.

    Sixteen shortlisted groups give 1,820 subsets of four and almost none can
    work. The budget divided by 200 hidden candidates allowed 20 of them, and
    because they were enumerated in index order all 20 shared their first two
    groups. A real day reported 692 overlap refusals and never reached an
    answer the coverage model could see.
    """

    def group_of(self, label: str, members: list[str]) -> GroupView:
        """Every tile also carries a shared domain.

        Real groups drawn from one export share ancestry, and the temptation
        floor refuses four groups that resemble each other in nothing. A
        fixture without it tests a board the game would never accept.
        """
        return group(
            f"category:{label}",
            [entity(m, f"category:{label}", "category:domain") for m in members],
        )

    def world(self):
        """Four groups that hold the hidden words, and decoys that do not."""
        hidden_words = ["h0", "h1", "h2", "h3", "h4"]
        visible = [
            self.group_of("a", ["h0", "a1", "a2", "a3", "a4"]),
            self.group_of("b", ["h1", "b1", "b2", "b3", "b4"]),
            self.group_of("c", ["h2", "c1", "c2", "c3", "c4"]),
            self.group_of("d", ["h3", "h4", "d1", "d2", "d3"]),
        ]
        decoys = [
            self.group_of(f"x{n}", [f"x{n}_{i}" for i in range(5)]) for n in range(12)
        ]
        hidden = group("category:hidden", [entity(w, "category:hidden") for w in hidden_words])
        return hidden, visible, decoys

    def test_the_answer_is_found(self):
        hidden, visible, decoys = self.world()

        found = list(generate_module.covering_quadruples(hidden, [*decoys, *visible]))

        wanted = {g.shared_category_id for g in visible}
        assert any({g.shared_category_id for g in quad} == wanted for quad in found)

    def test_decoys_alone_are_never_offered(self):
        """A set of four holding no hidden word cannot be a board, and the old
        enumeration spent its whole budget on exactly those."""
        hidden, visible, decoys = self.world()

        found = list(generate_module.covering_quadruples(hidden, [*decoys, *visible]))

        assert all(
            any(member_ids(hidden) & member_ids(g) for g in quad) for quad in found
        )

    def test_it_is_found_early_even_with_the_answer_last(self):
        """The property the budget needs: the answer is near the front."""
        hidden, visible, decoys = self.world()

        found = list(generate_module.covering_quadruples(hidden, [*decoys, *visible]))

        assert len(found) <= 40

    def test_every_offer_has_four_distinct_groups(self):
        hidden, visible, decoys = self.world()

        for quad in generate_module.covering_quadruples(hidden, [*decoys, *visible]):
            assert len(quad) == VISIBLE_GROUPS
            assert len({id(g) for g in quad}) == VISIBLE_GROUPS

    def test_each_set_is_offered_once(self):
        hidden, visible, decoys = self.world()

        found = [
            frozenset(g.shared_category_id for g in quad)
            for quad in generate_module.covering_quadruples(hidden, [*decoys, *visible])
        ]

        assert len(found) == len(set(found))

    def test_a_hidden_word_no_group_holds_yields_nothing(self):
        hidden, visible, _ = self.world()
        orphan = group(
            "category:hidden",
            [entity(w, "category:hidden") for w in ["h0", "h1", "h2", "h3", "nowhere"]],
        )

        assert list(generate_module.covering_quadruples(orphan, visible)) == []

    def test_results_are_deterministic(self):
        hidden, visible, decoys = self.world()
        shortlist = [*decoys, *visible]

        first = [tuple(g.shared_category_id for g in q)
                 for q in generate_module.covering_quadruples(hidden, shortlist)]
        second = [tuple(g.shared_category_id for g in q)
                  for q in generate_module.covering_quadruples(hidden, shortlist)]

        assert first == second

    def test_a_plan_is_produced_where_the_old_budget_would_have_failed(self):
        """End to end through plans_for, with a budget of 20: the number the
        divided budget actually allowed on the day this came from."""
        hidden, visible, decoys = self.world()

        plans = list(
            generate_module.plans_for(
                hidden, [*decoys, *visible], 5, enriched=False, budget=20, rejected={}
            )
        )

        assert plans
        assert {g.shared_category_id for g in plans[0].visible} == {
            g.shared_category_id for g in visible
        }
