"""Validate explicit painting regions and resolve complete speech cue phrases."""
from __future__ import annotations

import math
import re
from pathlib import Path

from boomearth.video.artifacts import capture_regular_file


class SpongeComponentError(ValueError):
    pass


def _number(value: object, low: float, high: float) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and low <= value <= high)


def validate_drawing_plan(value: object, plan, project_root: Path) -> dict:
    error = "sponge drawing plan is invalid"
    if (not isinstance(value, dict) or set(value) != {"schema_version", "scenes"}
            or value["schema_version"] != 2 or not isinstance(value["scenes"], dict)
            or set(value["scenes"]) != {scene.id for scene in plan.scenes}):
        raise SpongeComponentError(error)
    for scene in plan.scenes:
        entry = value["scenes"][scene.id]
        if (not isinstance(entry, dict) or set(entry) != {"source_sha256", "parts"}
                or not scene.visual_asset
                or entry["source_sha256"] != capture_regular_file(project_root / scene.visual_asset, within=project_root).sha256
                or not isinstance(entry["parts"], list) or not 3 <= len(entry["parts"]) <= 24):
            raise SpongeComponentError(error)
        seen = set(); text_started = False
        for index, part in enumerate(entry["parts"]):
            if (not isinstance(part, dict) or set(part) != {"id", "kind", "polygons", "weight"}
                    or not isinstance(part["id"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", part["id"])
                    or part["id"] in seen or part["kind"] not in {"character", "object", "motion", "text"}
                    or (index == 0) != (part["kind"] == "character")
                    or not _number(part["weight"], .1, 12)
                    or not isinstance(part["polygons"], list) or not 1 <= len(part["polygons"]) <= 20):
                raise SpongeComponentError(error)
            if text_started and part["kind"] != "text":
                raise SpongeComponentError(error)
            text_started |= part["kind"] == "text"
            seen.add(part["id"])
            for poly in part["polygons"]:
                if (not isinstance(poly, list) or not 3 <= len(poly) <= 32
                        or any(not isinstance(p, list) or len(p) != 2 or not all(_number(x, 0, 1) for x in p) for p in poly)):
                    raise SpongeComponentError(error)
                area = abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))) / 2
                if area < .00001:
                    raise SpongeComponentError(error)
        if sum(part["kind"] == "text" for part in entry["parts"]) != len(scene.overlay_labels):
            raise SpongeComponentError(error)
    return value["scenes"]


def resolve_cue_start(phrase: str, captions: list[dict], after: float = -1) -> float:
    """Match the full phrase across real caption boundaries without fuzzy fallback."""
    normalize = lambda text: "".join(c.casefold() for c in str(text) if c.isalnum())
    target = normalize(phrase)
    characters: list[str] = []; starts: list[float] = []
    for caption in captions:
        text = normalize(caption["text"])
        characters.extend(text)
        starts.extend([float(caption["start"])] * len(text))
    source = "".join(characters)
    index = source.find(target) if target else -1
    while index >= 0:
        if starts[index] >= after:
            return starts[index]
        index = source.find(target, index + 1)
    raise SpongeComponentError("sponge text cue has no complete speech match")
