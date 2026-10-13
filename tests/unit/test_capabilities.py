"""Capabilities observed, not declared.

The property under test is that reach is measured. A game cannot understate
what it touched, because nothing asks it: the profile is built from the port's
own record of what it was asked for.
"""

from __future__ import annotations

import json

import pytest

from puzzlegen.content.port import PortUsage, taxonomies_named_by
from puzzlegen.content.query import ContentQuery, Operation
from puzzlegen.core import ids
from puzzlegen.core.types import DifficultyBand
from puzzlegen.engine.capabilities import (
    CapabilityObserver,
    CapabilityProfile,
    check_profile,
    diff_profiles,
    profile_from_traces,
    read_profile,
    write_profile,
)
from puzzlegen.engine.records import (
    GenerationInputs,
    GenerationTrace,
    VersionSet,
)

from conftest import DAY, PLAY_START


def a_trace(
    *,
    game_id: str = "oddoneout",
    taxonomies: tuple[str, ...] = ("default",),
    operations: dict[str, int] | None = None,
    queries: int = 3,
    results: int = 40,
    day_key: str = DAY,
    succeeded: bool = True,
) -> GenerationTrace:
    return GenerationTrace(
        id=ids.for_generation(day_key, game_id, 0),
        day_key=day_key,
        game_id=game_id,
        inputs=GenerationInputs(
            day_key=day_key,
            game_id=game_id,
            seed_hex="ab" * 16,
            snapshot_id=ids.for_snapshot("test-snapshot"),
            snapshot_content_hash="hash",
            difficulty_target=DifficultyBand.MEDIUM,
        ),
        versions=VersionSet(game="1.0.0", protocol="1.1.0"),
        port_taxonomies=taxonomies,
        port_operations=operations or {"find_category_members": 2},
        port_queries=queries,
        port_results=results,
        puzzle_id=ids.for_puzzle(day_key, game_id, "h") if succeeded else None,
        succeeded=succeeded,
        failure_reason=None if succeeded else "nothing assembled",
        created_at=PLAY_START,
    )


class TestObservedUsage:
    def test_a_query_records_every_taxonomy_it_names(self):
        """Search taxonomy and cross-cutting constraint alike.

        A game reaching a second axis has reached it whichever field carried
        the name, so one field cannot be the definition of reach.
        """
        query = ContentQuery(
            operation=Operation.FIND_GROUPS,
            taxonomy="lexicon",
            intersects_taxonomy="overlay",
            covering_taxonomy="parity",
            group_size=4,
            minimum_covered_members=1,
        )
        assert taxonomies_named_by(query) == {"lexicon", "overlay", "parity"}

    def test_an_unnamed_taxonomy_contributes_nothing(self):
        query = ContentQuery(operation=Operation.FIND_ENTITIES, taxonomy="")
        assert taxonomies_named_by(query) == frozenset()

    def test_usage_accumulates_taxonomies_across_queries(self):
        usage = PortUsage()
        from puzzlegen.content.query import ContentResult

        for taxonomy in ("digits", "numbers", "digits"):
            usage.record(
                ContentQuery(
                    operation=Operation.FIND_CATEGORY_MEMBERS,
                    taxonomy=taxonomy,
                    category="c",
                ),
                ContentResult(operation=Operation.FIND_CATEGORY_MEMBERS),
            )
        assert usage.taxonomies == {"digits", "numbers"}
        assert usage.queries == 3
        assert usage.operation_kinds == {"find_category_members"}

    def test_operation_kinds_are_derived_from_the_counts(self):
        """One source of truth, so the two cannot disagree."""
        usage = PortUsage(operations={"find_pairs": 1, "find_groups": 4})
        assert usage.operation_kinds == {"find_pairs", "find_groups"}



class TestProfiles:
    def test_a_profile_is_built_from_stored_traces(self):
        profile = profile_from_traces(
            [
                a_trace(taxonomies=("digits",), queries=1, results=10),
                a_trace(taxonomies=("digits", "numbers"), queries=4, results=3),
            ],
            game_id="oddoneout",
            game_version="1.0.0",
            snapshot_id=ids.for_snapshot("test-snapshot"),
        )
        assert profile.taxonomies == ("digits", "numbers")
        assert profile.days_observed == 2
        # Ceilings are the maximum of any one run, not a total.
        assert profile.max_queries_in_a_run == 4
        assert profile.max_results_in_a_run == 10

    def test_a_failed_run_still_counts_towards_reach(self):
        """A game that reached into a taxonomy and then failed still reached."""
        profile = profile_from_traces(
            [a_trace(taxonomies=("exotic",), succeeded=False)],
            game_id="oddoneout",
            game_version="1.0.0",
        )
        assert profile.taxonomies == ("exotic",)
        assert profile.days_observed == 1

    def test_a_profile_over_mixed_games_is_refused(self):
        with pytest.raises(ValueError, match="not 'oddoneout'"):
            profile_from_traces(
                [a_trace(game_id="other")],
                game_id="oddoneout",
                game_version="1.0.0",
            )

    def test_the_same_observations_in_a_different_order_compare_equal(self):
        """The artifact's whole job is to be diffed byte for byte."""
        traces = [
            a_trace(taxonomies=("b",), day_key="2026-10-01"),
            a_trace(taxonomies=("a",), day_key="2026-10-02"),
        ]
        first = profile_from_traces(
            traces, game_id="oddoneout", game_version="1.0.0"
        )
        second = profile_from_traces(
            list(reversed(traces)), game_id="oddoneout", game_version="1.0.0"
        )
        assert first == second
        assert first.to_document() == second.to_document()

    def test_an_unsorted_profile_is_refused(self):
        with pytest.raises(ValueError, match="sorted and distinct"):
            CapabilityProfile(
                game_id="g", game_version="1", taxonomies=("b", "a")
            )

    def test_live_usage_and_stored_traces_reach_the_same_profile(self):
        from puzzlegen.content.query import ContentResult

        usage = PortUsage()
        usage.record(
            ContentQuery(
                operation=Operation.FIND_CATEGORY_MEMBERS,
                taxonomy="digits",
                category="c",
            ),
            ContentResult(operation=Operation.FIND_CATEGORY_MEMBERS),
        )
        live = CapabilityObserver(game_id="g", game_version="1")
        live.observe_usage(usage)
        stored = CapabilityObserver(game_id="g", game_version="1")
        stored.observe_trace(
            a_trace(
                game_id="g",
                taxonomies=("digits",),
                operations={"find_category_members": 1},
                queries=1,
                results=0,
            )
        )
        assert live.profile() == stored.profile()


class TestTheDiff:
    def base(self, **overrides) -> CapabilityProfile:
        fields = {
            "game_id": "g",
            "game_version": "1.0.0",
            "taxonomies": ("digits",),
            "operations": ("find_category_members",),
            "max_queries_in_a_run": 1,
            "max_results_in_a_run": 10,
        }
        fields.update(overrides)
        return CapabilityProfile(**fields)

    def test_an_unchanged_profile_diffs_empty(self):
        diff = diff_profiles(self.base(), self.base())
        assert diff.is_empty
        assert not diff.widens_reach
        assert diff.describe() == ()

    def test_a_new_taxonomy_is_a_widened_reach(self):
        """The one finding this whole item exists to produce."""
        diff = diff_profiles(
            self.base(), self.base(taxonomies=("digits", "lexicon"))
        )
        assert diff.taxonomies_added == ("lexicon",)
        assert diff.widens_reach
        assert "taxonomy now reached: lexicon" in diff.describe()

    def test_a_new_operation_is_a_widened_reach(self):
        diff = diff_profiles(
            self.base(),
            self.base(operations=("find_category_members", "find_pairs")),
        )
        assert diff.operations_added == ("find_pairs",)
        assert diff.widens_reach

    def test_a_lost_taxonomy_is_reported_separately(self):
        """Usually a content change that stopped a query matching."""
        diff = diff_profiles(self.base(taxonomies=("digits", "gone")), self.base())
        assert diff.taxonomies_removed == ("gone",)
        assert not diff.widens_reach

    def test_a_raised_ceiling_is_reported_and_is_not_a_widened_reach(self):
        """Recorded, not enforced. A measurement is not a limit."""
        diff = diff_profiles(self.base(), self.base(max_results_in_a_run=180))
        assert diff.ceilings_changed == (("max_results_in_a_run", 10, 180),)
        assert not diff.is_empty
        assert not diff.widens_reach

    def test_a_version_or_snapshot_change_is_named(self):
        diff = diff_profiles(self.base(), self.base(game_version="1.1.0"))
        assert diff.version_changed == ("1.0.0", "1.1.0")
        diff = diff_profiles(self.base(), self.base(snapshot_id="snapshot:x"))
        assert diff.snapshot_changed == ("", "snapshot:x")

    def test_profiles_for_different_games_cannot_be_diffed(self):
        with pytest.raises(ValueError, match="different games"):
            diff_profiles(self.base(), self.base(game_id="other"))


class TestTheArtifact:
    def test_a_profile_round_trips_through_its_file(self, tmp_path):
        profile = CapabilityProfile(
            game_id="g", game_version="1.0.0", taxonomies=("digits",)
        )
        path = tmp_path / "nested" / "g-capabilities.json"
        write_profile(path, profile)
        assert read_profile(path) == profile

    def test_the_file_is_sorted_and_newline_terminated(self, tmp_path):
        """So the file a run produces is the file a diff compares."""
        path = tmp_path / "p.json"
        write_profile(
            path, CapabilityProfile(game_id="g", game_version="1.0.0")
        )
        text = path.read_text(encoding="utf-8")
        assert text.endswith("\n")
        assert list(json.loads(text)) == sorted(json.loads(text))

    def test_a_missing_file_reads_as_nothing_committed_yet(self, tmp_path):
        """A game's first run has nothing to compare against."""
        assert read_profile(tmp_path / "absent.json") is None
        assert (
            check_profile(
                tmp_path / "absent.json",
                CapabilityProfile(game_id="g", game_version="1.0.0"),
            )
            is None
        )

    def test_the_check_fails_on_a_deliberately_altered_committed_profile(
        self, tmp_path
    ):
        path = tmp_path / "p.json"
        observed = CapabilityProfile(
            game_id="g", game_version="1.0.0", taxonomies=("digits",)
        )
        write_profile(path, observed)
        path.write_text(
            json.dumps(
                {
                    **observed.to_document(),
                    "taxonomies": ["digits", "smuggled"],
                }
            ),
            encoding="utf-8",
        )
        diff = check_profile(path, observed)
        assert diff is not None
        assert diff.taxonomies_removed == ("smuggled",)
        assert not diff.is_empty


class TestTheCommittedSymbolicProfiles:
    """The three committed artifacts, read as the records they claim to be."""

    @pytest.mark.parametrize(
        "slug, game_id, taxonomy",
        [
            ("digit-lock", "digit_lock", "digits"),
            ("shape-trio", "shape_trio", "figures"),
            ("odd-number-out", "odd_number_out", "numbers"),
        ],
    )
    def test_each_names_one_game_one_taxonomy_and_thirty_days(
        self, slug, game_id, taxonomy
    ):
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[2]
        profile = read_profile(root / "build" / f"{slug}-capabilities.json")
        assert profile is not None, (
            f"build/{slug}-capabilities.json is committed and must stay so; "
            "the CI check compares a fresh run against it"
        )
        assert profile.game_id == game_id
        assert profile.taxonomies == (taxonomy,)
        assert profile.days_observed == 30
        assert profile.operations == ("find_category_members",)
