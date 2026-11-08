"""Soft-evidence ranking for discovered movie and TV resource candidates."""
from __future__ import annotations

import re
from difflib import SequenceMatcher


class CandidateRanker:
    """Keep candidate ordering independent from search orchestration."""

    @staticmethod
    def _simplify(value: str) -> str:
        return re.sub(r"[^\w\u4e00-\u9fa5]", "", value or "")

    @staticmethod
    def _matches_primary_title(target: str, title_values: list[str]) -> bool:
        """Match a work title without treating a numbered sequel as its base title."""
        normalized_target = CandidateRanker._simplify(target).lower()
        if not normalized_target:
            return False
        for raw_title in title_values:
            # A separated release year is metadata, while an immediately attached
            # number (潜伏6) is part of the work identity.
            cleaned = re.sub(
                r"(?<!\d)[\s(（]+(?:19|20)\d{2}(?!\d)[)）]?", " ", raw_title
            )
            normalized = CandidateRanker._simplify(cleaned).lower()
            position = normalized.find(normalized_target)
            if position < 0:
                continue
            suffix = normalized[position + len(normalized_target):]
            if suffix and suffix[0].isdigit():
                continue
            return True
        return False

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
        primary_title_values = [
            str(source.get(key) or "")
            for key in ("title", "name", "resource_name", "share_name")
            if str(source.get(key) or "").strip()
        ]
        primary_title_text = " ".join(primary_title_values)
        simplified_primary_title = CandidateRanker._simplify(primary_title_text)
        if CandidateRanker._matches_primary_title(normalized_movie_title, primary_title_values):
            # An explicit work-name match in the source title outranks generic episode,
            # resolution, and codec markers. Quality is useful only after identity.
            score += 80
        elif simplified_title and simplified_title in simplified_text:
            # A mention in descriptions/paths is weaker evidence than the source title.
            score += 5
        elif len(simplified_title) >= 5 and len(simplified_primary_title) >= 5:
            # Conservative fuzzy evidence helps long Latin/translated titles tolerate
            # minor punctuation or spelling differences. Never use it to collapse
            # short Chinese titles or sequel identities such as 潜伏 vs 潜伏6.
            sequel_number = re.search(r"(\d+)$", simplified_title)
            primary_sequel = re.search(r"(\d+)", simplified_primary_title)
            same_sequel_identity = not sequel_number or bool(
                primary_sequel and primary_sequel.group(1) == sequel_number.group(1)
            )
            title_variants = []
            for raw_title in primary_title_values:
                cleaned = re.sub(r"(?<!\d)(?:19|20)\d{2}(?!\d)", " ", raw_title)
                cleaned = re.sub(
                    r"\b(?:2160p|1080p|720p|480p|4k|uhd|web[- .]?dl|bluray|bdrip|remux)\b",
                    " ",
                    cleaned,
                    flags=re.I,
                )
                cleaned = re.sub(
                    r"(?:s\d{1,2}\s*e\d{1,4}|第\s*\d+\s*[集话]|(?:ep|e)\s*\d{1,4})",
                    " ",
                    cleaned,
                    flags=re.I,
                )
                normalized_variant = CandidateRanker._simplify(cleaned).lower()
                if normalized_variant:
                    title_variants.append(normalized_variant)
            similarity = max(
                (
                    SequenceMatcher(None, simplified_title, variant).ratio()
                    for variant in title_variants
                ),
                default=0.0,
            )
            if same_sequel_identity and similarity >= 0.88:
                score += 24

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
            alias_values = [
                CandidateRanker._simplify(alias).lower()
                for alias in metadata_values(metadata.get(field))
            ]
            primary_alias_hit = any(
                len(alias) >= 2 and CandidateRanker._matches_primary_title(alias, primary_title_values)
                for alias in alias_values
            )
            broader_alias_hit = any(
                len(alias) >= 2 and alias in simplified_text
                for alias in alias_values
            )
            if primary_alias_hit:
                score += 42
                break
            if broader_alias_hit:
                score += 6
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

