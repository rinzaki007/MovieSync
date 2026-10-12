"""Tests for global filename recognition shared across resource sources."""
from card_templates.filename_recognition import FilenameRecognitionCard
from moviesync.cards import CardManifest, CardRegistry, FilenameProcessorCard
from moviesync.services.filename_rules import parse_tv_episode
from moviesync.services.resource_sources import ResourceSourceManager


class FakeConfigStore:
    def load(self):
        return {
            "cards": {
                "filename_recognition": {
                    "enabled": True,
                    "config": {
                        "magic_regex": {
                            "pattern": "^Show-Episode-([0-9]+)[.]mkv$",
                            "replace": r"Show.S01E\1.mkv",
                        }
                    },
                }
            }
        }


class FakeLogger:
    def exception(self, *args, **kwargs):
        pass


def test_global_filename_rules_apply_without_a_telegram_source():
    registry = CardRegistry()
    registry.register(FilenameRecognitionCard())
    manager = ResourceSourceManager(
        FakeConfigStore(), FakeLogger(), registry=registry
    )

    assert manager.parse_tv_episode("pansou", "Show-Episode-12.mkv") == (1, 12)


def test_filename_processor_is_discovered_by_type_not_fixed_card_id():
    class CustomFilenameProcessor(FilenameProcessorCard):
        manifest = CardManifest(
            id="custom_episode_rules",
            name="Custom episode rules",
            type="filename_processor",
            capabilities=("filename.parse",),
        )

        def parse_episode(self, file_name, config):
            magic = config.get("magic_regex", {})
            return parse_tv_episode(file_name, magic)

    class ConfigStore:
        def load(self):
            return {
                "cards": {
                    "custom_episode_rules": {
                        "enabled": True,
                        "config": {
                            "magic_regex": {
                                "pattern": "^Custom-Episode-([0-9]+)[.]mkv$",
                                "replace": r"Show.S01E\1.mkv",
                            }
                        },
                    }
                }
            }

    registry = CardRegistry()
    registry.register(CustomFilenameProcessor())
    manager = ResourceSourceManager(ConfigStore(), FakeLogger(), registry=registry)

    assert manager.parse_tv_episode(
        "some-resource-source", "Custom-Episode-12.mkv"
    ) == (1, 12)

def test_global_filename_card_rejects_invalid_regex():
    import pytest

    from moviesync.errors import ConfigValidationError

    card = FilenameRecognitionCard()
    with pytest.raises(ConfigValidationError, match="规则无效"):
        card.validate_config({
            "magic_regex": {"pattern": "(", "replace": ""}
        })



def test_search_result_episode_statistics_use_shared_rules_for_each_source():
    from moviesync.services.search import _episode_statistics

    class RuleConfigStore:
        def load(self):
            return {
                "cards": {
                    "filename_recognition": {
                        "enabled": True,
                        "config": {
                            "rules": [
                                {
                                    "pattern": r"^Raw_S(\d+)_E(\d+)[.]mkv$",
                                    "replace": r"Show.S\1E\2.mkv",
                                }
                            ]
                        },
                    }
                }
            }

    registry = CardRegistry()
    registry.register(FilenameRecognitionCard())
    manager = ResourceSourceManager(RuleConfigStore(), FakeLogger(), registry=registry)
    files = [
        {"file_name": "Raw_S02_E07.mkv"},
        {"file_name": "Raw_S02_E08.mkv"},
        {"file_name": "Raw_S03_E01.mkv"},
    ]

    pansou = _episode_statistics(files, "pansou", manager)
    telegram = _episode_statistics(files, "telegram", manager)

    assert pansou == telegram
    assert pansou[1:4] == (3, 3, 0)
    assert "S02 E07-E08" in pansou[0]
    assert "S03 E01" in pansou[0]


def test_legacy_filename_rule_migrates_into_new_rule_list():
    card = FilenameRecognitionCard()
    legacy = {
        "pattern": "^Old-Episode-([0-9]+)[.]mkv$",
        "replace": r"Show.S01E\1.mkv",
    }

    normalized = card.validate_config({"magic_regex": legacy})

    assert "magic_regex" not in normalized
    assert normalized["rules"] == [
        {
            "pattern": legacy["pattern"],
            "replace": legacy["replace"],
            "source_ids": [],
        }
    ]


def test_legacy_filename_rule_is_preserved_when_new_rules_already_exist():
    card = FilenameRecognitionCard()
    legacy = {
        "pattern": "^Old-Episode-([0-9]+)[.]mkv$",
        "replace": r"Show.S01E\1.mkv",
    }
    current = {
        "pattern": r"^New-Episode-([0-9]+)[.]mkv$",
        "replace": r"Show.S01E\1.mkv",
        "source_ids": [],
    }

    normalized = card.validate_config({"magic_regex": legacy, "rules": [current]})

    assert normalized["rules"] == [
        current,
        {
            "pattern": legacy["pattern"],
            "replace": legacy["replace"],
            "source_ids": [],
        },
    ]
