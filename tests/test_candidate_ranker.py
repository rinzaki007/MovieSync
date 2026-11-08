from moviesync.services.candidate_ranker import CandidateRanker
from moviesync.services.search import SearchService


def test_search_service_compatibility_wrapper_uses_candidate_ranker():
    candidate = {
        "title": "兰香如故 (2026) 4K 全47完结",
        "description": "S01E01 1080p",
        "file_name": "第01集.mp4",
    }
    metadata = {"year": "2026", "aliases": ["旧梦如故"]}

    assert SearchService._candidate_priority(
        candidate, "兰香如故", "tv", metadata
    ) == CandidateRanker.priority(candidate, "兰香如故", "tv", metadata)


def test_candidate_ranker_prioritizes_explicit_title_identity():
    direct = {"title": "兰香如故 (2026) 4K 全47完结", "file_name": "第01集.mp4"}
    unrelated = {
        "title": "吞噬星空 S01E47 4K 1080p 全集",
        "description": "频道文案提到兰香如故",
        "file_name": "第47集.mkv",
    }

    assert CandidateRanker.priority(direct, "兰香如故", "tv") > CandidateRanker.priority(
        unrelated, "兰香如故", "tv"
    )


def test_candidate_ranker_does_not_treat_numbered_sequel_as_exact_title_match():
    base = {"title": "潜伏6：血域 4K WEB-DL"}
    sequel = {"title": "潜伏6：血域 4K WEB-DL"}
    plain = {"title": "潜伏 电视剧全集 1080p"}
    assert CandidateRanker.priority(plain, "潜伏", "tv") > CandidateRanker.priority(
        sequel, "潜伏", "tv"
    )
    assert CandidateRanker.priority(sequel, "潜伏6", "movie") > CandidateRanker.priority(
        plain, "潜伏6", "movie"
    )
