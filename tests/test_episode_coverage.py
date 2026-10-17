"""Conservative Smart Follow episode coverage tests."""
from moviesync.services.episode_coverage import build_episode_coverage


def test_reports_only_gaps_inside_observed_range_and_never_claims_future_episodes():
    subscription = {
        "start_season": 1,
        "start_ep": 1,
        "last_diagnostics": {
            "success": True,
            "interrupted": False,
            "resolution_failures": 0,
            "source_search_errors": 0,
            "unrecognized": 0,
            "shares": [{"episodes": [
                {"season": 1, "episode": 2},
                {"season": 1, "episode": 3},
                {"season": 1, "episode": 5},
            ]}],
        },
    }

    coverage = build_episode_coverage(subscription)

    assert coverage["gaps"] == [
        {"season": 1, "episodes": [4], "range_start": 2, "range_end": 4}
    ]
    assert coverage["confidence"] == "observed_range"
    assert "最终总集数" in coverage["notice"]


def test_incomplete_scan_does_not_present_gap_as_confirmed():
    subscription = {
        "start_season": 0,
        "start_ep": 1,
        "last_diagnostics": {
            "success": False,
            "resolution_failures": 1,
            "shares": [{"episodes": [
                {"season": 0, "episode": 2},
                {"season": 0, "episode": 4},
            ]}],
        },
    }

    coverage = build_episode_coverage(subscription)

    assert coverage["gaps"][0]["episodes"] == [3]
    assert coverage["confidence"] == "incomplete_scan"
    assert coverage["scan_complete"] is False


def test_does_not_infer_gaps_from_empty_or_malformed_diagnostics():
    assert build_episode_coverage({})["gaps"] == []
    assert build_episode_coverage({"last_diagnostics": {"shares": "bad"}})["gaps"] == []


def test_season_aware_baseline_does_not_mix_seasonless_episode_numbers():
    subscription = {
        "start_season": 2,
        "start_ep": 7,
        "last_diagnostics": {
            "success": True,
            "shares": [{"episodes": [
                {"season": 1, "episode": 8},
                {"season": 2, "episode": 8},
                {"season": 2, "episode": 10},
                {"season": None, "episode": 9},
            ]}],
        },
    }

    coverage = build_episode_coverage(subscription)

    assert coverage["gaps"] == [
        {"season": 2, "episodes": [9], "range_start": 8, "range_end": 9}
    ]


def test_truncated_episode_observations_are_not_marked_complete():
    coverage = build_episode_coverage({
        "start_season": 0,
        "start_ep": 0,
        "last_diagnostics": {
            "success": True,
            "episode_observations_truncated": True,
            "observed_episodes": [
                {"season": 0, "episode": 1},
                {"season": 0, "episode": 3},
            ],
        },
    })

    assert coverage["gaps"][0]["episodes"] == [2]
    assert coverage["confidence"] == "incomplete_scan"


def test_unknown_season_baseline_does_not_infer_missing_early_season_episodes():
    coverage = build_episode_coverage({
        "start_season": 0,
        "start_ep": 8,
        "last_diagnostics": {
            "success": True,
            "observed_episodes": [
                {"season": 1, "episode": 8},
                {"season": 1, "episode": 10},
            ],
        },
    })

    assert coverage["gaps"][0]["episodes"] == [9]
