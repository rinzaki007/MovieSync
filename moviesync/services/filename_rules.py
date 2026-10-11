"""可配置的影视文件名识别与模板渲染规则。

该模块独立于主业务流程，资源卡片可以通过自己的 config 提供 magic_regex。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath

from ..regex_safety import has_nested_unbounded_quantifier

DEFAULT_TV_MAGIC_REGEX = {
    "pattern": r".*?(?<!\d)([Ss]\d{1,2})?([Ee]?[Pp]?[Xx]?\d{1,3})(?!\d).*?\.(mp4|mkv)",
    "replace": r"\1\2.\3",
}

_SEASON_EPISODE = re.compile(r"(?<![A-Za-z0-9])S\s*(\d{1,2})[\s._-]*E[Pp]?\s*(\d{1,4})(?!\d)", re.I)
_CHINESE_SEASON_EPISODE = re.compile(r"第\s*(\d{1,2})\s*季.{0,8}?第\s*(\d{1,4})\s*[集话期]")
_EPISODE_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])E[Pp]?\s*(\d{1,4})(?!\d)", re.I),
    re.compile(r"第\s*(\d{1,4})\s*[集话期]"),
    re.compile(r"[\[(【](\d{1,4})[\])】]"),
)
_YEAR = re.compile(r"(?<!\d)(18\d{2}|19\d{2}|20\d{2}|21\d{2})(?!\d)")
_DATE = re.compile(r"(?<!\d)(20\d{2})[-_.年/月]?([01]?\d)[-_.月]?([0-3]?\d)日?(?!\d)")
_CHINESE = re.compile(r"[\u4e00-\u9fff]{2,}")
_PART = re.compile(r"(上|中|下|一|二|三|四|五|六|七|八|九|十)(?=部|篇|季|章)")
_VERSION = re.compile(r"[\u4e00-\u9fff]{2,}版")


def _valid_episode(value: int) -> bool:
    return not (1900 <= value <= 2130 or value in {480, 720, 1080, 1440, 2160, 4320})


def _apply_magic_regex(file_name: str, magic_regex: object = None) -> str:
    if not isinstance(magic_regex, dict):
        magic_regex = DEFAULT_TV_MAGIC_REGEX
    pattern = str(magic_regex.get("pattern") or "").strip()
    replacement = str(magic_regex.get("replace") or "")
    if not pattern or has_nested_unbounded_quantifier(pattern):
        # Old/imported config may predate validation; never run an obviously
        # dangerous pattern on filenames from remote resource sources.
        return file_name
    try:
        return re.sub(pattern, replacement, file_name, count=1, flags=re.IGNORECASE)
    except (re.error, IndexError):
        # 卡片配置错误不能让整条订阅检查失败，回退到内置识别规则。
        return file_name


def parse_tv_episode(
    file_name: str,
    magic_regex: object = None,
) -> tuple[int | None, int | None]:
    """解析文件名并返回 (season, episode)，兼容只有集数的旧命名。"""

    def parse_normalized(value: str) -> tuple[int | None, int | None]:
        # Only treat an all-numeric filename stem as an episode.
        # This avoids mistaking years/resolutions for episode numbers.
        numeric_stem = PurePosixPath(value.replace("\\", "/")).stem.strip()
        if re.fullmatch(r"\d{1,4}", numeric_stem):
            episode = int(numeric_stem)
            if _valid_episode(episode):
                return None, episode

        match = _SEASON_EPISODE.search(value) or _CHINESE_SEASON_EPISODE.search(value)
        if match:
            season, episode = int(match.group(1)), int(match.group(2))
            return (season, episode) if _valid_episode(episode) else (None, None)

        for pattern in _EPISODE_PATTERNS:
            match = pattern.search(value)
            if not match:
                continue
            episode = int(match.group(1))
            if _valid_episode(episode):
                return None, episode
        return None, None

    if not file_name:
        return None, None

    original = str(file_name)
    # 先解析原始文件名，避免正则重写丢失“第 N 集”等本来就能识别的信息。
    result = parse_normalized(original)
    if result[1] is not None:
        return result

    normalized = _apply_magic_regex(original, magic_regex)
    if normalized == original:
        return None, None
    return parse_normalized(normalized)


def extract_filename_tokens(file_name: str, task_name: str = "") -> dict[str, str]:
    """从文件名提取模板令牌；令牌值不包含文件扩展名。"""
    path = PurePosixPath(str(file_name or "").replace("\\", "/"))
    stem, ext = path.stem, path.suffix.lstrip(".")
    season, episode = parse_tv_episode(stem)
    date_match = _DATE.search(stem)
    date_value = ""
    if date_match:
        try:
            date_value = datetime(
                int(date_match.group(1)),
                int(date_match.group(2)),
                int(date_match.group(3)),
            ).strftime("%Y%m%d")
        except ValueError:
            date_value = ""
    year_match = _YEAR.search(stem)
    chinese_match = _CHINESE.search(stem)
    part_match = _PART.search(stem)
    version_match = _VERSION.search(stem)
    season_text = f"S{season:02d}" if season is not None else "S01"
    episode_text = str(episode) if episode is not None else ""
    ordinal_width = 2 if len(episode_text) <= 2 else 3
    ordinal = f"{int(episode_text):0{ordinal_width}d}" if episode_text else ""
    return {
        "TASKNAME": str(task_name or "").strip(),
        "II": ordinal,
        "EXT": ext,
        "CHINESE": chinese_match.group(0) if chinese_match else "",
        "DATE": date_value,
        "YEAR": year_match.group(1) if year_match else "",
        "S": f"{season:02d}" if season is not None else "01",
        "SXX": season_text,
        "E": episode_text,
        "PART": part_match.group(1) if part_match else "",
        "VER": version_match.group(0) if version_match else "",
    }


def render_filename_template(
    file_name: str,
    template: str,
    task_name: str = "",
) -> str:
    """将 {TASKNAME}/{II}/{EXT}/{CHINESE}/{DATE}/{YEAR}/{S}/{SXX}/{E}/{PART}/{VER} 替换为令牌值。"""
    tokens = extract_filename_tokens(file_name, task_name)
    episode_value = tokens.get("E", "")
    # {I}/{II}/{III} 分别按 1/2/3 位补零，I 的数量决定排序位数。
    rendered = re.sub(
        r"\{(I{1,10})\}",
        lambda match: (
            f"{int(episode_value):0{len(match.group(1))}d}"
            if episode_value.isdigit()
            else ""
        ),
        str(template or ""),
    )
    rendered = re.sub(
        r"\{([A-Z]+)\}",
        lambda match: tokens.get(match.group(1), match.group(0)),
        rendered,
    )
    return re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", rendered).strip(" .")
