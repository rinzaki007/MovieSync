"""Load trusted single-file card plugins from the persistent data directory.

Only administrator-provided Python files are accepted. A plugin file is executable
code, not a sandboxed data format; the web UI must warn users to install trusted code.
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import Any

from .cards import (
    Card,
    CardRegistry,
    FilenameProcessorCard,
    MetadataProviderCard,
    NotificationCard,
    PlaybackProviderCard,
    ResourceSourceCard,
    StorageTargetCard,
)

# Git blob IDs for bundled card versions shipped before automatic card upgrades.
# These hashes allow safe migration of old empty-marker installs without replacing
# administrator-edited files.
_LEGACY_BUNDLED_CARD_BLOBS = {
    "tmdb.py": {
        "76292d41d3af54ffbd17fe31de4512427af87b10",
    },
    # Historical bundled Quark cards that predate destination-directory listing.
    # These are exact Git blob IDs, so even a stale customized marker can be
    # corrected safely when the persistent file is byte-for-byte a known template.
    "quark.py": {
        "93f21c01135b97dbf772d95cdba4e7d785da96da",
        "9c2e605ae0cded760ba778f7e0020b7142226fcb",
        "8a53bc521ae0a32121ea9f41ebbd08ce75ab5e8f",
        "c3a8fb618e78110a6a0ddd7c94fdb3b266c375d3",
        "cdc731caeb8831f53df59467ae758ade36ed50e4",
        "93ea4dbaec79aeaaae7bd59531af6aebe0f17173",
        "bbedc5cec19b46be41e4f033396bc81e06c91334",
        "901eee606dbeeda391c0febb4c8dd45915057a90",
        "7987df724e95312d6a66258bedffd458cbd94df6",
    },
    "douban.py": {
        "4c601e750bfb312a3d7e028908568965f819d0f6",
        "d2cb3df5d76fdf7d35f24d05471d878796101899",
        "d768c1f3f9269549b1052bae46d37a98701992d4",
        "11928ad2e76f7b4ff224dcb0cca012b925e0780c",
    },
}

# Exact historical bundled file versions that should be retired on upgrade.
# Only these known built-in copies are removed; customized/user-installed cards stay intact.
_RETIRED_BUNDLED_CARD_BLOBS = {
    "aliyun.py": {"504fe978f795cd825544ffb2abefc49b2def9a55"},
    "quark_playback.py": {"d9211e0422e29b11e2d10a94f8a41ffd5a80a37e"},
}


def _git_blob_sha(content: bytes) -> str:
    """Return the Git blob object ID for exact-byte version comparisons."""
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


_PLUGIN_FILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\.py$")
_MAX_PLUGIN_BYTES = 256 * 1024


class CardFilePluginManager:
    """Manage one trusted card factory per Python file under the data directory."""

    def __init__(
        self,
        plugin_dir: Path,
        registry: CardRegistry,
        logger,
        bundled_dir: Path | None = None,
        *,
        seed_missing: bool = True,
    ):
        self.plugin_dir = Path(plugin_dir).resolve()
        self.registry = registry
        self.logger = logger
        self.bundled_dir = Path(bundled_dir).resolve() if bundled_dir else None
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        self._seed_bundled_cards(self.bundled_dir, seed_missing=seed_missing)
        self._lock = RLock()
        # Keep the exact instance so stale file bookkeeping can never remove a
        # different card that later registers under the same ID.
        self._loaded: dict[str, tuple[str, str, Card]] = {}
        self._errors: dict[str, str] = {}
        self._context: dict[str, Any] = {}

    def _seed_bundled_cards(
        self,
        bundled_dir: Path | None,
        *,
        seed_missing: bool = True,
    ) -> None:
        """Optionally seed new bundled cards and safely upgrade existing unmodified copies."""
        if bundled_dir is None or not bundled_dir.is_dir():
            return
        marker_dir = self.plugin_dir / ".seeded"
        marker_dir.mkdir(parents=True, exist_ok=True)
        for source in sorted(bundled_dir.glob("*.py")):
            try:
                filename = self.validate_filename(source.name)
            except ValueError:
                continue

            marker = marker_dir / f"{filename}.seeded"
            destination = self.plugin_dir / filename
            source_content = source.read_bytes()
            source_blob = _git_blob_sha(source_content)

            if not destination.exists():
                # A marker with no file means an administrator intentionally
                # uninstalled this card. Fresh installs also remain empty unless
                # the administrator explicitly opts into installing bundled cards.
                if marker.exists() or not seed_missing:
                    continue
                try:
                    destination.write_bytes(source_content)
                    marker.write_text(source_blob, encoding="ascii")
                except OSError:
                    self.logger.exception("初始化内置卡片文件 %s 失败", filename)
                continue

            try:
                destination_content = destination.read_bytes()
                destination_blob = _git_blob_sha(destination_content)
                marker_value = marker.read_text(encoding="ascii").strip() if marker.exists() else ""

                legacy_blobs = _LEGACY_BUNDLED_CARD_BLOBS.get(filename, set())
                if marker_value and marker_value == destination_blob:
                    # The file still matches the last bundled version: upgrade it.
                    if destination_blob != source_blob:
                        destination.write_bytes(source_content)
                        self.logger.info("已更新未修改的内置卡片 %s", filename)
                    marker.write_text(source_blob, encoding="ascii")
                elif destination_blob in legacy_blobs:
                    # Upgrade only exact known historical bundled bytes. A stale
                    # customized marker may have been assigned by an older release
                    # to an unrecognized template; the blob hash is stronger proof
                    # that this file is not administrator-edited.
                    if destination_blob != source_blob:
                        destination.write_bytes(source_content)
                        self.logger.info("已迁移旧版内置卡片 %s", filename)
                    marker.write_text(source_blob, encoding="ascii")
                elif not marker_value:
                    # Empty markers are from older releases. Current templates can
                    # be marked directly; unknown content is treated as customized.
                    if destination_blob == source_blob:
                        marker.write_text(source_blob, encoding="ascii")
                    else:
                        marker.write_text(f"customized:{destination_blob}", encoding="ascii")
                        self.logger.info("保留可能已自定义的内置卡片 %s，不自动覆盖", filename)
                # A changed destination, or a marker already marked customized,
                # is intentionally preserved to avoid losing administrator edits.
            except OSError:
                self.logger.exception("升级内置卡片文件 %s 失败", filename)

            self._migrate_legacy_builtin_card(filename, destination)

        # Remove obsolete bundled cards only when their persistent copy is known
        # to be an unmodified bundled version. Never delete unknown or customized
        # Python files from the administrator's data directory.
        bundled_filenames = {
            source.name
            for source in bundled_dir.glob("*.py")
            if _PLUGIN_FILE_RE.fullmatch(source.name)
        }
        retired_filenames = set(_RETIRED_BUNDLED_CARD_BLOBS)
        marked_filenames = {
            marker.name.removesuffix(".seeded")
            for marker in marker_dir.glob("*.py.seeded")
        }
        for filename in sorted(retired_filenames | marked_filenames):
            if filename in bundled_filenames:
                continue
            try:
                self.validate_filename(filename)
            except ValueError:
                continue
            destination = self.plugin_dir / filename
            marker = marker_dir / f"{filename}.seeded"
            try:
                marker_value = marker.read_text(encoding="ascii").strip() if marker.is_file() else ""
                if not destination.is_file():
                    marker.unlink(missing_ok=True)
                    continue
                destination_blob = _git_blob_sha(destination.read_bytes())
                known_retired_blob = destination_blob in _RETIRED_BUNDLED_CARD_BLOBS.get(filename, set())
                is_unmodified_bundled = marker_value == destination_blob or (
                    not marker_value and known_retired_blob
                )
                if not is_unmodified_bundled:
                    continue
                destination.unlink()
                marker.unlink(missing_ok=True)
                self.logger.info("已卸载已从内置模板移除且未修改的卡片 %s", filename)
            except OSError:
                self.logger.exception("清理已移除的内置卡片 %s 失败", filename)

    def _migrate_legacy_builtin_card(self, filename: str, destination: Path) -> None:
        """Apply narrowly-scoped upgrades without replacing administrator card files."""
        migrations = {
            "douban.py": (
                'return DoubanMetadataCard(context["douban"])',
                "return DoubanMetadataCard(DoubanClient())",
            ),
        }
        migration = migrations.get(filename)
        if migration is None or not destination.is_file():
            return
        legacy_factory, updated_factory = migration
        try:
            content = destination.read_text(encoding="utf-8")
            if legacy_factory not in content:
                return
            if "from moviesync.clients.douban import DoubanClient" not in content:
                return
            destination.write_text(
                content.replace(legacy_factory, updated_factory, 1),
                encoding="utf-8",
            )
        except OSError:
            self.logger.exception("迁移旧版内置卡片 %s 失败", filename)
            return
        self.logger.info("已迁移旧版内置卡片 %s 的工厂依赖", filename)

    @staticmethod
    def validate_filename(filename: str) -> str:
        name = str(filename or "").strip()
        if not _PLUGIN_FILE_RE.fullmatch(name):
            raise ValueError("卡片文件名仅允许小写字母、数字、连字符和下划线，且必须以 .py 结尾")
        return name

    def list_plugins(self) -> list[dict[str, Any]]:
        with self._lock:
            items = []
            for path in sorted(self.plugin_dir.glob("*.py")):
                try:
                    filename = self.validate_filename(path.name)
                except ValueError:
                    continue
                loaded = self._loaded.get(filename)
                items.append({
                    "filename": filename,
                    "loaded": bool(loaded and self.registry.get(loaded[0]) is loaded[2]),
                    "card_id": loaded[0] if loaded else None,
                    "error": self._errors.get(filename, ""),
                    "size": path.stat().st_size,
                })
            return items

    def load_all(self, context: dict[str, Any] | None = None) -> list[str]:
        # Keep the context and the full load pass consistent. Without this lock,
        # overlapping startup/reload calls can replace self._context between files,
        # producing a mixed set of cards initialized with different service objects.
        with self._lock:
            self._context = dict(context or {})
            loaded = []
            for item in self.list_plugins():
                # Repeated load_all calls are safe: do not turn already-loaded
                # cards into false error states by attempting duplicate registration.
                if item.get("loaded"):
                    continue
                try:
                    card = self.load_file(item["filename"], self._context)
                    loaded.append(card.card_id)
                except Exception as exc:
                    self.logger.exception("单文件卡片 %s 加载失败", item["filename"])
                    self._errors[item["filename"]] = str(exc)[:300]
            return loaded

    def _overlay_bundled_config_ui(self, card: Card, filename: str) -> None:
        """Reuse bundled form metadata for older customized copies of a built-in card.

        The implementation file is never replaced here. Only UI hints are overlaid,
        and only when a field key and type still match the bundled schema.
        """
        if self.bundled_dir is None:
            return
        bundled_path = self.bundled_dir / filename
        if not bundled_path.is_file():
            return

        module_name = "moviesync_bundled_card_schema_" + filename[:-3]
        spec = importlib.util.spec_from_file_location(module_name, bundled_path)
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
            canonical_manifest = None
            for candidate in vars(module).values():
                if not isinstance(candidate, type) or candidate.__module__ != module_name:
                    continue
                try:
                    if not issubclass(candidate, Card) or candidate is Card:
                        continue
                except TypeError:
                    continue
                manifest = getattr(candidate, "manifest", None)
                if getattr(manifest, "id", None) == card.card_id:
                    canonical_manifest = manifest
                    break
            if canonical_manifest is None:
                return

            canonical_fields = {
                str(field.get("key")): field
                for field in canonical_manifest.config_fields
                if isinstance(field, dict) and field.get("key")
            }
            merged_fields = []
            changed = False
            for declared in card.manifest.config_fields:
                field = dict(declared)
                canonical = canonical_fields.get(str(field.get("key") or ""))
                if canonical:
                    # 配置编辑器属于可迁移的表单定义；允许新版内置卡片更新旧版字段类型，
                    # 例如把旧 JSON 配置界面升级为文本框，而不覆盖用户实际保存的数据。
                    for key in ("type", "editor", "item_fields", "placeholder", "description", "label"):
                        if key in canonical and field.get(key) != canonical[key]:
                            field[key] = canonical[key]
                            changed = True
                merged_fields.append(field)

            # Filename normalization now belongs to its own optional card. Older
            # Telegram manifests may still expose this retired field; hide it from
            # the Telegram form without touching the user's saved config or code.
            if card.card_id == "telegram":
                filtered_fields = [
                    field for field in merged_fields
                    if str(field.get("key") or "") != "magic_regex"
                ]
                if len(filtered_fields) != len(merged_fields):
                    merged_fields = filtered_fields
                    changed = True

            if changed:
                card.manifest = replace(card.manifest, config_fields=tuple(merged_fields))
                self.logger.info(
                    "卡片 %s 使用新版内置配置表单定义；保留现有卡片代码",
                    card.card_id,
                )
        except Exception:
            self.logger.exception("更新卡片 %s 的配置表单定义失败", card.card_id)
        finally:
            sys.modules.pop(module_name, None)

    def _is_unmodified_bundled_file(self, filename: str, path: Path) -> bool:
        """仅对未修改的内置卡片隐藏旧版共享 Telegram 客户端入口。"""
        if self.bundled_dir is None:
            return False
        bundled = self.bundled_dir / filename
        try:
            return bundled.is_file() and path.read_bytes() == bundled.read_bytes()
        except OSError:
            return False

    def load_file(self, filename: str, context: dict[str, Any] | None = None) -> Card:
        name = self.validate_filename(filename)
        path = (self.plugin_dir / name).resolve()
        if path.parent != self.plugin_dir or not path.is_file():
            raise ValueError("卡片文件不存在")
        with self._lock:
            current = self._loaded.get(name)
            if current and self.registry.get(current[0]) is current[2]:
                raise ValueError("该卡片文件已经加载")
            if current:
                # The instance was removed/replaced outside this manager. Forget
                # stale bookkeeping, but never unregister the replacement.
                self._loaded.pop(name, None)
                sys.modules.pop(current[1], None)
            module_name = "moviesync_file_card_" + name[:-3]
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ValueError("无法读取卡片 Python 文件")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
                factory = getattr(module, "create_card", None)
                if not callable(factory):
                    raise ValueError("卡片文件必须提供 create_card(context) 工厂函数")
                factory_context = dict(context if context is not None else self._context)
                if self._is_unmodified_bundled_file(name, path):
                    # Built-in cards own their clients. Keep the legacy adapter
                    # available only to custom/edited files that may still use it.
                    factory_context.pop("telegram", None)
                card = factory(factory_context)
                self._validate_card(card, name)
                self._overlay_bundled_config_ui(card, name)
                self.registry.register(card)
            except Exception as exc:
                self._errors[name] = str(exc)[:300]
                sys.modules.pop(module_name, None)
                raise
            self._loaded[name] = (card.card_id, module_name, card)
            self._errors.pop(name, None)
            self.logger.info("已加载单文件卡片 %s（%s）", card.card_id, name)
            return card

    @staticmethod
    def _validate_card(card: object, filename: str) -> None:
        if not isinstance(card, Card):
            raise TypeError("create_card(context) 必须返回 MovieSync Card 实例")
        expected_id = filename[:-3]
        if card.card_id != expected_id:
            raise ValueError(
                f"卡片 ID「{card.card_id}」必须与文件名「{expected_id}」一致"
            )
        expected_types = {
            "resource_source": ResourceSourceCard,
            "storage_target": StorageTargetCard,
            "playback_provider": PlaybackProviderCard,
            "metadata_provider": MetadataProviderCard,
            "notification": NotificationCard,
            "filename_processor": FilenameProcessorCard,
        }
        base = expected_types.get(card.card_type)
        if base is not None and not isinstance(card, base):
            raise TypeError(f"{card.card_type} 类型卡片必须继承 {base.__name__}")
        if card.card_type not in {
            "resource_source", "storage_target", "playback_provider", "metadata_provider",
            "notification", "automation", "filename_processor", "custom",
        }:
            raise ValueError("卡片声明了不支持的类型")

    def install(self, filename: str, content: bytes) -> Card:
        name = self.validate_filename(filename)
        if not isinstance(content, bytes) or not content:
            raise ValueError("卡片文件不能为空")
        if len(content) > _MAX_PLUGIN_BYTES:
            raise ValueError("卡片文件不能超过 256 KiB")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("卡片文件必须使用 UTF-8 编码") from exc
        path = self.plugin_dir / name
        with self._lock:
            if path.exists():
                raise ValueError("同名卡片文件已存在")
            # Exclusive creation prevents overwriting a file during concurrent requests.
            with path.open("xb") as handle:
                handle.write(content)
            try:
                return self.load_file(name, self._context)
            except Exception:
                path.unlink(missing_ok=True)
                self._errors.pop(name, None)
                raise

    def uninstall(self, filename: str) -> dict[str, str]:
        name = self.validate_filename(filename)
        path = self.plugin_dir / name
        with self._lock:
            if not path.is_file():
                raise FileNotFoundError("卡片文件不存在")
            loaded = self._loaded.pop(name, None)
            card_id = loaded[0] if loaded else ""
            module_name = loaded[1] if loaded else ""
            loaded_card = loaded[2] if loaded else None
            if card_id and loaded_card is not None:
                # A stale plugin entry must not uninstall a different card that
                # happens to reuse this ID after the original was unloaded.
                if self.registry.get(card_id) is loaded_card:
                    self.registry.unregister(card_id)
            if module_name:
                sys.modules.pop(module_name, None)
            path.unlink()
            try:
                Path(importlib.util.cache_from_source(str(path))).unlink(missing_ok=True)
                cache_dir = self.plugin_dir / "__pycache__"
                if cache_dir.is_dir() and not any(cache_dir.iterdir()):
                    cache_dir.rmdir()
            except (OSError, NotImplementedError):
                self.logger.debug("清理卡片 %s 的字节码缓存失败", name, exc_info=True)
            self._errors.pop(name, None)
            self.logger.info("已卸载单文件卡片 %s（%s）", card_id or name, name)
            return {"filename": name, "card_id": card_id}
