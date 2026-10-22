"""豆瓣影视数据客户端。

用途：获取电影/电视剧/综艺/动漫列表和搜索结果，并把不同豆瓣接口的返回结构统一成前端需要的格式。
维护说明：电影列表包含多个上游接口兜底；当前还负责兼容海报字段结构变化，避免豆瓣接口小改动导致首页空白。
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote, urlsplit

from bs4 import BeautifulSoup, Tag

from .http import ApiError, HttpClient

DOUBAN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://movie.douban.com/explore",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


class DoubanClient:
    def __init__(self):
        self.http = HttpClient(DOUBAN_HEADERS)

    def close(self) -> None:
        self.http.close()

    def get_movies(self, tag: str, sort_type: str) -> list[dict]:
        tag = tag if tag in {"电影", "电视剧", "综艺", "动漫"} else "电影"
        sort_type = sort_type if sort_type in {"U", "T", "R"} else "U"

        if tag == "电影":
            items = self._get_movie_items(sort_type)
        else:
            items = self._get_tv_items(tag, sort_type)

        movies = []
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized = self._normalize_item(item)
            if normalized["title"]:
                movies.append(normalized)

        return movies

    def _get_movie_items(self, sort_type: str) -> list[dict]:
        # 优先使用移动端推荐接口。
        try:
            response, payload = self.http.request_json(
                "GET",
                "https://m.douban.com/rexxar/api/v2/subject/recent_hot/movie",
                timeout=10,
                retries=1,
                params={
                    "start": "0",
                    "limit": "100",
                    "category": "最新" if sort_type in {"T", "R"} else "热门",
                    "type": "全部",
                },
            )
            if response.status_code == 200:
                items = self._extract_items(payload)
                if items:
                    return items
        except ApiError:
            pass

        # 第二个移动端接口，响应结构和 recent_hot 不完全一致。
        try:
            response, payload = self.http.request_json(
                "GET",
                "https://m.douban.com/rexxar/api/v2/movie/recommend",
                timeout=10,
                retries=1,
                params={
                    "refresh": "0",
                    "start": "0",
                    "count": "100",
                    "selected_categories": json.dumps(
                        {"类型": ""},
                        ensure_ascii=False,
                    ),
                    "uncollect": "false",
                    "tags": "",
                },
            )
            if response.status_code == 200:
                items = self._extract_items(payload)
                if items:
                    return items
        except ApiError:
            pass

        # 最后使用网页版搜索接口。这个接口返回 subjects，字段更稳定，
        # 也直接提供海报 img，是首页无法显示时的重要兜底。
        try:
            response, payload = self.http.request_json(
                "GET",
                "https://movie.douban.com/j/search_subjects",
                timeout=10,
                retries=1,
                params={
                    "type": "movie",
                    "tag": "热门" if sort_type == "U" else "最新",
                    "sort": "recommend" if sort_type == "U" else "time",
                    "page_limit": "100",
                    "page_start": "0",
                },
            )
            if response.status_code == 200:
                return self._extract_items(payload)
        except ApiError:
            pass

        return []

    def _get_tv_items(self, tag: str, sort_type: str) -> list[dict]:
        cat_map = {
            "动漫": {"类型": "动画", "形式": "电视剧"},
            "综艺": {"类型": "", "形式": "综艺"},
            "电视剧": {"类型": "", "形式": "电视剧"},
        }

        try:
            response, payload = self.http.request_json(
                "GET",
                "https://m.douban.com/rexxar/api/v2/tv/recommend",
                timeout=10,
                retries=1,
                params={
                    "refresh": "0",
                    "start": "0",
                    "count": "100",
                    "selected_categories": json.dumps(
                        cat_map[tag],
                        ensure_ascii=False,
                    ),
                    "tags": tag,
                    "sort": "R" if sort_type in {"T", "R"} else "U",
                },
            )
            if response.status_code == 200:
                items = self._extract_items(payload)
                if items:
                    return items
        except ApiError:
            pass

        return []

    @staticmethod
    def _extract_items(payload: object) -> list[dict]:
        if isinstance(payload, list):
            return [
                item
                for item in payload
                if isinstance(item, dict)
            ]

        if not isinstance(payload, dict):
            return []

        for key in (
            "subjects",
            "items",
            "subject_collection_items",
            "recommend_items",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                return [
                    item
                    for item in value
                    if isinstance(item, dict)
                ]

        return []

    @staticmethod
    def _normalize_cover_url(value: object) -> str:
        """Normalize string-shaped legacy/protocol-relative Douban poster URLs."""
        if not isinstance(value, str):
            return ""
        cover = value.strip()
        if cover.startswith("//"):
            return f"https:{cover}"
        if cover.lower().startswith("http://"):
            parsed = urlsplit(cover)
            host = (parsed.hostname or "").lower()
            if (
                host == "doubanio.com"
                or host.endswith(".doubanio.com")
                or host == "douban.com"
                or host.endswith(".douban.com")
            ):
                return "https://" + cover[7:]
        return cover

    @staticmethod
    def _extract_cover_url(value: object, depth: int = 0) -> str:
        """Extract a usable poster URL from the varied nested shapes used by Douban APIs."""
        if depth > 5:
            return ""
        if isinstance(value, str):
            return DoubanClient._normalize_cover_url(value)
        if isinstance(value, dict):
            # API versions have returned strings, {url: ...}, and nested
            # {normal: {url: ...}} / {large: {url: ...}} poster structures.
            for key in (
                "url", "normal", "large", "src", "image_url", "image",
                "cover", "original", "medium", "small",
            ):
                candidate = DoubanClient._extract_cover_url(value.get(key), depth + 1)
                if candidate:
                    return candidate
        elif isinstance(value, (list, tuple)):
            for item in value:
                candidate = DoubanClient._extract_cover_url(item, depth + 1)
                if candidate:
                    return candidate
        return ""

    @staticmethod
    def _detail_subject_id(value: object) -> str:
        """Accept only a numeric subject ID or a canonical Douban subject URL."""
        raw = str(value or "").strip()
        if re.fullmatch(r"\d{1,16}", raw):
            return raw
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return ""
        if (parsed.hostname or "").lower() not in {"movie.douban.com", "m.douban.com"}:
            return ""
        match = re.fullmatch(r"/subject/(\d+)/?", parsed.path)
        return match.group(1) if match else ""

    @staticmethod
    def _schema_people(value: object) -> list[str]:
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list):
            return []
        names = []
        for person in value:
            name = person.get("name") if isinstance(person, dict) else person
            name = str(name or "").strip()
            if name and name not in names:
                names.append(name)
        return names

    @staticmethod
    def _schema_objects(value: object) -> list[dict]:
        if isinstance(value, dict):
            return [value]
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def get_detail(self, item_id: str) -> dict | None:
        """Fetch public subject details from the Douban subject page.

        The listing endpoints do not consistently include credits. The public
        subject HTML provides JSON-LD plus stable info/summary markup; no API key
        or third-party metadata service is required.
        """
        subject_id = self._detail_subject_id(item_id)
        if not subject_id:
            return None

        url = f"https://movie.douban.com/subject/{subject_id}/"
        try:
            response = self.http.session.get(url, timeout=10)
        except Exception as exc:
            raise ApiError(f"获取豆瓣影视详情失败：{exc}") from exc
        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, "html.parser")
        schema = {}
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                payload = json.loads(script.string or script.get_text())
            except (TypeError, ValueError):
                continue
            candidates = self._schema_objects(payload)
            if isinstance(payload, dict) and isinstance(payload.get("@graph"), list):
                candidates.extend(self._schema_objects(payload.get("@graph")))
            for candidate in candidates:
                kind = candidate.get("@type", "")
                kinds = kind if isinstance(kind, list) else [kind]
                if any(str(value).lower() in {"movie", "tvseries", "creativework"} for value in kinds):
                    schema = candidate
                    break
            if schema:
                break

        def schema_text(key: str) -> str:
            value = schema.get(key)
            if isinstance(value, dict):
                value = value.get("name") or value.get("value")
            if isinstance(value, list):
                value = "、".join(self._schema_people(value))
            return str(value or "").strip()

        def info_values(label: str) -> list[str]:
            target = None
            for element in soup.select("#info .pl"):
                current = element.get_text(" ", strip=True).rstrip(":：")
                if current == label:
                    target = element
                    break
            if target is None:
                return []
            values = []
            for sibling in target.next_siblings:
                if isinstance(sibling, Tag) and sibling.name == "span" and "pl" in (sibling.get("class") or []):
                    break
                if isinstance(sibling, Tag):
                    text = sibling.get_text(" ", strip=True)
                    if sibling.name == "br":
                        continue
                else:
                    text = str(sibling).strip()
                text = re.sub(r"\s+", " ", text).strip(" :：\n\t")
                if text and text not in values:
                    values.append(text)
            return values

        def people(label: str, schema_key: str) -> list[str]:
            values = info_values(label)
            names = []
            for value in values:
                for part in re.split(r"\s*/\s*|、|，", value):
                    part = part.strip()
                    if part and part not in names:
                        names.append(part)
            return names or self._schema_people(schema.get(schema_key))

        title_node = soup.select_one('#content h1 span[property="v:itemreviewed"]') or soup.select_one("#content h1 span")
        title = (title_node.get_text(" ", strip=True) if title_node else "") or schema_text("name")
        title = re.sub(r"\s*\(\d{4}\)\s*$", "", title).strip()
        cover_node = soup.select_one("#mainpic img") or soup.select_one('meta[property="og:image"]')
        cover = ""
        if cover_node:
            cover = cover_node.get("src") or cover_node.get("data-src") or cover_node.get("content") or ""
        cover = self._normalize_cover_url(cover or schema_text("image"))

        rating_node = soup.select_one('strong[property="v:average"]') or soup.select_one('[itemprop="ratingValue"]')
        rating = (rating_node.get_text(" ", strip=True) if rating_node else "") or schema_text("ratingValue")
        summary_node = soup.select_one('span[property="v:summary"]') or soup.select_one("#link-report .all") or soup.select_one("#link-report")
        summary = summary_node.get_text(" ", strip=True) if summary_node else ""
        summary = re.sub(r"\s+", " ", summary).strip() or schema_text("description")

        genres = []
        for node in soup.select('span[property="v:genre"]'):
            value = node.get_text(" ", strip=True)
            if value and value not in genres:
                genres.append(value)
        if not genres:
            raw_genres = schema.get("genre", [])
            genres = [str(value).strip() for value in (raw_genres if isinstance(raw_genres, list) else [raw_genres]) if str(value).strip()]

        release_dates = [node.get_text(" ", strip=True) for node in soup.select('span[property="v:initialReleaseDate"]') if node.get_text(" ", strip=True)]
        year_match = re.search(r"\b(19|20)\d{2}\b", " ".join(release_dates) or schema_text("datePublished"))
        year = year_match.group(0) if year_match else ""
        info_year = info_values("上映日期")
        if not year and info_year:
            match = re.search(r"\b(19|20)\d{2}\b", " ".join(info_year))
            year = match.group(0) if match else ""

        directors = people("导演", "director")
        writers = people("编剧", "creator")
        casts = people("主演", "actor")
        countries = info_values("制片国家/地区")
        runtime = info_values("片长")
        original_title = info_values("又名")

        return {
            "id": subject_id,
            "title": title,
            "cover": cover,
            "rate": str(rating or "").strip() or "暂无",
            "year": year,
            "summary": summary,
            "genres": genres,
            "directors": directors,
            "writers": writers,
            "casts": casts,
            "countries": countries,
            "release_dates": release_dates or info_year,
            "runtime": runtime,
            "original_title": original_title,
            "url": url,
        }

    def search(self, query: str) -> list[dict]:
        query = str(query or "").strip()
        if not query:
            return []

        response, payload = self.http.request_json(
            "GET",
            f"https://movie.douban.com/j/subject_suggest?q={quote(query)}",
            timeout=8,
            retries=1,
            allow_non_dict=True,
        )

        if response.status_code != 200 or not isinstance(payload, list):
            raise ApiError(
                f"豆瓣搜索失败（HTTP {response.status_code}）"
            )

        return [
            {
                "id": str(item.get("id")),
                "title": item.get("title"),
                "cover": self._extract_cover_url(item.get("img", "")),
                "rate": item.get("rate") or item.get("rating") or "暂无",
                "year": item.get("year", ""),
                "url": (
                    f"https://movie.douban.com/subject/{item.get('id')}/"
                ),
            }
            for item in payload
            if item.get("title") and item.get("id")
        ]

    @staticmethod
    def _normalize_item(item: dict) -> dict:
        target = item.get("target")
        if not isinstance(target, dict):
            target = {}

        subject_id = (
            item.get("id")
            or target.get("id")
            or item.get("subject_id")
        )

        title = (
            item.get("title")
            or target.get("title")
            or item.get("name")
            or target.get("name")
            or ""
        )

        rating_data = item.get("rating")
        if not isinstance(rating_data, dict):
            rating_data = {}

        target_rating = target.get("rating")
        if not isinstance(target_rating, dict):
            target_rating = {}

        rating = (
            rating_data.get("value")
            or target_rating.get("value")
            or item.get("rate")
            or target.get("rate")
            or "暂无"
        )

        pic_value = item.get("pic")
        pic = pic_value if isinstance(pic_value, dict) else {}

        target_pic_value = target.get("pic")
        target_pic = target_pic_value if isinstance(target_pic_value, dict) else {}

        cover_value = item.get("cover")
        cover_data = cover_value if isinstance(cover_value, dict) else {}

        target_cover_value = target.get("cover")
        target_cover = target_cover_value if isinstance(target_cover_value, dict) else {}

        cover_candidates = (
            pic.get("normal"),
            pic.get("large"),
            pic.get("url"),
            target_pic.get("normal"),
            target_pic.get("large"),
            target_pic.get("url"),
            cover_data,
            cover_value,
            target_cover,
            target_cover_value,
            pic_value,
            target_pic_value,
            item.get("cover_url"),
            target.get("cover_url"),
            item.get("img"),
            target.get("img"),
        )
        cover = next(
            (
                candidate
                for value in cover_candidates
                if (candidate := DoubanClient._extract_cover_url(value))
            ),
            "",
        )

        summary = (
            item.get("summary")
            or target.get("summary")
            or item.get("description")
            or target.get("description")
            or ""
        )
        year = item.get("year") or target.get("year") or ""
        genres = item.get("genres") or target.get("genres") or []
        if not isinstance(genres, list):
            genres = []

        subject_id = str(subject_id or "")
        return {
            "id": subject_id,
            "title": title,
            "cover": DoubanClient._normalize_cover_url(cover),
            "rate": str(rating),
            "summary": str(summary).strip(),
            "year": str(year).strip(),
            "genres": [str(value).strip() for value in genres if value],
            "url": (
                f"https://movie.douban.com/subject/{subject_id}/"
                if subject_id
                else "https://movie.douban.com/"
            ),
        }
