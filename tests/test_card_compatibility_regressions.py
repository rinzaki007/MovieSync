"""Card contract compatibility and isolation regression tests."""
import pytest

from moviesync.cards import Card, CardManifest, CardRegistry


class ResourceCapabilityCard(Card):
    manifest = CardManifest(
        id="test.resource",
        name="Test resource",
        type="resource_source",
        capabilities=("resource.search",),
    )


class StorageCapabilityCard(Card):
    manifest = CardManifest(
        id="test.storage",
        name="Test storage",
        type="storage_target",
        capabilities=("storage.transfer",),
    )


def test_capability_lookup_does_not_assume_every_card_supports_every_operation():
    registry = CardRegistry()
    resource = ResourceCapabilityCard()
    storage = StorageCapabilityCard()
    registry.register(resource)
    registry.register(storage)

    assert [card.card_id for card in registry.find_by_capability("resource.search")] == ["test.resource"]
    assert [card.card_id for card in registry.find_by_capability("storage.transfer")] == ["test.storage"]
    assert registry.find_by_capability("playback.resolve") == []


def test_manifest_rejects_unknown_config_version_and_keeps_capabilities_explicit():
    with pytest.raises(ValueError, match="config_version"):
        CardManifest(id="test.invalid-version", name="Invalid", config_version=0)

    manifest = ResourceCapabilityCard.manifest.to_dict()
    assert manifest["capabilities"] == ["resource.search"]
    assert "storage.transfer" not in manifest["capabilities"]
