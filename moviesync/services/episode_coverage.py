"""Conservative episode coverage summaries for Smart Follow diagnostics.

Only report gaps inside an observed episode range. A source scan is not proof that
a show has no later episodes, and incomplete scans must never be presented as
confirmed missing episodes.
"""
from __future__ import annotations


def build_episode_coverage(subscription: dict) -> dict:
    diagnostics = subscription.get("last_diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    shares = diagnostics.get("shares")
    shares = shares if isinstance(shares, list) else []

    observed: dict[int, set[int]] = {}
    episode_groups = [diagnostics.get("observed_episodes")]
    episode_groups.extend(
        share.get("episodes")
        for share in shares
        if isinstance(share, dict)
    )
    for episodes in episode_groups:
        if not isinstance(episodes, list):
            continue
        for item in episodes:
            if not isinstance(item, dict):
                continue
            try:
                episode = int(item.get("episode"))
                season = int(item.get("season") or 0)
            except (TypeError, ValueError, OverflowError):
                continue
            if episode > 0 and season >= 0:
                observed.setdefault(season, set()).add(episode)

    try:
        start_episode = max(0, int(subscription.get("start_ep") or 0))
        start_season = max(0, int(subscription.get("start_season") or 0))
    except (TypeError, ValueError, OverflowError):
        start_episode, start_season = 0, 0

    missing = []
    for season, episodes in sorted(observed.items()):
        lower = start_episode if season == start_season else 0
        if season == 0 and start_season > 0:
            # Seasonless files cannot safely be compared against a season-aware baseline.
            continue
        if not episodes:
            continue
        upper = max(episodes)
        absent = [number for number in range(lower + 1, upper) if number not in episodes]
        if absent:
            missing.append({
                "season": season or None,
                "episodes": absent[:100],
                "range_start": lower + 1,
                "range_end": upper - 1,
            })

    complete = (
        diagnostics.get("success") is True
        and not diagnostics.get("interrupted")
        and not diagnostics.get("resolution_failures")
        and not diagnostics.get("source_search_errors")
        and not diagnostics.get("unrecognized")
    )
    known_count = sum(len(values) for values in observed.values())
    return {
        "observed_count": known_count,
        "observed_seasons": [
            {"season": season or None, "episodes": sorted(episodes)}
            for season, episodes in sorted(observed.items())
        ],
        "gaps": missing,
        "confidence": "observed_range" if complete and missing else (
            "incomplete_scan" if missing else "no_gap_proven"
        ),
        "scan_complete": complete,
        "notice": (
            "缺集仅指本次已识别资源覆盖范围内未发现的集数，不代表整部剧的最终总集数。"
            if missing else
            "当前记录不足以确认缺集；未识别文件或不完整扫描不会被当作缺集结论。"
        ),
    }
