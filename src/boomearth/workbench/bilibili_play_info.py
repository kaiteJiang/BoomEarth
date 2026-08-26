"""Strict, deterministic interpretation of private Bilibili play information."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import math
import re
from typing import Literal, Mapping, Sequence
from urllib.parse import urlsplit


AssetKind = Literal["progressive", "video", "audio"]
NextAction = Literal[
    "media-download-ready",
    "playurl-required",
    "offline-schema-review",
]

_BVID_RE = re.compile(r"^BV[0-9A-Za-z]{10}$")
_UPSTREAM_KEYS = frozenset({"bvid", "bv_id", "cid", "quality", "dash", "durl"})


class BilibiliPlayInfoError(RuntimeError):
    """A fixed, redacted error for structurally unsafe play information."""

    def __init__(self) -> None:
        super().__init__("Bilibili play information is structurally invalid")

    def __repr__(self) -> str:
        return "BilibiliPlayInfoError(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class BilibiliAsset:
    kind: AssetKind
    private_url: str = field(repr=False)
    mime_type: str
    codecs: str
    bandwidth: int
    size_bytes: int | None
    width: int | None
    height: int | None
    frame_rate: str | None
    backup_url_sha256: tuple[str, ...] = field(default=(), repr=False)

    def __repr__(self) -> str:
        return f"BilibiliAsset(kind={self.kind!r}, <redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class BilibiliMediaSelection:
    mode: Literal["progressive", "dash"]
    bv_id: str | None = field(repr=False)
    cid: int | None = field(repr=False)
    quality_id: int | None
    assets: tuple[BilibiliAsset] | tuple[BilibiliAsset, BilibiliAsset] = field(
        repr=False
    )
    rule_version: int = 1

    def __repr__(self) -> str:
        return (
            "BilibiliMediaSelection("
            f"mode={self.mode!r}, quality_id={self.quality_id!r}, "
            f"asset_count={len(self.assets)}, rule_version={self.rule_version})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class BilibiliInterpretation:
    next_action: NextAction
    bv_id: str | None = field(repr=False)
    cid: int | None = field(repr=False)
    selection: BilibiliMediaSelection | None = field(repr=False)

    def __repr__(self) -> str:
        return f"BilibiliInterpretation(next_action={self.next_action!r}, <redacted>)"


def _review() -> BilibiliInterpretation:
    return BilibiliInterpretation(
        next_action="offline-schema-review",
        bv_id=None,
        cid=None,
        selection=None,
    )


def _upstream(payload: Mapping[str, object]) -> Mapping[str, object] | None:
    outer = payload.get("data")
    if not isinstance(outer, Mapping):
        return None
    inner = outer.get("data")
    if inner is None:
        return outer
    if not isinstance(inner, Mapping):
        return None
    if _UPSTREAM_KEYS.intersection(outer):
        raise BilibiliPlayInfoError()
    return inner


def _alias(mapping: Mapping[str, object], camel: str, snake: str) -> object | None:
    if camel in mapping and snake in mapping:
        if mapping[camel] != mapping[snake]:
            raise BilibiliPlayInfoError()
        return mapping[camel]
    if camel in mapping:
        return mapping[camel]
    return mapping.get(snake)


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _safe_https_url(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query is None
        or port is not None and not 1 <= port <= 65535
    ):
        return None
    return value


def _frame_rate(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    rendered = str(value)
    parts = rendered.split("/")
    if len(parts) > 2 or any(not part for part in parts):
        return None
    try:
        numbers = [Decimal(part) for part in parts]
    except InvalidOperation:
        return None
    if any(not number.is_finite() for number in numbers):
        return None
    if numbers[0] <= 0 or len(numbers) == 2 and numbers[1] <= 0:
        return None
    numeric = float(numbers[0] / numbers[1]) if len(numbers) == 2 else float(numbers[0])
    if not math.isfinite(numeric) or numeric <= 0:
        return None
    return rendered


def _backup_hashes(mapping: Mapping[str, object]) -> tuple[str, ...] | None:
    raw = _alias(mapping, "backupUrl", "backup_url")
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return None
    hashes: list[str] = []
    for candidate in raw:
        url = _safe_https_url(candidate)
        if url is None:
            return None
        hashes.append(hashlib.sha256(url.encode("utf-8")).hexdigest())
    return tuple(hashes)


def _dash_asset(mapping: object, kind: Literal["video", "audio"]) -> tuple[int, BilibiliAsset] | None:
    if not isinstance(mapping, Mapping):
        return None
    stream_id = _positive_int(mapping.get("id"))
    url = _safe_https_url(_alias(mapping, "baseUrl", "base_url"))
    backups = _backup_hashes(mapping)
    bandwidth = _nonnegative_int(mapping.get("bandwidth"))
    mime = _alias(mapping, "mimeType", "mime_type")
    codecs = mapping.get("codecs")
    if (
        stream_id is None
        or url is None
        or backups is None
        or bandwidth is None
        or not isinstance(mime, str)
        or not isinstance(codecs, str)
    ):
        return None
    if kind == "video":
        width = _positive_int(mapping.get("width"))
        height = _positive_int(mapping.get("height"))
        raw_frame_rate = _alias(mapping, "frameRate", "frame_rate")
        frame_rate = _frame_rate(raw_frame_rate)
        if (
            mime != "video/mp4"
            or not codecs.startswith("avc1.")
            or width is None
            or height is None
            or frame_rate is None
        ):
            return None
    else:
        if mime != "audio/mp4" or not codecs.startswith("mp4a."):
            return None
        width = None
        height = None
        frame_rate = None
    return (
        stream_id,
        BilibiliAsset(
            kind=kind,
            private_url=url,
            mime_type=mime,
            codecs=codecs,
            bandwidth=bandwidth,
            size_bytes=None,
            width=width,
            height=height,
            frame_rate=frame_rate,
            backup_url_sha256=backups,
        ),
    )


def _dash_selection(
    upstream: Mapping[str, object], bv_id: str | None, cid: int | None
) -> BilibiliMediaSelection | None:
    dash = upstream.get("dash")
    if not isinstance(dash, Mapping):
        return None
    raw_video = dash.get("video")
    raw_audio = dash.get("audio")
    if (
        not isinstance(raw_video, Sequence)
        or isinstance(raw_video, (str, bytes, bytearray))
        or not isinstance(raw_audio, Sequence)
        or isinstance(raw_audio, (str, bytes, bytearray))
    ):
        return None
    videos = [asset for item in raw_video if (asset := _dash_asset(item, "video"))]
    audios = [asset for item in raw_audio if (asset := _dash_asset(item, "audio"))]
    if not videos or not audios:
        return None
    videos.sort(
        key=lambda item: (
            item[1].height or 0,
            item[1].width or 0,
            item[1].bandwidth,
            item[0],
        ),
        reverse=True,
    )
    audios.sort(key=lambda item: (item[1].bandwidth, item[0]), reverse=True)
    _, video = videos[0]
    max_quality_id = max(item[0] for item in videos)
    _, audio = audios[0]
    return BilibiliMediaSelection(
        mode="dash",
        bv_id=bv_id,
        cid=cid,
        quality_id=max_quality_id,
        assets=(video, audio),
    )


def _progressive_selection(
    upstream: Mapping[str, object],
    bv_id: str | None,
    cid: int | None,
    quality: int | None,
) -> BilibiliMediaSelection | None:
    durl = upstream.get("durl")
    if (
        not isinstance(durl, Sequence)
        or isinstance(durl, (str, bytes, bytearray))
        or len(durl) != 1
        or not isinstance(durl[0], Mapping)
    ):
        return None
    item = durl[0]
    url = _safe_https_url(item.get("url"))
    size = _positive_int(item.get("size"))
    length = _positive_int(item.get("length"))
    backups = _backup_hashes(item)
    if url is None or size is None or length is None or backups is None:
        return None
    asset = BilibiliAsset(
        kind="progressive",
        private_url=url,
        mime_type="video/mp4",
        codecs="",
        bandwidth=0,
        size_bytes=size,
        width=None,
        height=None,
        frame_rate=None,
        backup_url_sha256=backups,
    )
    return BilibiliMediaSelection(
        mode="progressive",
        bv_id=bv_id,
        cid=cid,
        quality_id=quality,
        assets=(asset,),
    )


def _identifiers(upstream: Mapping[str, object]) -> tuple[str | None, int | None]:
    identifier_keys = {"bvid", "bv_id", "cid"}.intersection(upstream)
    if not identifier_keys:
        return None, None
    if "bvid" in upstream and "bv_id" in upstream:
        raise BilibiliPlayInfoError()
    raw_bv = upstream.get("bvid", upstream.get("bv_id"))
    raw_cid = upstream.get("cid")
    if not isinstance(raw_bv, str) or _BVID_RE.fullmatch(raw_bv) is None:
        raise BilibiliPlayInfoError()
    cid = _positive_int(raw_cid)
    if cid is None:
        raise BilibiliPlayInfoError()
    return raw_bv, cid


def interpret_play_info(payload: Mapping[str, object]) -> BilibiliInterpretation:
    """Interpret only documented wrapper paths and never guess nested URLs."""

    upstream = _upstream(payload)
    if upstream is None or not _UPSTREAM_KEYS.intersection(upstream):
        return _review()
    bv_id, cid = _identifiers(upstream)
    raw_quality = upstream.get("quality")
    if raw_quality is None:
        quality = None
    else:
        quality = _positive_int(raw_quality)
        if quality is None:
            raise BilibiliPlayInfoError()
    dash = _dash_selection(upstream, bv_id, cid) if "dash" in upstream else None
    progressive = (
        _progressive_selection(upstream, bv_id, cid, quality)
        if "durl" in upstream
        else None
    )
    selection: BilibiliMediaSelection | None
    if dash is not None and progressive is not None:
        selection = (
            dash
            if quality is None or (dash.quality_id or 0) > quality
            else progressive
        )
    elif dash is not None:
        selection = dash
    elif progressive is not None:
        selection = progressive
    else:
        selection = None
    if selection is not None:
        return BilibiliInterpretation(
            next_action="media-download-ready",
            bv_id=bv_id,
            cid=cid,
            selection=selection,
        )
    if "dash" not in upstream and "durl" not in upstream:
        if bv_id is None or cid is None:
            return _review()
        return BilibiliInterpretation(
            next_action="playurl-required",
            bv_id=bv_id,
            cid=cid,
            selection=None,
        )
    return BilibiliInterpretation(
        next_action="offline-schema-review",
        bv_id=bv_id,
        cid=cid,
        selection=None,
    )


def selection_value(selection: BilibiliMediaSelection) -> dict[str, object]:
    """Return the private immutable selection value used by later hash contracts."""

    return {
        "schema": 1,
        "mode": selection.mode,
        "bv_id": selection.bv_id,
        "cid": selection.cid,
        "quality_id": selection.quality_id,
        "rule_version": selection.rule_version,
        "assets": [
            {
                "kind": asset.kind,
                "private_url": asset.private_url,
                "mime_type": asset.mime_type,
                "codecs": asset.codecs,
                "bandwidth": asset.bandwidth,
                "size_bytes": asset.size_bytes,
                "width": asset.width,
                "height": asset.height,
                "frame_rate": asset.frame_rate,
                "backup_url_sha256": list(asset.backup_url_sha256),
            }
            for asset in selection.assets
        ],
    }


__all__ = [
    "BilibiliAsset",
    "BilibiliInterpretation",
    "BilibiliMediaSelection",
    "BilibiliPlayInfoError",
    "interpret_play_info",
    "selection_value",
]
