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



def test_candidate_ranker_uses_alias_in_primary_title_as_strong_evidence():
    alias_title = {"title": "旧梦如故 2026 全47集"}
    incidental_alias = {
        "title": "吞噬星空 S01E47",
        "description": "旧梦如故相关频道推荐",
    }
    metadata = {"aliases": ["旧梦如故"]}

    assert CandidateRanker.priority(
        alias_title, "兰香如故", "tv", metadata
    ) > CandidateRanker.priority(
        incidental_alias, "兰香如故", "tv", metadata
    )


def test_candidate_ranker_fuzzy_match_is_conservative_and_never_hides_sequels():
    near_match = {"title": "The Matriix 1999 BluRay"}
    sequel_mismatch = {"title": "潜伏 电视剧 S01E01"}

    assert CandidateRanker.priority(
        near_match, "The Matrix", "movie"
    ) > CandidateRanker.priority(
        {"title": "Unrelated Movie"}, "The Matrix", "movie"
    )
    assert CandidateRanker.priority(
        sequel_mismatch, "潜伏6", "movie"
    ) < CandidateRanker.priority(
        {"title": "潜伏6 4K WEB-DL"}, "潜伏6", "movie"
    )


def test_base_title_does_not_strongly_match_numbered_sequel():
    exact = {"title": "潜伏 4K WEB-DL"}
    sequel = {"title": "潜伏6：血域 4K WEB-DL"}
    subtitle = {"title": "潜伏：血域 4K WEB-DL"}

    assert CandidateRanker.priority(exact, "潜伏", "movie") > CandidateRanker.priority(
        sequel, "潜伏", "movie"
    )
    # A Chinese subtitle without a sequel number remains a valid title match.
    assert CandidateRanker.priority(subtitle, "潜伏", "movie") > CandidateRanker.priority(
        {"title": "完全无关的作品"}, "潜伏", "movie"
    )


def test_release_year_is_not_mistaken_for_a_sequel_number():
    candidate = {"title": "The Matrix 1999 BluRay"}
    unrelated = {"title": "Unrelated Movie"}

    assert CandidateRanker.priority(candidate, "The Matrix", "movie") > CandidateRanker.priority(
        unrelated, "The Matrix", "movie"
    )
