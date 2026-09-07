"""Pure, source-redacted parsing primitives for public X Articles."""

from __future__ import annotations

import base64
import hashlib
import html as html_module
import io
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from time import monotonic
from urllib.parse import urlsplit, urlunsplit

import httpx
from PIL import Image, UnidentifiedImageError


_URL_PATH_RE = re.compile(r"/([A-Za-z0-9_]{1,15})/status/([0-9]+)")
_BLOCK_RE = re.compile(
    r'content_state:blocks:(\d+)"[^\n]*?__typename:"DraftJsBlock",'
    r'key:"[^"]*",text:"((?:[^"\\]|\\.)*)",type:"([^"]+)"'
)
_ENTITY_RANGE_RE = re.compile(
    r'content_state:blocks:(\d+):entity_ranges:(\d+)"[^\n]*?'
    r'__typename:"DraftJsEntityRange",key:(\d+),length:\d+,offset:\d+'
)
_ENTITY_MAP_RE = re.compile(
    r'content_state:entity_map:(\d+)"[^\n]*?__typename:"DraftJsEntityMap",'
    r'key:"([^"]*)",value:'
)
_ENTITY_TYPE_RE = re.compile(
    r'content_state:entity_map:(\d+):value"[^\n]*?'
    r'__typename:"DraftJsEntity",type:"([^"]+)"'
)
_ENTITY_MARKDOWN_RE = re.compile(
    r'content_state:entity_map:(\d+):value:data"[^\n]*?'
    r'__typename:"DraftJsEntityData",caption:[^,]*,markdown:'
    r'("(?:[^"\\]|\\.)*"|null)'
)
_ENTITY_MEDIA_RE = re.compile(
    r'content_state:entity_map:(\d+):value:data:media_items:\d+"[^\n]*?'
    r'__typename:"ArticleMediaKey",media_id:"([^"]+)"'
)
_ENTITY_TWEET_RE = re.compile(
    r'content_state:entity_map:(\d+):value:data"[^}]*?'
    r'__typename:"DraftJsEntityData",[^}]*?tweet_id:"([0-9]+)"'
)
_API_MEDIA_RE = re.compile(
    r'original_img_url:"(https://pbs\.twimg\.com/media/[^"<]+)"'
)
_API_ID_RE = re.compile(r'"(QXBpTWVkaWE6[^"\s]+)"')
PAGE_MAX_BYTES = 20 * 1024 * 1024
IMAGE_MAX_BYTES = 15 * 1024 * 1024
TOTAL_READ_TIMEOUT_SECONDS = 30.0
_IMAGE_MIMES = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/webp": "webp",
}


class XArticleProviderError(ValueError):
    """A fixed-message error that never renders source material."""

    def __repr__(self) -> str:
        return "XArticleProviderError(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class XArticleAssetRef:
    key: str
    remote_url: str = field(repr=False)

    def __repr__(self) -> str:
        return "XArticleAssetRef(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class XArticleBlock:
    type: str
    text: str | None = None
    level: int | None = None
    language: str | None = None
    asset_key: str | None = None

    def __repr__(self) -> str:
        return "XArticleBlock(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class XArticleDocument:
    title: str
    author_handle: str
    blocks: tuple[XArticleBlock, ...]
    assets: tuple[XArticleAssetRef, ...]

    def __repr__(self) -> str:
        return "XArticleDocument(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class XArticleImage:
    payload: bytes = field(repr=False)
    format: str
    width: int
    height: int

    def __repr__(self) -> str:
        return "XArticleImage(<redacted>)"


def _read_response_bounded(response: httpx.Response, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    deadline = monotonic() + TOTAL_READ_TIMEOUT_SECONDS
    for chunk in response.iter_bytes():
        if monotonic() > deadline:
            raise ValueError
        total += len(chunk)
        if total > limit:
            raise ValueError
        chunks.append(chunk)
        if monotonic() > deadline:
            raise ValueError
    payload = b"".join(chunks)
    if not payload:
        raise ValueError
    return payload


class XArticleTransport:
    """Caller-injected, one-request-at-a-time X Article HTTP boundary."""

    def __init__(self, client: httpx.Client) -> None:
        if not isinstance(client, httpx.Client):
            raise XArticleProviderError("x-article-response-invalid")
        self._client = client

    def fetch_page(self, url: str) -> bytes:
        canonical = normalize_x_article_url(url)
        try:
            with self._client.stream(
                "GET",
                canonical,
                headers={
                    "accept": "text/html,application/xhtml+xml",
                    "user-agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"
                    ),
                },
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise XArticleProviderError("x-article-page-unavailable")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type not in {"text/html", "application/xhtml+xml"}:
                    raise ValueError
                return _read_response_bounded(response, PAGE_MAX_BYTES)
        except XArticleProviderError:
            raise
        except (httpx.HTTPError, OSError):
            raise XArticleProviderError("x-article-page-unavailable") from None
        except (TypeError, ValueError):
            raise XArticleProviderError("x-article-response-invalid") from None

    def fetch_image(self, url: str) -> XArticleImage:
        try:
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.hostname != "pbs.twimg.com"
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port is not None
                or not parsed.path.startswith("/media/")
                or parsed.fragment
            ):
                raise ValueError
            with self._client.stream(
                "GET",
                url,
                headers={"accept": "image/jpeg,image/png,image/webp"},
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise ValueError
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                expected = _IMAGE_MIMES.get(content_type)
                if expected is None:
                    raise ValueError
                payload = _read_response_bounded(response, IMAGE_MAX_BYTES)
            with Image.open(io.BytesIO(payload)) as decoded:
                detected = (decoded.format or "").lower()
                if detected == "jpg":
                    detected = "jpeg"
                width, height = decoded.size
                decoded.verify()
            with Image.open(io.BytesIO(payload)) as decoded:
                decoded.load()
            if detected != expected or width <= 0 or height <= 0:
                raise ValueError
            return XArticleImage(
                payload=payload,
                format=detected,
                width=width,
                height=height,
            )
        except (httpx.HTTPError, OSError, TypeError, ValueError, UnidentifiedImageError):
            raise XArticleProviderError("x-article-image-invalid") from None


def normalize_x_article_url(value: str) -> str:
    """Validate and canonicalize one public X status URL without network I/O."""

    try:
        if not isinstance(value, str) or value != value.strip() or "\n" in value or "\r" in value:
            raise ValueError
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"x.com", "www.x.com"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.query
            or parsed.fragment
            or _URL_PATH_RE.fullmatch(parsed.path) is None
            or parsed.hostname.encode("ascii").decode("ascii") != parsed.hostname
        ):
            raise ValueError
        match = _URL_PATH_RE.fullmatch(parsed.path)
        assert match is not None
        return urlunsplit(("https", "x.com", parsed.path, "", ""))
    except (AssertionError, UnicodeError, ValueError):
        raise XArticleProviderError("x-article-input-invalid") from None


def _decode_json_string(raw: str) -> str:
    try:
        value = json.loads(f'"{raw}"')
    except (json.JSONDecodeError, TypeError):
        raise XArticleProviderError("x-article-structure-unsupported") from None
    if not isinstance(value, str):
        raise XArticleProviderError("x-article-structure-unsupported")
    return value


def _extract_title(html: str) -> str:
    match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
    if match is None:
        match = re.search(
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)',
            html,
            re.IGNORECASE,
        )
    if match is None:
        raise XArticleProviderError("x-article-structure-unsupported")
    title = html_module.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()
    if not title:
        raise XArticleProviderError("x-article-structure-unsupported")
    return title


def _api_media_urls(html: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for match in _API_MEDIA_RE.finditer(html):
        identifiers = list(_API_ID_RE.finditer(html, 0, match.start()))
        if identifiers:
            result[identifiers[-1].group(1)] = html_module.unescape(match.group(1))
    return result


def _media_url(media_id: str, api_urls: Mapping[str, str]) -> str | None:
    try:
        needle = int(media_id).to_bytes(8, "big")
    except (OverflowError, ValueError):
        return None
    for encoded, remote_url in api_urls.items():
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            continue
        if needle in decoded:
            return remote_url
    return None


def _parse_code(markdown: str) -> tuple[str, str]:
    stripped = markdown.strip()
    match = re.fullmatch(r"```([A-Za-z0-9_+.-]*)\n([\s\S]*?)\n```", stripped)
    if match is None:
        raise XArticleProviderError("x-article-structure-unsupported")
    return match.group(1) or "text", match.group(2)


def parse_x_article_html(payload: bytes, canonical_url: str) -> XArticleDocument:
    """Recover an ordered DraftJS article document from inert HTML bytes."""

    canonical = normalize_x_article_url(canonical_url)
    try:
        html = payload.decode("utf-8")
    except (AttributeError, UnicodeError):
        raise XArticleProviderError("x-article-response-invalid") from None
    title = _extract_title(html)
    author_match = _URL_PATH_RE.fullmatch(urlsplit(canonical).path)
    assert author_match is not None

    raw_blocks: dict[int, tuple[str, str]] = {}
    matches = list(_BLOCK_RE.finditer(html))
    for match in matches:
        index = int(match.group(1))
        if index in raw_blocks:
            raise XArticleProviderError("x-article-structure-unsupported")
        raw_blocks[index] = (_decode_json_string(match.group(2)), match.group(3))
    if not raw_blocks:
        raise XArticleProviderError("x-article-body-missing")
    if sorted(raw_blocks) != list(range(min(raw_blocks), max(raw_blocks) + 1)):
        raise XArticleProviderError("x-article-structure-unsupported")

    ranges = {
        (int(match.group(1)), int(match.group(2))): match.group(3)
        for match in _ENTITY_RANGE_RE.finditer(html)
    }
    entity_keys = {
        match.group(2): int(match.group(1)) for match in _ENTITY_MAP_RE.finditer(html)
    }
    entity_types = {
        int(match.group(1)): match.group(2) for match in _ENTITY_TYPE_RE.finditer(html)
    }
    entity_markdown: dict[int, str | None] = {}
    for match in _ENTITY_MARKDOWN_RE.finditer(html):
        raw = match.group(2)
        entity_markdown[int(match.group(1))] = (
            None if raw == "null" else json.loads(raw)
        )
    entity_media = {
        int(match.group(1)): match.group(2) for match in _ENTITY_MEDIA_RE.finditer(html)
    }
    entity_tweets = {
        int(match.group(1)): match.group(2) for match in _ENTITY_TWEET_RE.finditer(html)
    }
    api_urls = _api_media_urls(html)

    blocks: list[XArticleBlock] = []
    assets_by_url: dict[str, XArticleAssetRef] = {}
    for index in sorted(raw_blocks):
        text, block_type = raw_blocks[index]
        if block_type == "unstyled":
            if text.strip():
                blocks.append(XArticleBlock(type="paragraph", text=text))
        elif block_type in {"header-one", "header-two"}:
            if not text.strip():
                raise XArticleProviderError("x-article-structure-unsupported")
            blocks.append(XArticleBlock(
                type="heading", level=1 if block_type == "header-one" else 2, text=text
            ))
        elif block_type == "blockquote":
            if not text.strip():
                raise XArticleProviderError("x-article-structure-unsupported")
            blocks.append(XArticleBlock(type="blockquote", text=text))
        elif block_type == "ordered-list-item":
            if not text.strip():
                raise XArticleProviderError("x-article-structure-unsupported")
            blocks.append(XArticleBlock(type="ordered-list-item", text=text))
        elif block_type == "atomic":
            entity_key = ranges.get((index, 0))
            entity_index = entity_keys.get(entity_key or "")
            if entity_index is None:
                raise XArticleProviderError("x-article-structure-unsupported")
            entity_type = entity_types.get(entity_index)
            if entity_type == "DIVIDER":
                blocks.append(XArticleBlock(type="divider"))
            elif entity_type == "TWEET":
                tweet_id = entity_tweets.get(entity_index)
                if tweet_id is None:
                    raise XArticleProviderError("x-article-structure-unsupported")
                blocks.append(XArticleBlock(type="embedded-post", text=tweet_id))
            elif entity_type == "MARKDOWN":
                markdown = entity_markdown.get(entity_index)
                if not markdown:
                    raise XArticleProviderError("x-article-structure-unsupported")
                language, code = _parse_code(markdown)
                blocks.append(XArticleBlock(type="code", text=code, language=language))
            elif entity_type == "MEDIA":
                media_id = entity_media.get(entity_index)
                remote_url = _media_url(media_id or "", api_urls)
                if remote_url is None:
                    raise XArticleProviderError("x-article-structure-unsupported")
                asset = assets_by_url.get(remote_url)
                if asset is None:
                    asset = XArticleAssetRef(
                        key=hashlib.sha256(remote_url.encode("utf-8")).hexdigest()[:24],
                        remote_url=remote_url,
                    )
                    assets_by_url[remote_url] = asset
                blocks.append(XArticleBlock(type="image", asset_key=asset.key))
            else:
                raise XArticleProviderError("x-article-structure-unsupported")
        else:
            raise XArticleProviderError("x-article-structure-unsupported")
    if not blocks:
        raise XArticleProviderError("x-article-body-missing")
    return XArticleDocument(
        title=title,
        author_handle=f"@{author_match.group(1)}",
        blocks=tuple(blocks),
        assets=tuple(assets_by_url.values()),
    )


def article_json_value(
    document: XArticleDocument, asset_paths: Mapping[str, str]
) -> dict[str, object]:
    """Convert a parsed document into deterministic local-only structured data."""

    blocks: list[dict[str, object]] = []
    for block in document.blocks:
        if block.type == "paragraph":
            blocks.append({"type": "paragraph", "text": block.text})
        elif block.type == "blockquote":
            blocks.append({"type": "blockquote", "text": block.text})
        elif block.type == "divider":
            blocks.append({"type": "divider"})
        elif block.type == "embedded-post":
            blocks.append({"type": "embedded-post", "post_id": block.text})
        elif block.type == "heading":
            blocks.append({"type": "heading", "level": block.level, "text": block.text})
        elif block.type == "ordered-list-item":
            blocks.append({"type": "ordered-list-item", "text": block.text})
        elif block.type == "code":
            blocks.append(
                {"type": "code", "language": block.language, "text": block.text}
            )
        elif block.type == "image" and block.asset_key is not None:
            path = asset_paths.get(block.asset_key)
            if path is None:
                raise XArticleProviderError("x-article-image-invalid")
            pure = PurePosixPath(path)
            if pure.is_absolute() or ".." in pure.parts or pure.parent.name != "images":
                raise XArticleProviderError("x-article-image-invalid")
            blocks.append(
                {
                    "type": "image",
                    "asset_id": pure.stem,
                    "relative_path": pure.as_posix(),
                }
            )
        else:
            raise XArticleProviderError("x-article-structure-unsupported")
    return {
        "schema_version": 1,
        "provider": "x-public-relay-html",
        "title": document.title,
        "author_handle": document.author_handle,
        "blocks": blocks,
    }


def render_article_markdown(value: Mapping[str, object]) -> bytes:
    """Render canonical article JSON into deterministic UTF-8 Markdown bytes."""

    try:
        title = value["title"]
        author = value["author_handle"]
        blocks = value["blocks"]
        if not isinstance(title, str) or not isinstance(author, str) or not isinstance(blocks, list):
            raise ValueError
        output = f"# {title}\n\n> **作者**：{author}\n\n"
        in_list = False
        for block in blocks:
            if not isinstance(block, dict) or not isinstance(block.get("type"), str):
                raise ValueError
            block_type = block["type"]
            if in_list and block_type != "ordered-list-item":
                output += "\n"
                in_list = False
            if block_type == "paragraph" and isinstance(block.get("text"), str):
                output += f'{block["text"]}\n\n'
            elif block_type == "heading" and type(block.get("level")) is int and block["level"] in {1, 2} and isinstance(block.get("text"), str):
                output += f'{"#" * block["level"]} {block["text"]}\n\n'
            elif block_type == "blockquote" and isinstance(block.get("text"), str):
                output += "\n".join("> " + line for line in block["text"].split("\n")) + "\n\n"
            elif block_type == "divider":
                output += "---\n\n"
            elif block_type == "embedded-post" and isinstance(block.get("post_id"), str) and re.fullmatch(r"[0-9]+", block["post_id"]):
                output += f'[引用帖子](https://x.com/i/status/{block["post_id"]})\n\n'
            elif block_type == "ordered-list-item" and isinstance(block.get("text"), str):
                output += f'1. {block["text"]}\n'
                in_list = True
            elif block_type == "code" and isinstance(block.get("text"), str) and isinstance(block.get("language"), str):
                output += f'```{block["language"]}\n{block["text"]}\n```\n\n'
            elif block_type == "image" and isinstance(block.get("asset_id"), str) and isinstance(block.get("relative_path"), str):
                output += f'![{block["asset_id"]}]({block["relative_path"]})\n\n'
            else:
                raise ValueError
        return (output.rstrip() + "\n").encode("utf-8")
    except (KeyError, TypeError, ValueError):
        raise XArticleProviderError("x-article-structure-unsupported") from None


__all__ = [
    "XArticleAssetRef",
    "XArticleBlock",
    "XArticleDocument",
    "XArticleImage",
    "XArticleProviderError",
    "XArticleTransport",
    "article_json_value",
    "normalize_x_article_url",
    "parse_x_article_html",
    "render_article_markdown",
]
