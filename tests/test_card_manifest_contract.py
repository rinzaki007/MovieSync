"""Regression tests for the public card manifest contract."""

import pytest

from moviesync.cards import CardManifest


@pytest.mark.parametrize(
    "version",
    [
        "1.0.0",
        "0.1.2",
        "2.0.0-rc.1",
        "1.2.3+build.7",
        "1.2.3-beta.2+build.7",
    ],
)
def test_manifest_accepts_semver_versions(version):
    manifest = CardManifest(id="manifest.version", name="Versioned", version=version)

    assert manifest.to_dict()["version"] == version


@pytest.mark.parametrize("version", ["", "1", "1.0", "01.2.3", "1.0.0-"])
def test_manifest_rejects_invalid_versions(version):
    with pytest.raises(ValueError, match="语义化版本号"):
        CardManifest(id="manifest.version", name="Versioned", version=version)


@pytest.mark.parametrize("capabilities", [
    ("resource.search", "resource.search"),
    ("resource search",),
    ("Resource.search",),
    ("resource.",),
    ("",),
])
def test_manifest_rejects_duplicate_or_invalid_capabilities(capabilities):
    with pytest.raises(ValueError, match="capabilities"):
        CardManifest(
            id="manifest.capabilities",
            name="Capabilities",
            capabilities=capabilities,
        )


def test_manifest_rejects_non_list_capabilities():
    with pytest.raises(ValueError, match="字符串列表"):
        CardManifest(
            id="manifest.capabilities",
            name="Capabilities",
            capabilities="resource.search",
        )


def test_manifest_rejects_blank_name_and_non_text_description():
    with pytest.raises(ValueError, match="名称不能为空"):
        CardManifest(id="manifest.name", name="  ")

    with pytest.raises(ValueError, match="描述必须是文本"):
        CardManifest(id="manifest.description", name="Description", description=None)


@pytest.mark.parametrize(
    "field, message",
    [
        (None, "字段必须是对象"),
        ({"key": "Bad Key"}, "key 必须"),
        ({"key": "token", "required": "yes"}, "必须是布尔值"),
        ({"key": "secret", "secret": 1}, "必须是布尔值"),
    ],
)
def test_manifest_rejects_invalid_config_field_contract(field, message):
    with pytest.raises(ValueError, match=message):
        CardManifest(
            id="manifest.config",
            name="Config",
            config_fields=(field,),
        )


def test_manifest_allows_existing_quark_and_filename_capabilities():
    quark = CardManifest(
        id="quark",
        name="Quark",
        type="storage_target",
        capabilities=(
            "storage.check",
            "storage.accepts.quark_share",
            "storage.list_destination_files",
            "storage.transfer",
        ),
    )
    filename = CardManifest(
        id="filename_recognition",
        name="Filename recognition",
        type="filename_processor",
        capabilities=("filename.parse", "filename.normalize"),
    )

    assert "storage.accepts.quark_share" in quark.capabilities
    assert "filename.normalize" in filename.capabilities


def test_manifest_detaches_mutable_inputs_after_validation():
    capabilities = ["custom.read"]
    config_fields = [{"key": "endpoint", "type": "string"}]
    image_hosts = ["example.com"]

    manifest = CardManifest(
        id="manifest.detached",
        name="Detached metadata",
        capabilities=capabilities,
        config_fields=config_fields,
        image_hosts=image_hosts,
    )

    capabilities.append("custom.write")
    config_fields[0]["key"] = "injected_secret"
    image_hosts.append("attacker.example")

    assert manifest.capabilities == ("custom.read",)
    assert manifest.config_fields[0]["key"] == "endpoint"
    assert manifest.image_hosts == ("example.com",)



def test_manifest_config_fields_are_deeply_immutable_and_serializable():
    config_fields = [
        {
            "key": "rules",
            "type": "json",
            "item_fields": [{"key": "pattern", "type": "string"}],
        }
    ]
    manifest = CardManifest(
        id="manifest.immutable-fields",
        name="Immutable fields",
        config_fields=config_fields,
    )

    with pytest.raises(TypeError):
        manifest.config_fields[0]["key"] = "changed"
    with pytest.raises(TypeError):
        manifest.config_fields[0]["item_fields"][0]["key"] = "changed"

    # Serialization returns fresh, ordinary JSON-compatible containers.
    exported = manifest.to_dict()
    assert exported["config_fields"][0]["item_fields"][0]["key"] == "pattern"
    exported["config_fields"][0]["item_fields"][0]["key"] = "export-only-change"
    assert manifest.config_fields[0]["item_fields"][0]["key"] == "pattern"

    # Caller-owned input is detached as well as the manifest's internal view.
    config_fields[0]["item_fields"][0]["key"] = "caller-change"
    assert manifest.config_fields[0]["item_fields"][0]["key"] == "pattern"
