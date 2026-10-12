"""One shared filename-recognition engine for every resource source."""
from __future__ import annotations

import re
from typing import Any

from moviesync.cards import CardManifest, FilenameProcessorCard
from moviesync.errors import ConfigValidationError
from moviesync.regex_safety import has_nested_unbounded_quantifier
from moviesync.services.filename_rules import parse_media_filename, parse_tv_episode


class FilenameRecognitionCard(FilenameProcessorCard):
    """Configurable global filename recognizer with optional source-scoped exceptions."""

    # This card provides general fallback rules. More specific third-party processors
    # inherit the higher default priority and are tried first.
    rule_priority = 0

    manifest = CardManifest(
        id="filename_recognition",
        name="文件名识别",
        version="1.2.0",
        type="filename_processor",
        description="自动识别电影年份、剧集季集、动画集数和综艺期数；大多数情况无需手动配置。",
        capabilities=("filename.parse", "filename.normalize"),
        config_fields=(
            {
                "key": "rules",
                "label": "特殊文件名规则（高级设置）",
                "type": "json",
                "editor": "rule_list",
                "item_fields": (
                    {"key": "pattern", "label": "原始文件名的匹配方式", "placeholder": "仅特殊命名需要填写，例如 Raw_S(\\d+)_E(\\d+)"},
                    {"key": "replace", "label": "用于识别的标准格式（不会改动原文件）", "placeholder": "例如：Show.S\\1E\\2.mkv"},
                    {
                        "key": "source_ids",
                        "label": "只对哪些资源来源生效（可选）",
                        "placeholder": "留空表示所有来源；多个 ID 用逗号分隔",
                    },
                ),
                "default": [],
                "description": (
                    "电影、剧集、动画和综艺通常会自动识别，不需要添加规则。只有文件名特别、识别不到时才使用这里的高级设置；"
                    "不熟悉正则表达式可以先不填写。来源范围留空时对所有资源来源生效，旧版规则会自动迁移。"
                ),
            },
        ),
    )

    def parse_filename(self, file_name: str) -> dict[str, object]:
        """返回名称、媒体类型、年份与季集等结构化识别结果。"""
        return parse_media_filename(file_name)

    def parse_episode(
        self, file_name: str, config: dict[str, Any]
    ) -> tuple[int | None, int | None]:
        return self.parse_episode_for_source(file_name, "", config)

    def parse_episode_for_source(
        self,
        file_name: str,
        source_id: str,
        config: dict[str, Any],
    ) -> tuple[int | None, int | None]:
        """所有来源共用内置格式与全局规则；来源限定规则仅用于少数例外。"""
        builtin = parse_tv_episode(file_name, {"pattern": "", "replace": ""})
        if builtin[1] is not None:
            return builtin

        config = config if isinstance(config, dict) else {}
        rules = config.get("rules", [])
        if isinstance(rules, list):
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                source_ids = rule.get("source_ids", [])
                if source_ids and (
                    not isinstance(source_ids, list)
                    or source_id not in source_ids
                ):
                    continue
                parsed = self._parse_rule(file_name, rule)
                if parsed[1] is not None:
                    return parsed

        # Preserve settings saved by older versions and old resource cards.
        magic = config.get("magic_regex")
        if isinstance(magic, dict) and str(magic.get("pattern") or "").strip():
            return self._parse_rule(file_name, magic)
        return None, None

    @staticmethod
    def _parse_rule(
        file_name: str, rule: dict[str, Any]
    ) -> tuple[int | None, int | None]:
        pattern = str(rule.get("pattern") or "").strip()
        replacement = str(rule.get("replace") or "")
        if not pattern:
            return None, None
        return parse_tv_episode(file_name, {"pattern": pattern, "replace": replacement})

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        # 先保留旧版字段用于迁移；新版表单不再展示 magic_regex。
        legacy_magic = config.get("magic_regex") if isinstance(config, dict) else None
        normalized = super().validate_config(config)

        rules = normalized.get("rules", [])
        if (
            isinstance(rules, list)
            and isinstance(legacy_magic, dict)
            and str(legacy_magic.get("pattern") or "").strip()
        ):
            # 把旧规则追加到新版列表末尾；若内容已存在则不重复添加。
            migrated = self._validate_rule(legacy_magic, "旧版特殊命名规则")
            migrated["source_ids"] = []
            if not any(
                isinstance(item, dict)
                and item.get("pattern") == migrated["pattern"]
                and item.get("replace") == migrated["replace"]
                and not item.get("source_ids")
                for item in rules
            ):
                rules = [*rules, migrated]
        if rules is None:
            rules = []
        if not isinstance(rules, list) or len(rules) > 100:
            raise ConfigValidationError("扩展识别规则必须是数组，且最多 100 条")
        validated_rules = []
        for index, item in enumerate(rules, start=1):
            if not isinstance(item, dict):
                raise ConfigValidationError(f"第 {index} 条扩展识别规则必须是对象")
            rule = self._validate_rule(item, f"第 {index} 条扩展识别规则")
            source_ids = item.get("source_ids", [])
            if source_ids is None:
                source_ids = []
            if (
                not isinstance(source_ids, list)
                or len(source_ids) > 100
                or any(
                    not isinstance(source_id, str)
                    or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", source_id)
                    for source_id in source_ids
                )
            ):
                raise ConfigValidationError(
                    f"第 {index} 条规则的 source_ids 必须是有效的资源卡片 ID 数组"
                )
            rule["source_ids"] = list(dict.fromkeys(source_ids))
            validated_rules.append(rule)
        normalized["rules"] = validated_rules
        # The old field is no longer part of the saved schema after migration.
        normalized.pop("magic_regex", None)
        return normalized

    @staticmethod
    def _validate_rule(rule: dict[str, Any], label: str) -> dict[str, str]:
        pattern = str(rule.get("pattern") or "").strip()
        replacement = str(rule.get("replace") or "")
        if len(pattern) > 1000 or len(replacement) > 200:
            raise ConfigValidationError(f"{label}的匹配规则或替换规则过长")
        if has_nested_unbounded_quantifier(pattern):
            raise ConfigValidationError(f"{label}包含不安全的嵌套重复")
        try:
            if pattern:
                re.compile(pattern)
        except re.error as exc:
            raise ConfigValidationError(f"{label}无效：{exc}") from exc
        return {"pattern": pattern, "replace": replacement}


def create_card(context):
    return FilenameRecognitionCard()
