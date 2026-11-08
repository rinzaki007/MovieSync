"""Regression corpus for title identity ranking against noisy resource names."""
import json
import pathlib

from moviesync.services.candidate_ranker import CandidateRanker

CASES = json.loads(
    (pathlib.Path(__file__).parent / "search_ranker_cases.json").read_text(encoding="utf-8")
)


def test_realistic_search_ranker_corpus_keeps_exact_title_first():
    for case in CASES:
        ranked = sorted(
            case["candidates"],
            key=lambda candidate: CandidateRanker.priority({"title": candidate}, case["title"]),
            reverse=True,
        )
        assert ranked[0] == case["expected"], case["title"]
