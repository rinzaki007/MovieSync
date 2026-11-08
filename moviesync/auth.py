"""MovieSync 管理员认证与账号安全存储。

用途：管理员初始化、登录密码校验、用户名/密码修改，以及旧版 auth.json 迁移。
维护说明：密码只保存 Werkzeug 哈希；修改账号信息后由 Web 层清理当前会话，要求重新登录。
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path
from threading import Lock

from werkzeug.security import check_password_hash, generate_password_hash

from .storage import JsonStore

MIN_USERNAME_LENGTH = 2
MAX_USERNAME_LENGTH = 64

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


class AuthStore:
    def __init__(
        self,
        auth_file: Path,
        legacy_file: Path | None = None,
    ):
        self.store = JsonStore(
            auth_file,
            lambda: {"initialized": False},
        )

        self.legacy_file = legacy_file
        self.lock = Lock()

        self._migrate_legacy()

    def _migrate_legacy(self) -> None:
        if (
            self.store.path.exists()
            or not self.legacy_file
            or not self.legacy_file.exists()
        ):
            return

        try:
            import json

            data = json.loads(
                self.legacy_file.read_text(
                    encoding="utf-8"
                )
            )

            if (
                isinstance(data, dict)
                and data.get("initialized")
                and data.get("password_hash")
            ):
                self.store.write(data)

        except (OSError, ValueError):
            pass

    @staticmethod
    def _validate_username(
        username: str,
    ) -> str:
        username = str(
            username or ""
        ).strip()

        if not MIN_USERNAME_LENGTH <= len(username) <= MAX_USERNAME_LENGTH:
            raise ValueError(
                f"管理员账号长度必须在 "
                f"{MIN_USERNAME_LENGTH}-{MAX_USERNAME_LENGTH} "
                f"个字符之间"
            )

        return username

    @staticmethod
    def _validate_password(
        password: str,
    ) -> str:
        password = str(
            password or ""
        )

        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(
                f"管理员密码至少需要 "
                f"{MIN_PASSWORD_LENGTH} 个字符"
            )

        if len(password) > MAX_PASSWORD_LENGTH:
            raise ValueError(
                f"管理员密码不能超过 "
                f"{MAX_PASSWORD_LENGTH} 个字符"
            )

        return password

    def is_initialized(self) -> bool:
        data = self.store.read()

        return bool(
            isinstance(data, dict)
            and data.get("initialized")
        )

    @property
    def setup_token_path(self) -> Path:
        return self.store.path.with_name("setup.token")

    def get_or_create_setup_token(self) -> tuple[str, bool]:
        """Return the persistent first-run token and whether it was just created."""
        path = self.setup_token_path
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            token = path.read_text(encoding="utf-8").strip()
            if not token:
                raise OSError("首次初始化令牌文件为空") from exc
            try:
                path.chmod(0o600)
            except OSError:
                pass
            return token, False
        token = secrets.token_urlsafe(32)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
        return token, True

    def clear_setup_token(self) -> None:
        self.setup_token_path.unlink(missing_ok=True)

    def setup(
        self,
        username: str,
        password: str,
    ) -> None:
        username = self._validate_username(username)
        password = self._validate_password(password)

        with self.lock:
            if self.is_initialized():
                raise ValueError(
                    "系统已完成初始化"
                )

            self.store.write(
                {
                    "initialized": True,
                    "username": username,
                    "password_hash": generate_password_hash(
                        password
                    ),
                }
            )

    def verify(
        self,
        username: str,
        password: str,
    ) -> bool:
        username = str(
            username or ""
        ).strip()

        password = str(
            password or ""
        )

        data = self.store.read()

        if (
            not isinstance(data, dict)
            or not data.get("initialized")
        ):
            return False

        stored_username = str(
            data.get("username") or ""
        )

        password_hash = str(
            data.get("password_hash") or ""
        )

        if (
            username != stored_username
            or not password_hash
        ):
            return False

        try:
            return check_password_hash(
                password_hash,
                password,
            )
        except (ValueError, TypeError):
            return False

    def change_username(
        self,
        current_username: str,
        current_password: str,
        new_username: str,
    ) -> None:
        current_username = self._validate_username(
            current_username
        )

        current_password = str(
            current_password or ""
        )

        new_username = self._validate_username(
            new_username
        )

        with self.lock:
            if not self.verify(
                current_username,
                current_password,
            ):
                raise ValueError(
                    "当前用户名或密码不正确"
                )

            data = self.store.read()

            if (
                not isinstance(data, dict)
                or not data.get("initialized")
            ):
                raise ValueError(
                    "系统尚未完成初始化"
                )

            self.store.write(
                {
                    **data,
                    "username": new_username,
                }
            )

    def change_password(
        self,
        username: str,
        current_password: str,
        new_password: str,
    ) -> None:
        username = self._validate_username(
            username
        )

        current_password = str(
            current_password or ""
        )

        new_password = self._validate_password(
            new_password
        )

        with self.lock:
            if not self.verify(
                username,
                current_password,
            ):
                raise ValueError(
                    "当前密码不正确"
                )

            data = self.store.read()

            if (
                not isinstance(data, dict)
                or not data.get("initialized")
            ):
                raise ValueError(
                    "系统尚未完成初始化"
                )

            self.store.write(
                {
                    **data,
                    "password_hash": generate_password_hash(
                        new_password
                    ),
                }
            )
