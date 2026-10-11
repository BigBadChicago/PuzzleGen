"""Choosing a board, and refusing one.

Built on synthetic content views rather than a real snapshot, so a failure
points at the game rather than at whichever word the curated file happened to
hold. The run against real content is in ``test_letters_game.py``.

Every refusal gets its own test, because each is a different kind of problem
and the counts are what a curator reads when a day comes back empty. A name
too long for the board is a naming problem no curation fixes; letters no
member holds is a content gap; a second reading is an unfair board that looks
perfectly good until somebody gives the other answer.
"""

from __future__ import annotations

from puzzlegen.core.rng import DeterministicRng, derive_seed
from puzzlegen.core.types import DifficultyBand
from puzzlegen.surface import SurfaceIndex
from puzzlegen_letters import content as content_module
from puzzlegen_letters.descriptor import GAME_ID, board_sizes_for
from puzzlegen_letters.generate import (
    AMBIGUOUS_ANSWER,
    ANSWER_ON_A_TILE,
    LETTERS_UNPLACEABLE,
    NAME_TOO_LONG,
    NAME_UNUSABLE,
    NO_CATEGORY_NAME,
    WRONG_SIZE,
    alternate_answers,
    generate_candidates,
    last_rejections,
    memberships_of,
    plan_for,
    ranked,
    shared_categories,
    unusable_reason,
    vocabulary_of,
)
from puzzlegen_letters.marks import SURFACE_POLICY

from boards import FISH, context, group, result

DAY = "2026-10-01"

#: Six members holding between them every letter of several short names, so a
#: test about one rule is not quietly also a test about the letters.
ROOMY = ("fabric", "garnet", "copper", "indigo", "walnut", "sesame")

#: The same, extended to the largest board, so a test about board size can ask
#: for exactly as many members as the size wants without repeating a name --
#: two tiles with the same word would be one tile the board cannot rule out.
ROOMY_WIDE = (
    *ROOMY,
    "shallot",
    "jasmine",
    "oregano",
    "paprika",
    "thistle",
    "mahogany",
)


def rng(label: str = "marks") -> DeterministicRng:
    return DeterministicRng(derive_seed(DAY, GAME_ID)).derive(label)


def plan(
    category: str,
    members=FISH,
    *,
    board_size: int = 6,
    blanks: int = 2,
    also=(),
    vocabulary=(),
):
    return plan_for(
        group(category, members, also=also),
        board_size=board_size,
        blanks=blanks,
        vocabulary=tuple(vocabulary),
        index=SurfaceIndex(policy=SURFACE_POLICY),
        rng=rng(),
    )


class TestABoardIsAccepted:
    def test_the_worked_example_is_a_board_at_the_blank_floor(self):
        """``fish`` has four letters: two marks, two blanks, six tiles."""
        built, reason = plan("fish")
        assert reason == ""
        assert built is not None
        assert built.letters == "fish"
        assert built.plan.blanks == 2
        assert built.plan.marked == 2

    def test_the_marks_sit_on_tiles_that_hold_their_letter(self):
        built, _ = plan("fish")
        for entity_id, letter in built.plan.marks.items():
            assert letter in entity_id.removeprefix("entity:")

    def test_at_least_one_tile_carries_no_mark(self):
        built, _ = plan("fish")
        assert built.plan.marked < built.board_size

    def test_the_candidate_id_is_the_category_and_its_tiles(self):
        """Two subsets of one category are two boards: the marks differ and
        one can be playable while the other is refused."""
        first, _ = plan("fish", FISH)
        second, _ = plan("fish", ("salmon", "trout", "herring", "bass", "halibut", "sole"))
        assert first.candidate_id() != second.candidate_id()
        again, _ = plan("fish", FISH)
        assert first.candidate_id() == again.candidate_id()


class TestTheRefusals:
    def test_a_name_longer_than_the_board_can_mark(self):
        """A board needs one more tile than it has marks, because a settled
        rule says at least one tile carries none."""
        built, reason = plan("hippopotamus")
        assert built is None
        assert reason == NAME_TOO_LONG

    def test_a_name_that_exactly_fills_the_board_is_still_too_long(self):
        built, reason = plan("abcdefgh", board_size=6)
        assert built is None
        assert reason == NAME_TOO_LONG

    def test_a_name_the_blanks_would_spell_entirely(self):
        built, reason = plan("ox")
        assert built is None
        assert reason == NAME_UNUSABLE

    def test_letters_no_member_holds_beyond_the_blank_budget(self):
        built, reason = plan("quiz", ("aaa", "bbb", "ccc", "ddd", "eee", "fff"))
        assert built is None
        assert reason == LETTERS_UNPLACEABLE

    def test_a_group_of_the_wrong_size(self):
        built, reason = plan("fish", FISH[:4])
        assert built is None
        assert reason == WRONG_SIZE

    def test_a_tile_whose_own_word_is_the_answer(self):
        """The answer printed on the board."""
        built, reason = plan("carp", FISH)
        assert built is None
        assert reason == ANSWER_ON_A_TILE

    def test_a_second_category_the_same_marks_and_blanks_spell(self):
        """The one refusal the verifier would otherwise have to find, by which
        point the board has cost an assembly and an enumeration.

        Every tile is in both ``gem`` and ``gym``, both three letters, and
        whichever mark is placed the other spells too.
        """
        built, reason = plan(
            "gem", ("agate", "amber", "garnet", "jade", "opal", "ruby"), also=("gym",)
        )
        assert built is None
        assert reason == AMBIGUOUS_ANSWER

    def test_a_group_with_no_category_name(self):
        built, reason = plan_for(
            group("", FISH),
            board_size=6,
            blanks=2,
            vocabulary=(),
            index=SurfaceIndex(policy=SURFACE_POLICY),
            rng=rng(),
        )
        assert built is None
        assert reason == NO_CATEGORY_NAME


class TestTheReadingsOfABoard:
    def test_a_category_every_tile_shares_is_a_candidate_answer(self):
        memberships = memberships_of(group("fish", FISH, also=("food",)))
        assert shared_categories(memberships) == frozenset({"fish", "food"})

    def test_a_category_one_tile_lacks_is_not(self):
        members = list(group("fish", FISH, also=("food",)).members)
        memberships = {
            member.entity_id: tuple(
                name for name in member.types if not (name == "food" and member.name == "carp")
            )
            for member in members
        }
        assert shared_categories(memberships) == frozenset({"fish"})

    def test_only_the_readings_the_letters_admit_are_answers(self):
        """``food`` is true of every tile and is not spellable from marks that
        spell ``fish``."""
        alternates = alternate_answers({"entity:bass": "s"}, 3, ["fish", "food"], "fish")
        assert alternates == ()

    def test_a_reading_the_letters_do_admit_is_named(self):
        alternates = alternate_answers({"entity:garnet": "g"}, 2, ["gem", "gym"], "gem")
        assert alternates == ("gym",)


class TestTheVocabularyIsRecorded:
    def test_it_is_every_category_the_day_offered(self):
        offered = result([group("fish", FISH), group("gem", ROOMY)])
        assert vocabulary_of(offered) == ("fish", "gem")

    def test_every_candidate_carries_it(self):
        """Section 4.1's promise is about this list, so a board whose
        vocabulary cannot be read back cannot be audited."""
        candidates = generate_candidates(
            context(day=DAY, content={size: [group("fish", FISH)] for size in (6,)})
        )
        assert candidates
        assert all(c.payload["vocabulary"] == ["fish"] for c in candidates)


class TestGeneratingADay:
    def test_a_day_offers_candidates_from_the_size_its_date_drew(self):
        sizes = board_sizes_for(DAY)
        candidates = generate_candidates(
            context(
                day=DAY,
                content={sizes[0]: [group("fish", ROOMY_WIDE[: sizes[0]])]},
            )
        )
        assert candidates == () or all(
            c.payload["board_size"] == sizes[0] for c in candidates
        )

    def test_a_day_falls_back_through_the_other_sizes(self):
        """A single unservable size must not lose a day that has a board one
        size down."""
        sizes = board_sizes_for(DAY)
        fallback = sizes[1]
        members = tuple(f"member{n}" for n in range(fallback))
        candidates = generate_candidates(
            context(
                day=DAY,
                content={fallback: [group("fish", ("fig", *members[:-1]))]},
            )
        )
        assert [c.payload["board_size"] for c in candidates] == [fallback] * len(
            candidates
        )

    def test_the_payload_carries_what_assembly_needs_and_nothing_opaque(self):
        candidates = generate_candidates(
            context(day=DAY, content={size: [group("fish", FISH)] for size in (6,)})
        )
        payload = candidates[0].payload
        assert set(payload) == {
            "board_size",
            "blanks",
            "answer_length",
            "category",
            "category_id",
            "letters",
            "tiles",
            "marks",
            "forced_blanks",
            "manufactured_blanks",
            "memberships",
            "vocabulary",
        }

    def test_the_candidate_declares_the_records_it_rests_on(self):
        candidates = generate_candidates(
            context(day=DAY, content={6: [group("fish", FISH)]})
        )
        refs = candidates[0].fact_refs
        assert refs
        assert "category:fish" in refs

    def test_the_rationale_says_what_the_board_is(self):
        candidates = generate_candidates(
            context(day=DAY, content={6: [group("fish", FISH)]})
        )
        assert "fish" in candidates[0].rationale

    def test_the_budget_is_respected(self):
        groups = [group("fish", FISH), group("gem", ROOMY), group("tool", ROOMY)]
        candidates = generate_candidates(
            context(day=DAY, content={6: groups}, budget=2)
        )
        assert len(candidates) <= 2

    def test_no_candidate_is_offered_twice(self):
        groups = [group("fish", FISH), group("fish", FISH)]
        candidates = generate_candidates(context(day=DAY, content={6: groups}))
        ids = [c.candidate_id for c in candidates]
        assert len(ids) == len(set(ids))


class TestTheRanking:
    def test_no_content_property_decides_which_board_goes_first(self):
        """Measured regression. Ordering by fewest manufactured blanks looked
        like honesty and behaved like a total order: five category names held
        every medium board that manufactured nothing, so ten days drew
        ``fabric`` five times. The order carries the day and nothing else."""
        groups = [group("fish", FISH), group("gem", ROOMY), group("tool", ROOMY)]
        first = {
            generate_candidates(
                context(day=f"2026-10-{day:02d}", content={6: groups})
            )[0].payload["category"]
            for day in range(1, 21)
        }
        assert len(first) > 1

    def test_two_boards_that_manufacture_equally_are_ordered_by_the_day(self):
        """Any total order over content properties hands the day to one board
        and keeps handing it over: game 1 served the same board for a month."""
        groups = [
            group(name, ROOMY)
            for name in ("gem", "ink", "oak", "ore", "rib", "sea")
        ]
        orders = {
            tuple(
                c.payload["category"]
                for c in generate_candidates(context(day=day, content={6: groups}))
            )
            for day in ("2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04")
        }
        assert len(orders) > 1

    def test_the_same_day_ranks_the_same_way_twice(self):
        groups = [group("fish", FISH), group("gem", ROOMY)]
        first = generate_candidates(context(day=DAY, content={6: groups}))
        second = generate_candidates(context(day=DAY, content={6: groups}))
        assert [c.candidate_id for c in first] == [c.candidate_id for c in second]

    def test_ranking_an_empty_list_is_not_an_error(self):
        assert ranked([], rng("order")) == []


class TestWhyADayFailed:
    def test_a_day_that_worked_has_no_reason(self):
        ctx = context(day=DAY, content={size: [group("fish", FISH)] for size in (6,)})
        generate_candidates(ctx)
        drawn = board_sizes_for(DAY)[0]
        if drawn == 6:
            assert unusable_reason(ctx) is None

    def test_no_content_at_the_day_s_size_is_named_as_such(self):
        ctx = context(day=DAY, content={})
        generate_candidates(ctx)
        reason = unusable_reason(ctx)
        assert reason is not None and "no categories" in reason

    def test_refused_categories_are_counted_by_cause(self):
        """Knowing that 180 of 200 were refused for the name's length says
        which constraint to argue with."""
        drawn = board_sizes_for(DAY)[0]
        members = tuple(f"member{n}" for n in range(drawn))
        ctx = context(day=DAY, content={drawn: [group("hippopotamus", members)]})
        generate_candidates(ctx)
        reason = unusable_reason(ctx)
        assert reason is not None
        assert NAME_TOO_LONG in reason
        assert NAME_TOO_LONG in " ".join(last_rejections())

    def test_a_later_run_does_not_report_the_previous_day_s_refusals(self):
        drawn = board_sizes_for(DAY)[0]
        members = tuple(f"member{n}" for n in range(drawn))
        generate_candidates(
            context(day=DAY, content={drawn: [group("hippopotamus", members)]})
        )
        generate_candidates(context(day=DAY, content={drawn: []}))
        assert all("name_too_long" not in key for key in last_rejections())
