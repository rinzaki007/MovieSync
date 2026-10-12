"""Regression tests for the extensible, source-aware filename-rule interface."""

from card_templates.filename_recognition import FilenameRecognitionCard
from moviesync.cards import CardManifest, CardRegistry, FilenameProcessorCard
from moviesync.services.resource_sources import ResourceSourceManager


class FakeLogger:
    def exception(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass


def _manager(config, *processors):
    class Store:
        def load(self):
            return config

    registry = CardRegistry()
    for processor in processors:
        registry.register(processor)
    return ResourceSourceManager(Store(), FakeLogger(), registry=registry)


def test_filename_card_applies_source_scoped_rules_only_to_matching_source():
    card = FilenameRecognitionCard()
    config = {
        "rules": [
            {
                "pattern": r"^PANSOU-(\d+)[.]mkv$",
                "replace": r"Show.S02E\1.mkv",
                "source_ids": ["pansou"],
            }
        ]
    }

    assert card.parse_episode_for_source("PANSOU-07.mkv", "pansou", config) == (2, 7)
    assert card.parse_episode_for_source("PANSOU-07.mkv", "telegram", config) == (None, None)


def test_filename_rules_are_tried_in_order_until_one_recognizes_episode():
    card = FilenameRecognitionCard()
    config = {
        "rules": [
            {"pattern": r"^Unknown-(\d+)[.]mkv$", "replace": "no episode"},
            {"pattern": r"^Show-Episode-(\d+)[.]mkv$", "replace": r"Show.S03E\1.mkv"},
        ]
    }

    assert card.parse_episode_for_source("Show-Episode-12.mkv", "pansou", config) == (3, 12)


def test_specialized_processor_runs_before_global_fallback_and_legacy_cards_still_work():
    class SpecializedProcessor(FilenameProcessorCard):
        manifest = CardManifest(
            id="specialized_rules",
            name="Specialized rules",
            type="filename_processor",
            capabilities=("filename.parse",),
        )
        rule_priority = 200

        def parse_episode(self, file_name, config):
            if file_name == "OddName-8.mkv":
                return 4, 8
            return None, None

    class LegacyProcessor(FilenameProcessorCard):
        manifest = CardManifest(
            id="legacy_rules",
            name="Legacy rules",
            type="filename_processor",
            capabilities=("filename.parse",),
        )

        def parse_episode(self, file_name, config):
            if file_name == "LegacyName-9.mkv":
                return None, 9
            return None, None

    config = {
        "cards": {
            "specialized_rules": {"enabled": True, "config": {}},
            "filename_recognition": {"enabled": True, "config": {}},
            "legacy_rules": {"enabled": True, "config": {}},
        }
    }
    manager = _manager(config, FilenameRecognitionCard(), SpecializedProcessor(), LegacyProcessor())

    assert manager.parse_tv_episode("pansou", "OddName-8.mkv") == (4, 8)
    assert manager.parse_tv_episode("pansou", "LegacyName-9.mkv") == (None, 9)


def test_disabled_filename_processor_is_skipped():
    class DisabledProcessor(FilenameProcessorCard):
        manifest = CardManifest(
            id="disabled_rules",
            name="Disabled rules",
            type="filename_processor",
            capabilities=("filename.parse",),
        )

        def parse_episode(self, file_name, config):
            return (1, 5) if file_name == "unrecognized.mkv" else (None, None)

    config = {"cards": {"disabled_rules": {"enabled": False, "config": {}}}}
    manager = _manager(config, DisabledProcessor())

    assert manager.parse_tv_episode("pansou", "unrecognized.mkv") == (None, None)


def test_filename_card_validates_rule_shape_and_regex_safety():
    import pytest

    from moviesync.errors import ConfigValidationError

    card = FilenameRecognitionCard()
    with pytest.raises(ConfigValidationError, match="必须是数组"):
        card.validate_config({"rules": {}})
    with pytest.raises(ConfigValidationError, match="source_ids"):
        card.validate_config({
            "rules": [{
                "pattern": r"^x-(\d+)$",
                "replace": r"Show.S01E\1.mkv",
                "source_ids": ["INVALID ID"],
            }]
        })
    with pytest.raises(ConfigValidationError, match="不安全"):
        card.validate_config({
            "rules": [{"pattern": r"(.+)+$", "replace": r"Show.S01E\1.mkv"}]
        })


def test_global_rules_are_shared_by_all_resource_sources():
    card = FilenameRecognitionCard()
    config = {
        "cards": {
            "filename_recognition": {
                "enabled": True,
                "config": {
                    "rules": [
                        {
                            "pattern": r"^Raw_(\d+)[.]mkv$",
                            "replace": r"Show.S02E\1.mkv",
                        }
                    ]
                },
            }
        }
    }
    manager = _manager(config, card)

    # The same configured rule applies regardless of which resource source found it.
    assert manager.parse_tv_episode("pansou", "Raw_07.mkv") == (2, 7)
    assert manager.parse_tv_episode("telegram", "Raw_07.mkv") == (2, 7)
    assert manager.parse_tv_episode("future_source", "Raw_07.mkv") == (2, 7)


def test_source_scoped_rules_are_opt_in_not_required_for_global_rules():
    card = FilenameRecognitionCard()
    global_config = {
        "rules": [
            {
                "pattern": r"^Shared-(\d+)[.]mkv$",
                "replace": r"Show.S01E\1.mkv",
            }
        ]
    }

    assert card.parse_episode_for_source("Shared-03.mkv", "pansou", global_config) == (1, 3)
    assert card.parse_episode_for_source("Shared-03.mkv", "telegram", global_config) == (1, 3)
