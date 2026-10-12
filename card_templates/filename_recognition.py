"""One shared filename-recognition engine for every resource source."""
from __future__ import annotations

import re
from typing import Any

from moviesync.cards import CardManifest, FilenameProcessorCard
from moviesync.errors import ConfigValidationError
from moviesync.regex_safety import has_nested_unbounded_quantifier
from moviesync.services.filename_rules import DEFAULT_TV_MAGIC_REGEX, parse_tv_episode


class FilenameRecognitionCard(FilenameProcessorCard):
    """Configurable global filename recognizer with optional source-scoped exceptions."""

    # This card provides general fallback rules. More specific third-party processors
    # inherit the higher default priority and are tried first.
    rule_priority = 0

    manifest = CardManifest(
        id="filename_recognition",
        name="文件名识别",
        version="1.1.0",
        type="filename_processor",
        description="统一识别不同资源来源中的季集信息和特殊文件命名。",
        capabilities=("filename.parse", "filename.normalize"),
        config_fields=(
            {
                "key": "magic_regex",
                "label": "兼容旧版特殊命名规则",
                "type": "json",
                "editor": "object_fields",
                "item_fields": (
                    {"key": "pattern", "label": "匹配规则", "placeholder": "例如：自定义文件名匹配表达式"},
                    {"key": "replace", "label": "替换规则", "placeholder": "例如：统一后的剧名和集数格式"},
                ),
                "default": DEFAULT_TV_MAGIC_REGEX,
                "description": "仅为旧配置保留。新增规则请统一添加到下方列表，默认对所有资源来源生效。",
            },
            {
                "key": "rules",
                "label": "全局文件名识别规则",
                "type": "json",
                "default": [],
                "description": (
                    "所有资源搜索来源共用这套规则，按数组顺序尝试。每项包含 pattern、replace；"
                    "通常无需填写 source_ids，只有确实存在来源特殊格式时才设置。内置格式始终优先。"
                ),
            },
        ),
    )

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
        normalized = super().validate_config(config)

        magic = normalized.get("magic_regex", DEFAULT_TV_MAGIC_REGEX)
        if magic is None:
            magic = DEFAULT_TV_MAGIC_REGEX
        if not isinstance(magic, dict):
            raise ConfigValidationError("兼容特殊命名规则必须是对象")
        normalized["magic_regex"] = self._validate_rule(magic, "兼容特殊命名规则")

        rules = normalized.get("rules", [])
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
