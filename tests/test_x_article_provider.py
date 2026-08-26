from __future__ import annotations

from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image

from boomearth.providers.x_article import (
    XArticleProviderError,
    XArticleTransport,
    article_json_value,
    normalize_x_article_url,
    parse_x_article_html,
    render_article_markdown,
)
import boomearth.providers.x_article as x_article_module


FIXTURES = Path(__file__).parent / "fixtures" / "x_article"
CANONICAL = "https://x.com/fixture_author/status/1234567890123456789"


def test_normalize_x_article_url_accepts_only_canonical_status_urls() -> None:
    assert normalize_x_article_url(CANONICAL) == CANONICAL
    assert (
        normalize_x_article_url(
            "https://www.x.com/fixture_author/status/1234567890123456789"
        )
        == CANONICAL
    )


@pytest.mark.parametrize(
    "value",
    [
        "http://x.com/user/status/1",
        "https://twitter.com/user/status/1",
        "https://t.co/abc",
        "https://mobile.x.com/user/status/1",
        "https://x.com/user/status/1?x=1",
        "https://x.com/user/status/1#fragment",
        "https://x.com:443/user/status/1",
        "https://u:p@x.com/user/status/1",
        "https://x.com/user/status/1/extra",
        "https://x.com/too-long-user-name/status/1",
        "https://x.com/usér/status/1",
        " https://x.com/user/status/1",
        "https://x.com/user/status/1\nhttps://x.com/user/status/2",
    ],
)
def test_normalize_x_article_url_rejects_noncanonical_or_ambiguous_input(
    value: str,
) -> None:
    with pytest.raises(XArticleProviderError, match="^x-article-input-invalid$"):
        normalize_x_article_url(value)


def test_parse_basic_article_preserves_blocks_and_deterministic_markdown() -> None:
    document = parse_x_article_html(
        (FIXTURES / "article-basic.html").read_bytes(), CANONICAL
    )
    value = article_json_value(document, {})

    assert document.title == "First & Principles"
    assert document.author_handle == "@fixture_author"
    assert [block.type for block in document.blocks] == [
        "paragraph",
        "heading",
        "ordered-list-item",
        "ordered-list-item",
        "paragraph",
    ]
    assert value["blocks"][1] == {
        "type": "heading",
        "level": 2,
        "text": "A useful heading",
    }
    expected = (
        "# First & Principles\n\n"
        "> **作者**：@fixture_author\n\n"
        "Opening paragraph.\n\n"
        "## A useful heading\n\n"
        "1. First item\n"
        "1. Second item\n\n"
        "Line one\nLine two\n"
    ).encode("utf-8")
    assert render_article_markdown(value) == expected
    assert render_article_markdown(value) == expected


def test_parse_code_and_repeated_media_preserves_order_and_deduplicates_assets() -> None:
    document = parse_x_article_html(
        (FIXTURES / "article-code-images.html").read_bytes(), CANONICAL
    )
    assert [block.type for block in document.blocks] == [
        "heading",
        "code",
        "image",
        "image",
    ]
    assert document.blocks[1].language == "python"
    assert document.blocks[1].text == 'print("safe <code>")'
    assert len(document.assets) == 1
    asset_key = document.assets[0].key
    value = article_json_value(document, {asset_key: "images/image-001.jpg"})
    assert value["blocks"][2] == {
        "type": "image",
        "asset_id": "image-001",
        "relative_path": "images/image-001.jpg",
    }
    assert value["blocks"][3] == value["blocks"][2]
    markdown = render_article_markdown(value).decode("utf-8")
    assert "```python\nprint(\"safe <code>\")\n```" in markdown
    assert markdown.count("![image-001](images/image-001.jpg)") == 2


@pytest.mark.parametrize(
    "payload",
    [
        b"<html><h1>Empty</h1></html>",
        b'<html><h1>Unknown</h1><script>"content_state:blocks:0":"$R[1]={__id:\\"b0\\",__typename:\\"DraftJsBlock\\",key:\\"a\\",text:\\"x\\",type:\\"blockquote\\"}"</script></html>',
        b'<html><h1>Broken entity</h1><script>"content_state:blocks:0":"$R[1]={__id:\\"b0\\",__typename:\\"DraftJsBlock\\",key:\\"a\\",text:\\" \",type:\\"atomic\\"}"</script></html>',
    ],
)
def test_parse_fails_closed_for_missing_or_unresolved_body(payload: bytes) -> None:
    with pytest.raises(XArticleProviderError):
        parse_x_article_html(payload, CANONICAL)


def _image_bytes(image_format: str) -> bytes:
    stream = BytesIO()
    Image.new("RGB", (1, 1), (1, 2, 3)).save(stream, format=image_format)
    return stream.getvalue()


PNG_1X1 = _image_bytes("PNG")
WEBP_1X1 = _image_bytes("WEBP")
JPEG_1X1 = _image_bytes("JPEG")


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_transport_fetches_one_html_response_without_following_redirects() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"location": "https://example.invalid/private"},
            request=request,
        )

    with _client(handler) as client:
        transport = XArticleTransport(client)
        with pytest.raises(XArticleProviderError, match="^x-article-page-unavailable$"):
            transport.fetch_page(CANONICAL)

    assert len(requests) == 1
    assert requests[0].url.host == "x.com"


def test_transport_rejects_non_html_and_oversized_page(monkeypatch) -> None:
    responses = [
        ("application/json", b"{}"),
        ("text/html; charset=utf-8", b"12345"),
    ]
    monkeypatch.setattr(x_article_module, "PAGE_MAX_BYTES", 4)
    for content_type, payload in responses:
        with _client(
            lambda request, ct=content_type, body=payload: httpx.Response(
                200, headers={"content-type": ct}, content=body, request=request
            )
        ) as client:
            with pytest.raises(XArticleProviderError, match="^x-article-response-invalid$"):
                XArticleTransport(client).fetch_page(CANONICAL)


def test_transport_enforces_total_read_deadline(monkeypatch) -> None:
    class SlowChunks(httpx.SyncByteStream):
        def __iter__(self):
            yield b"a"
            yield b"b"

    clock = iter((0.0, 0.1, 1.1))
    monkeypatch.setattr(x_article_module, "TOTAL_READ_TIMEOUT_SECONDS", 1.0, raising=False)
    monkeypatch.setattr(x_article_module, "monotonic", lambda: next(clock), raising=False)

    with _client(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/html"},
            stream=SlowChunks(),
            request=request,
        )
    ) as client:
        with pytest.raises(XArticleProviderError, match="^x-article-response-invalid$"):
            XArticleTransport(client).fetch_page(CANONICAL)


@pytest.mark.parametrize(
    ("content_type", "payload", "expected"),
    [
        ("image/png", PNG_1X1, "png"),
        ("image/webp", WEBP_1X1, "webp"),
        ("image/jpeg", JPEG_1X1, "jpeg"),
    ],
)
def test_transport_decodes_supported_images(
    content_type: str, payload: bytes, expected: str
) -> None:
    with _client(
        lambda request: httpx.Response(
            200,
            headers={"content-type": content_type},
            content=payload,
            request=request,
        )
    ) as client:
        image = XArticleTransport(client).fetch_image(
            "https://pbs.twimg.com/media/example?format=jpg&name=orig"
        )

    assert image.payload == payload
    assert (image.format, image.width, image.height) == (expected, 1, 1)


@pytest.mark.parametrize(
    ("url", "content_type", "payload"),
    [
        ("https://example.com/media/x", "image/png", PNG_1X1),
        ("https://pbs.twimg.com/other/x", "image/png", PNG_1X1),
        ("https://pbs.twimg.com/media/x", "image/jpeg", PNG_1X1),
        ("https://pbs.twimg.com/media/x", "image/png", b"not-an-image"),
        ("https://pbs.twimg.com/media/x", "image/svg+xml", b"<svg/>")
    ],
)
def test_transport_rejects_wrong_image_target_mime_magic_or_decode(
    url: str, content_type: str, payload: bytes
) -> None:
    with _client(
        lambda request: httpx.Response(
            200,
            headers={"content-type": content_type},
            content=payload,
            request=request,
        )
    ) as client:
        with pytest.raises(XArticleProviderError, match="^x-article-image-invalid$"):
            XArticleTransport(client).fetch_image(url)


def test_transport_rejects_oversized_image(monkeypatch) -> None:
    monkeypatch.setattr(x_article_module, "IMAGE_MAX_BYTES", len(PNG_1X1) - 1)
    with _client(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=PNG_1X1,
            request=request,
        )
    ) as client:
        with pytest.raises(XArticleProviderError, match="^x-article-image-invalid$"):
            XArticleTransport(client).fetch_image(
                "https://pbs.twimg.com/media/example"
            )
