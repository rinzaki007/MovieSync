"""Soft-evidence ranking for discovered movie and TV resource candidates."""
from __future__ import annotations

import re


class CandidateRanker:
    """Keep candidate ordering independent from search orchestration."""

    @staticmethod
    def _simplify(value: str) -> str:
        return re.sub(r"[^\w\u4e00-\u9fa5]", "", value or "")

    @staticmethod
    def priority(
        source: dict,
        movie_title: str,
        media_type: str = "",
        metadata: dict | None = None,
    ) -> int:
        """稳定地把名称/剧集信息较明确的资源排在前面；未知格式仍保留在后续页。"""
        name_fields = (
            "title", "name", "file_name", "filename", "resource_name",
            "share_name", "description", "content", "relative_path", "path",
        )
        text = " ".join(str(source.get(key) or "") for key in name_fields).lower()
        score = 0
        # 多个集数标记通常意味着资源标题包含更明确的剧集范围；
        # 只用于排序，不过滤候选，也不把分辨率差异当作重复资源。
        episode_markers = re.findall(
            r"(?:s\d{1,2}\s*e\d{1,4}|第\s*\d+\s*[集话]|(?:ep|e)\s*\d{1,4})",
            text,
            re.I,
        )
        score += min(len(episode_markers), 4) * 2
        if re.search(r"s\d{1,2}", text, re.I):
            score += 1
        if re.search(r"\.(?:mp4|mkv|avi|mov|flv|wmv|m4v|ts|m2ts|iso)(?:\b|$)", text, re.I):
            score += 2
        if re.search(r"\b(?:2160p|1080p|720p|4k|web[- .]?dl|bluray|bdrip|remux)\b", text, re.I):
            score += 1
        # Release years are metadata, not part of title identity. Strip a
        # trailing parenthesized year before sequel-number checks; otherwise
        # "潜伏6 (2025)" can be mistaken for a title numbered 2025.
        normalized_movie_title = re.sub(
            r"[（(]\s*(19\d{2}|20\d{2})\s*[）)]", "", movie_title
        ).strip()
        simplified_title = CandidateRanker._simplify(normalized_movie_title).lower()
        simplified_text = CandidateRanker._simplify(text)
        primary_title_text = " ".join(
            str(source.get(key) or "")
            for key in ("title", "name", "resource_name", "share_name")
        )
        simplified_primary_title = CandidateRanker._simplify(primary_title_text)
        if simplified_title and simplified_title in simplified_primary_title:
            # Do not treat a numbered sequel as an exact match for an unnumbered
            # title (e.g. searching "潜伏" must not promote "潜伏6：血域").
            suffix = simplified_primary_title.split(simplified_title, 1)[1]
            numbered_sequel = bool(
                re.match(r"^\d{1,2}(?=[\u4e00-\u9fff]|$)", suffix)
                and not simplified_title[-1:].isdigit()
            )
            if numbered_sequel:
                score -= 12
            else:
                # An explicit work-name match in the source title outranks generic
                # episode, resolution, and codec markers.
                score += 80
        elif simplified_title and simplified_title in simplified_text:
            # A mention in descriptions/paths is weaker evidence than the source title.
            score += 5

        # Metadata is soft evidence for ordering, never a hard filter. Share titles
        # often omit credits or years, so absent evidence must not hide a valid result.
        metadata = metadata if isinstance(metadata, dict) else {}

        def metadata_values(value: object) -> list[str]:
            if isinstance(value, str):
                return [part.strip() for part in re.split(r"[,;；、|]", value) if part.strip()]
            if isinstance(value, (list, tuple, set)):
                return [str(part).strip() for part in value if str(part).strip()]
            return []

        for field in ("original_title", "aliases", "alternative_titles", "aka"):
            alias_hit = any(
                len(normalized := CandidateRanker._simplify(alias).lower()) >= 2
                and normalized in simplified_text
                for alias in metadata_values(metadata.get(field))
            )
            if alias_hit:
                score += 3
                break

        year = str(metadata.get("year") or metadata.get("release_year") or "").strip()
        year_match = re.search(r"(?:19|20)\d{2}", year)
        if year_match and re.search(rf"(?<!\d){year_match.group(0)}(?!\d)", text):
            score += 2

        credit_hits = 0
        for field in ("directors", "writers", "casts", "actors"):
            if any(
                len(normalized := CandidateRanker._simplify(person).lower()) >= 2
                and normalized in simplified_text
                for person in metadata_values(metadata.get(field))
            ):
                credit_hits += 1
            if credit_hits >= 2:
                break
        score += min(credit_hits, 2)

        # A sequel number is part of the title, not disposable punctuation:
        # "潜伏6" must not rank "潜伏" TV-season shares above matching film shares.
        sequel_match = re.search(r"(\d+)$", simplified_title)
        if sequel_match:
            base_title = simplified_title[: -len(sequel_match.group(1))]
            if base_title and base_title in simplified_text and simplified_title not in simplified_text:
                score -= 8
        # For a selected movie, explicit episode markers are strong contrary
        # evidence. Keep these candidates visible, but place them lower.
        if media_type == "movie" and re.search(
            r"(?:s\d{1,2}\s*e\d{1,4}|第\s*\d+\s*[集话]|(?:ep|e)\s*\d{1,4})",
            text,
            re.I,
        ):
            score -= 6
        # 标题过于简短或主要由分享说明组成时，降低优先级但仍保留候选。
        meaningful_text = re.sub(
            r"(?:分享|资源|链接|下载|转存|合集|全集|更新|推荐)",
            "",
            simplified_text,
        )
        if len(meaningful_text) < 3:
            score -= 2
        return score

