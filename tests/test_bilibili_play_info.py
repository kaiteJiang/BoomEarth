from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from boomearth.workbench.bilibili_play_info import (
    BilibiliPlayInfoError,
    interpret_play_info,
    selection_value,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _dash() -> dict[str, object]:
    return _fixture("tikhub_bilibili_play_info_dash.json")


def _progressive() -> dict[str, object]:
    return _fixture("tikhub_bilibili_play_info_progressive.json")


def _data(payload: dict[str, object]) -> dict[str, object]:
    value = payload["data"]
    assert isinstance(value, dict)
    return value


def _dash_data(payload: dict[str, object]) -> dict[str, object]:
    value = _data(payload)["dash"]
    assert isinstance(value, dict)
    return value


def test_compatible_dash_pair_is_selected_in_video_audio_order() -> None:
    result = interpret_play_info(_dash())

    assert result.next_action == "media-download-ready"
    assert result.selection is not None
    assert result.selection.mode == "dash"
    assert result.selection.quality_id == 80
    assert [asset.kind for asset in result.selection.assets] == ["video", "audio"]
    assert result.selection.assets[0].private_url.endswith("main.m4s")
    assert result.selection.assets[1].mime_type == "audio/mp4"


def test_one_complete_progressive_asset_is_selected() -> None:
    result = interpret_play_info(_progressive())

    assert result.next_action == "media-download-ready"
    assert result.selection is not None
    assert result.selection.mode == "progressive"
    assert result.selection.quality_id == 80
    assert len(result.selection.assets) == 1
    assert result.selection.assets[0].kind == "progressive"
    assert result.selection.assets[0].size_bytes == 424242


def test_documented_snake_case_stream_aliases_are_accepted() -> None:
    payload = _dash()
    dash = _dash_data(payload)
    video = dash["video"][0]
    assert isinstance(video, dict)
    video["base_url"] = video.pop("baseUrl")
    video["backup_url"] = video.pop("backupUrl")
    video["mime_type"] = video.pop("mimeType")
    video["frame_rate"] = video.pop("frameRate")
    audio = dash["audio"][0]
    assert isinstance(audio, dict)
    audio["base_url"] = audio.pop("baseUrl")
    audio["backup_url"] = audio.pop("backupUrl")
    audio["mime_type"] = audio.pop("mimeType")

    result = interpret_play_info({"data": {"data": _data(payload)}})

    assert result.next_action == "media-download-ready"
    assert result.selection is not None
    assert result.selection.mode == "dash"


def test_real_play_info_schema_selects_complete_dash_without_identifiers() -> None:
    payload = _fixture("tikhub_bilibili_play_info_real_schema.json")

    result = interpret_play_info(payload)

    assert result.next_action == "media-download-ready"
    assert result.bv_id is None
    assert result.cid is None
    assert result.selection is not None
    assert result.selection.bv_id is None
    assert result.selection.cid is None
    assert result.selection.mode == "dash"
    video, audio = result.selection.assets
    assert video.height == 1080
    assert audio.bandwidth == 192000


def test_equal_duplicate_stream_aliases_are_accepted() -> None:
    result = interpret_play_info(
        _fixture("tikhub_bilibili_play_info_real_schema.json")
    )

    assert result.selection is not None
    assert result.selection.assets[0].private_url.endswith("1080.m4s")


def test_conflicting_duplicate_stream_aliases_are_rejected() -> None:
    payload = _fixture("tikhub_bilibili_play_info_real_schema.json")
    video = _dash_data(payload)["video"][0]
    assert isinstance(video, dict)
    video["baseUrl"] = "https://video.example.invalid/conflict.m4s"

    with pytest.raises(BilibiliPlayInfoError):
        interpret_play_info(payload)


def test_only_documented_wrapper_paths_are_examined() -> None:
    nested = _dash()
    payload = {
        "data": {
            "metadata": nested["data"],
            "unrelated_url": "https://unrelated.example.invalid/video.mp4",
        }
    }

    result = interpret_play_info(payload)

    assert result.next_action == "offline-schema-review"
    assert result.selection is None


def test_higher_dash_quality_beats_complete_progressive() -> None:
    payload = _dash()
    data = _data(payload)
    data["quality"] = 64
    data["durl"] = _data(_progressive())["durl"]

    result = interpret_play_info(payload)

    assert result.selection is not None
    assert result.selection.mode == "dash"


def test_dash_quality_comparison_uses_max_compatible_video_id() -> None:
    payload = _dash()
    data = _data(payload)
    data["quality"] = 100
    data["durl"] = _data(_progressive())["durl"]
    video = _dash_data(payload)["video"][0]
    assert isinstance(video, dict)
    _dash_data(payload)["video"].append(
        {
            **deepcopy(video),
            "id": 120,
            "baseUrl": "https://video.example.invalid/720-quality-120.m4s",
            "height": 720,
            "width": 1280,
        }
    )

    result = interpret_play_info(payload)

    assert result.selection is not None
    assert result.selection.mode == "dash"
    assert result.selection.quality_id == 120
    assert result.selection.assets[0].private_url.endswith("main.m4s")


def test_equal_quality_prefers_complete_progressive() -> None:
    payload = _dash()
    _data(payload)["durl"] = _data(_progressive())["durl"]

    result = interpret_play_info(payload)

    assert result.selection is not None
    assert result.selection.mode == "progressive"


def test_missing_progressive_quality_prefers_compatible_dash() -> None:
    payload = _dash()
    data = _data(payload)
    data.pop("quality")
    data["durl"] = _data(_progressive())["durl"]

    result = interpret_play_info(payload)

    assert result.selection is not None
    assert result.selection.mode == "dash"


def test_dash_candidates_are_filtered_and_sorted_deterministically() -> None:
    payload = _dash()
    dash = _dash_data(payload)
    original_video = dash["video"][0]
    original_audio = dash["audio"][0]
    assert isinstance(original_video, dict)
    assert isinstance(original_audio, dict)
    dash["video"] = [
        {**deepcopy(original_video), "id": 120, "codecs": "hev1.1", "height": 2160},
        {**deepcopy(original_video), "id": 64, "baseUrl": "https://video.example.invalid/720.m4s", "height": 720, "width": 1280, "bandwidth": 900000},
        {**deepcopy(original_video), "id": 80, "baseUrl": "https://video.example.invalid/1080-low.m4s", "bandwidth": 1800000},
        {**deepcopy(original_video), "id": 80, "baseUrl": "https://video.example.invalid/1080-high.m4s", "bandwidth": 2800000},
    ]
    dash["audio"] = [
        {**deepcopy(original_audio), "id": 30290, "codecs": "ec-3", "bandwidth": 384000},
        {**deepcopy(original_audio), "id": 30216, "baseUrl": "https://audio.example.invalid/low.m4s", "bandwidth": 128000},
        {**deepcopy(original_audio), "id": 30280, "baseUrl": "https://audio.example.invalid/high.m4s", "bandwidth": 192000},
    ]

    result = interpret_play_info(payload)

    assert result.selection is not None
    video, audio = result.selection.assets
    assert video.private_url.endswith("1080-high.m4s")
    assert audio.private_url.endswith("high.m4s")


def test_backup_urls_are_hashed_metadata_and_never_selected() -> None:
    payload = _dash()
    result = interpret_play_info(payload)
    assert result.selection is not None

    value = selection_value(result.selection)
    video_value = value["assets"][0]
    assert isinstance(video_value, dict)
    backup = "https://backup.example.invalid/main.m4s"
    assert video_value["backup_url_sha256"] == [
        hashlib.sha256(backup.encode("utf-8")).hexdigest()
    ]
    assert backup not in json.dumps(value)
    assert result.selection.assets[0].private_url != backup


def test_identifiers_only_requires_separate_playurl_step() -> None:
    result = interpret_play_info(_fixture("tikhub_bilibili_identifiers_only.json"))

    assert result.next_action == "playurl-required"
    assert result.bv_id == "BV1ab411c7De"
    assert result.cid == 123456789
    assert result.selection is None


@pytest.mark.parametrize("field,value", [("cid", True), ("cid", 0), ("quality", True)])
def test_bool_or_nonpositive_numeric_identifiers_are_rejected(field: str, value: object) -> None:
    payload = _dash()
    _data(payload)[field] = value

    with pytest.raises(BilibiliPlayInfoError) as raised:
        interpret_play_info(payload)

    assert repr(value) not in repr(raised.value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.pop("bvid"),
        lambda data: data.pop("cid"),
        lambda data: data.update({"bvid": "not-a-bv-id"}),
        lambda data: data.update({"bvid": "BV1ab411c7De", "bv_id": "BV1xy411c7Df"}),
    ],
)
def test_incomplete_or_duplicate_identifiers_stop_safely(mutate) -> None:
    payload = _fixture("tikhub_bilibili_identifiers_only.json")
    mutate(_data(payload))

    with pytest.raises(BilibiliPlayInfoError):
        interpret_play_info(payload)


@pytest.mark.parametrize(
    "mutation",
    [
        ("size", -1),
        ("size", True),
        ("length", 0),
    ],
)
def test_invalid_progressive_metadata_stops_for_offline_review(mutation: tuple[str, object]) -> None:
    payload = _progressive()
    item = _data(payload)["durl"][0]
    assert isinstance(item, dict)
    item[mutation[0]] = mutation[1]

    result = interpret_play_info(payload)

    assert result.next_action == "offline-schema-review"
    assert result.selection is None


def test_multiple_progressive_segments_are_not_guessed_or_joined() -> None:
    payload = _progressive()
    durl = _data(payload)["durl"]
    assert isinstance(durl, list)
    durl.append(deepcopy(durl[0]))

    result = interpret_play_info(payload)

    assert result.next_action == "offline-schema-review"
    assert result.selection is None


@pytest.mark.parametrize(
    "url",
    [
        "http://video.example.invalid/main.m4s",
        "https://user:pass@video.example.invalid/main.m4s",
        "https://video.example.invalid/main.m4s#fragment",
    ],
)
def test_unsafe_candidate_url_is_not_selected(url: str) -> None:
    payload = _dash()
    video = _dash_data(payload)["video"][0]
    assert isinstance(video, dict)
    video["baseUrl"] = url

    result = interpret_play_info(payload)

    assert result.next_action == "offline-schema-review"
    assert result.selection is None
    assert url not in repr(result)


@pytest.mark.parametrize(
    "target,value",
    [
        ("video", []),
        ("audio", []),
        ("video_codecs", "av01.0.08M.08"),
        ("audio_codecs", "ec-3"),
        ("frame_rate", "NaN"),
        ("bandwidth", -1),
    ],
)
def test_incompatible_or_malformed_dash_is_not_selected(target: str, value: object) -> None:
    payload = _dash()
    dash = _dash_data(payload)
    if target in {"video", "audio"}:
        dash[target] = value
    elif target == "video_codecs":
        dash["video"][0]["codecs"] = value
    elif target == "audio_codecs":
        dash["audio"][0]["codecs"] = value
    elif target == "frame_rate":
        dash["video"][0]["frameRate"] = value
    else:
        dash["video"][0]["bandwidth"] = value

    result = interpret_play_info(payload)

    assert result.next_action == "offline-schema-review"
    assert result.selection is None


def test_repr_and_error_are_redacted() -> None:
    payload = _dash()
    result = interpret_play_info(payload)
    assert result.selection is not None
    serialized = selection_value(result.selection)

    assert "BV1ab411c7De" not in repr(result)
    assert "example.invalid" not in repr(result)
    assert "BV1ab411c7De" not in repr(result.selection)
    assert "example.invalid" not in repr(result.selection.assets[0])
    assert serialized["bv_id"] == "BV1ab411c7De"
    assert serialized["assets"][0]["private_url"].startswith("https://")


def test_null_or_unknown_payload_requires_offline_schema_review() -> None:
    for payload in ({"code": 200, "data": None}, {"code": 200}, {"data": []}):
        result = interpret_play_info(payload)
        assert result.next_action == "offline-schema-review"
        assert result.selection is None
