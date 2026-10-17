"""夸克网盘 API 客户端。

用途：校验 Cookie、解析分享文件、创建分类/影视专属目录，以及执行白名单文件转存。
维护说明：分享解析限制递归深度和文件数量；转存只接收经过服务端重新解析并校验过的文件 FID。
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from .http import ApiError, HttpClient

QUARK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://pan.quark.cn/",
    "Origin": "https://pan.quark.cn",
    "Content-Type": "application/json",
}
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".m4v", ".ts", ".m2ts", ".iso")


class ShareFileList(list):
    """List-compatible result that carries share-scan completeness diagnostics."""

    def __init__(self):
        super().__init__()
        self.scan_complete = True
        self.scan_warnings: list[str] = []


def sanitize_pwd_id(pwd_id: object) -> str:
    if not pwd_id:
        return ""
    value = str(pwd_id).strip()
    match = re.search(r"quark\.cn/s/([A-Za-z0-9]+)", value, re.IGNORECASE)
    if match:
        return match.group(1)
    if "://" in value or "/" in value or "?" in value:
        return ""
    return value if re.fullmatch(r"[A-Za-z0-9]{1,128}", value) else ""


def clean_tv_filename(raw_name: str, title: str = "") -> tuple[int | None, str]:
    """兼容旧调用方；集数判断统一委托给共享文件名规则。"""
    if not raw_name:
        return None, raw_name

    # 延迟导入以避免 clients.quark 与卡片注册初始化期间形成循环导入。
    from ..services.filename_rules import parse_tv_episode

    _season, episode = parse_tv_episode(raw_name)
    return episode, raw_name


class QuarkClient:
    def __init__(self, cookie: str):
        cookie = "".join((cookie or "").splitlines()).strip()
        if cookie.lower().startswith("cookie:"):
            cookie = cookie[7:].strip()
        self.cookie = cookie
        headers = {**QUARK_HEADERS, "Cookie": cookie}
        self.http = HttpClient(headers)

    def _request_json(self, *args, **kwargs):
        """Make refreshed Quark session cookies available to subsequent API calls."""
        response, data = self.http.request_json(*args, **kwargs)
        response_cookies = getattr(response, "cookies", None)
        get_dict = getattr(response_cookies, "get_dict", None)
        if callable(get_dict):
            refreshed = get_dict()
            if isinstance(refreshed, dict):
                updates = {key: str(refreshed[key]) for key in ("__pus", "__puus") if key in refreshed}
                if updates:
                    pairs = {}
                    for part in self.cookie.split(";"):
                        name, sep, value = part.strip().partition("=")
                        if sep and name:
                            pairs[name] = value
                    pairs.update(updates)
                    self.cookie = "; ".join(f"{name}={value}" for name, value in pairs.items())
                    self.http.headers["Cookie"] = self.cookie
                    session = getattr(getattr(self.http, "_local", None), "session", None)
                    if session is not None:
                        session.headers["Cookie"] = self.cookie
        return response, data

    def get_cookie(self) -> str:
        """Return the current Cookie header, including refreshed Quark session values."""
        return self.cookie

    def check_cookie_valid(self) -> bool:
        if not self.cookie:
            return False
        try:
            response, data = self._request_json(
                "GET",
                "https://drive.quark.cn/1/clouddrive/file/sort?pr=ucpro&fr=pc&pdir_fid=0&num=1",
                timeout=6,
                retries=1,
            )
            return response.status_code == 200 and data.get("code") == 0
        except ApiError:
            return False

    def _list_children(self, parent_fid: str) -> list[dict[str, Any]]:
        """Read a complete child listing or fail; partial results must not trigger duplicate folder creation."""
        items: list[dict[str, Any]] = []
        page = 1
        while page <= 20:
            url = (
                "https://drive.quark.cn/1/clouddrive/file/sort"
                f"?pr=ucpro&fr=pc&pdir_fid={quote(str(parent_fid))}&_page={page}&_size=200&_fetch_total=1"
            )
            try:
                response, data = self._request_json("GET", url, timeout=6, retries=1)
            except ApiError as exc:
                raise RuntimeError(f"读取夸克网盘目录失败：{exc}") from exc
            if response.status_code != 200 or not isinstance(data, dict) or data.get("code") != 0:
                message = data.get("message") if isinstance(data, dict) else None
                raise RuntimeError(str(message or f"读取夸克网盘目录失败（HTTP {response.status_code}）"))
            payload = data.get("data")
            batch = payload.get("list") if isinstance(payload, dict) else None
            if not isinstance(batch, list):
                raise RuntimeError("夸克网盘返回的目录格式无效")
            items.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < 200:
                break
            if page >= 20:
                raise RuntimeError("目录超过分页扫描上限，无法确认完整列表；为避免重复创建目录，请稍后重试")
            page += 1
        return items

    def get_or_create_subfolder(self, folder_name: str, parent_fid: str = "0") -> tuple[str | None, str | None]:
        parent_fid = str(parent_fid or "0").strip()
        folder_name = str(folder_name or "").strip()
        if not folder_name:
            return None, "文件夹名称不能为空"

        for item in self._list_children(parent_fid):
            is_dir = item.get("dir_file") is True or item.get("file_type") == 0
            if is_dir and item.get("file_name") == folder_name and item.get("fid"):
                return str(item["fid"]), None

        url = "https://drive-pc.quark.cn/1/clouddrive/file?pr=ucpro&fr=pc"
        payload = {"pdir_fid": parent_fid, "file_name": folder_name, "dir_init_lock": False, "dir_path": ""}
        error_message = "创建目录失败"
        try:
            response, data = self._request_json("POST", url, timeout=8, retries=0, json=payload)
            if response.status_code == 200 and data.get("code") == 0:
                obj = data.get("data") or {}
                fid = obj.get("fid") or obj.get("file_id") or data.get("fid")
                if fid:
                    return str(fid), None
            error_message = str(data.get("message") or f"HTTP {response.status_code}")
        except ApiError as exc:
            error_message = str(exc)

        for item in self._list_children(parent_fid):
            is_dir = item.get("dir_file") is True or item.get("file_type") == 0
            if is_dir and item.get("file_name") == folder_name and item.get("fid"):
                return str(item["fid"]), None
        return None, error_message

    def list_drive_files(self, parent_fid: object = "0") -> list[dict[str, Any]]:
        """严格读取本人夸克网盘目录；接口失败时抛错，避免把读取失败误报为空目录。"""
        parent = str(parent_fid or "0").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", parent):
            raise ValueError("目录 FID 格式无效")
        items: list[dict[str, Any]] = []
        page = 1
        while page <= 20:
            url = (
                "https://drive.quark.cn/1/clouddrive/file/sort"
                f"?pr=ucpro&fr=pc&pdir_fid={quote(parent)}&_page={page}&_size=200&_fetch_total=1"
            )
            response, data = self._request_json("GET", url, timeout=6, retries=1)
            if response.status_code != 200 or data.get("code") != 0:
                raise RuntimeError("夸克网盘目录读取失败，请检查 Cookie 是否有效")
            payload = data.get("data")
            batch = payload.get("list") if isinstance(payload, dict) else None
            if not isinstance(batch, list):
                raise RuntimeError("夸克网盘返回的目录格式无效")
            items.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < 200:
                break
            if page >= 20:
                raise RuntimeError("夸克网盘目录超过分页扫描上限，列表可能不完整；请缩小目录范围后重试")
            page += 1

        normalized = []
        for item in items:
            fid = str(item.get("fid") or "").strip()
            name = str(item.get("file_name") or "").strip()
            if not fid or not name:
                continue
            is_dir = item.get("dir_file") is True or item.get("file_type") == 0
            is_video = item.get("category") == 1 or name.lower().endswith(VIDEO_EXTENSIONS)
            normalized.append({
                "fid": fid, "file_name": name, "is_dir": bool(is_dir),
                "is_video": bool(not is_dir and is_video),
                "size": item.get("size") or 0,
                "updated_at": item.get("updated_at") or item.get("l_updated_at") or 0,
            })
        return normalized

    def get_share_files(
        self,
        pwd_id: object,
        max_depth: int = 3,
        max_files: int = 5000,
        passcode: str = "",
    ) -> tuple[
        list[dict[str, Any]] | None,
        str | None,
        str | None,
    ]:
        pwd_id = sanitize_pwd_id(pwd_id)
        if not pwd_id:
            return None, None, "分享链接 ID 无效"

        token_url = "https://drive.quark.cn/1/clouddrive/share/sharepage/token"
        try:
            response, data = self._request_json(
                "POST",
                token_url,
                timeout=8,
                retries=1,
                json={"pwd_id": pwd_id, "passcode": str(passcode or "").strip()},
            )
            if response.status_code == 404:
                return None, None, "分享链接已失效或已被删除"
            if response.status_code != 200 or data.get("code") != 0:
                return None, None, str(data.get("message") or f"获取 Token 失败（HTTP {response.status_code}）")
            stoken = (data.get("data") or {}).get("stoken")
        except ApiError as exc:
            return None, None, f"请求 Token 失败: {exc}"

        if not stoken:
            return None, None, "上游未返回 stoken"

        all_files = ShareFileList()
        visited: set[str] = set()
        walk_error: str | None = None

        def add_scan_warning(message: str) -> None:
            if message not in all_files.scan_warnings:
                all_files.scan_warnings.append(message)
            all_files.scan_complete = False

        def walk(parent_fid: str, depth: int, relative_path: str = "") -> None:
            nonlocal walk_error

            if walk_error or parent_fid in visited:
                return
            if depth > max_depth:
                add_scan_warning(f"目录嵌套超过扫描深度上限（{max_depth} 层），部分子目录可能未读取")
                return
            if len(all_files) >= max_files:
                add_scan_warning(f"已达到分享文件扫描上限（{max_files} 个），列表可能不完整")
                return

            visited.add(parent_fid)
            page = 1
            page_size = 50
            seen_children: set[str] = set()
            reported_total: int | None = None

            while (
                page <= 100
                and len(all_files) < max_files
                and not walk_error
            ):
                url = (
                    "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/detail"
                    f"?pr=ucpro&fr=pc&uc_param_str=&pwd_id={quote(pwd_id)}"
                    f"&stoken={quote(stoken, safe='')}"
                    f"&pdir_fid={quote(str(parent_fid))}&force=0"
                    f"&_page={page}&_size={page_size}&_fetch_total=1"
                    "&_sort=file_type:asc,file_name:asc"
                )

                try:
                    response, payload = self._request_json(
                        "GET",
                        url,
                        timeout=8,
                        retries=1,
                    )
                except ApiError as exc:
                    walk_error = f"读取分享文件列表失败: {exc}"
                    return

                if response.status_code != 200 or not isinstance(payload, dict) or payload.get("code") != 0:
                    walk_error = str(
                        payload.get("message")
                        or f"读取分享文件列表失败（HTTP {response.status_code}）"
                    )
                    return

                data_payload = payload.get("data")
                if not isinstance(data_payload, dict):
                    walk_error = "夸克分享目录返回格式无效"
                    return
                batch = data_payload.get("list")
                if not isinstance(batch, list):
                    walk_error = "夸克分享目录文件列表格式无效"
                    return

                metadata = payload.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = data_payload.get("metadata")
                if isinstance(metadata, dict):
                    raw_total = metadata.get("_total", metadata.get("total"))
                    try:
                        if raw_total is not None:
                            reported_total = max(0, int(raw_total))
                    except (TypeError, ValueError):
                        reported_total = None
                if reported_total is None:
                    try:
                        raw_total = data_payload.get("total")
                        if raw_total is not None:
                            reported_total = max(0, int(raw_total))
                    except (TypeError, ValueError):
                        reported_total = None

                if not batch:
                    if reported_total is not None and len(seen_children) < reported_total:
                        add_scan_warning(
                            f"目录分页提前结束：接口报告 {reported_total} 项，实际只读取到 {len(seen_children)} 项"
                        )
                    break

                new_items = []
                for item in batch:
                    if not isinstance(item, dict):
                        continue
                    child_key = str(item.get("fid") or item.get("file_name") or "").strip()
                    if child_key and child_key in seen_children:
                        continue
                    if child_key:
                        seen_children.add(child_key)
                    new_items.append(item)

                if not new_items:
                    add_scan_warning(
                        "目录分页没有读取到新文件，可能是上游忽略了分页参数；已停止重复读取"
                    )
                    break

                for item in new_items:
                    if (
                        item.get("dir_file") is True
                        or item.get("file_type") == 0
                    ):
                        fid = item.get("fid")
                        if fid:
                            child_name = str(item.get("file_name") or "").strip()
                            child_path = "/".join(part for part in (relative_path, child_name) if part)
                            walk(str(fid), depth + 1, child_path)
                    else:
                        normalized_item = dict(item)
                        normalized_item["relative_path"] = relative_path
                        all_files.append(normalized_item)
                        if len(all_files) >= max_files:
                            add_scan_warning(f"已达到分享文件扫描上限（{max_files} 个），列表可能不完整")
                            return

                # Quark may return fewer rows than requested while more rows remain.
                # Trust the reported total instead of treating a short page as EOF.
                if reported_total is not None and len(seen_children) >= reported_total:
                    break
                # Without total metadata, keep requesting pages until an empty or repeated page.
                # A short page is not proof that the share directory is complete.
                if page >= 100:
                    add_scan_warning("单个目录超过分页扫描上限，可能还有未读取的文件")
                    break

                page += 1

        walk("0", 0)

        if walk_error:
            return None, None, walk_error

        return all_files, str(stoken), None

    def save_files(
        self,
        pwd_id: object,
        files_to_save: list[dict[str, Any]],
        stoken: str | None,
        target_fid: object = "0",
    ) -> tuple[bool, str]:
        pwd_id = sanitize_pwd_id(pwd_id)
        if not pwd_id:
            return False, "分享链接 ID 无效"
        if not stoken:
            return False, "缺少分享 Token"
        fid_list: list[str] = []
        seen: set[str] = set()

        for item in (files_to_save or [])[:200]:
            if not isinstance(item, dict):
                continue

            fid = str(item.get("fid") or "").strip()

            if (
                fid
                and len(fid) <= 128
                and re.fullmatch(r"[A-Za-z0-9_-]+", fid)
                and fid not in seen
            ):
                seen.add(fid)
                fid_list.append(fid)

        if not fid_list:
            return False, "没有可转存的有效文件"

        target_fid = str(target_fid or "0").strip()
        if (
            len(target_fid) > 128
            or not re.fullmatch(r"[A-Za-z0-9_-]+", target_fid)
        ):
            return False, "目标目录 FID 格式无效"

        url = "https://drive.quark.cn/1/clouddrive/share/sharepage/save?pr=ucpro&fr=pc"
        payload = {
            "pwd_id": pwd_id,
            "stoken": stoken,
            "fid_list": fid_list,
            "to_pdir_fid": target_fid,
        }
        try:
            response, data = self._request_json("POST", url, timeout=10, retries=0, json=payload)
            if response.status_code == 200 and data.get("code") == 0:
                return True, "转存成功"
            return False, str(data.get("message") or f"转存失败（HTTP {response.status_code}）")
        except ApiError as exc:
            return False, (
                "转存结果不确定：请求可能已到达网盘，请检查目标目录后再决定是否重试。"
                f"详情：{exc}"
            )
