"""Validate source-bound, lossless semantic pixel partitions for comic painting."""
from __future__ import annotations

import base64
import binascii
import io
import struct
import hashlib
import math
import re

from PIL import Image

from boomearth.video.artifacts import capture_regular_file
from boomearth.video.sponge_components import SpongeComponentError


def build_comic_semantic_entry(source_path, mask_path, parts):
    """Compile a reviewed index mask; never infer objects from label rectangles.

    The caller supplies one source-bound integer owner per pixel. White paper
    within three RGB levels of the canvas is omitted from brush traversal.
    """
    error = "comic semantic pixel partition is invalid"
    clean = []
    for i, part in enumerate(parts):
        if (not isinstance(part, dict) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", str(part.get("id", "")))
                or part.get("kind") not in {"character", "object", "motion", "text"}
                or (i == 0) != (part.get("kind") == "character")
                or type(part.get("weight")) not in {int, float}
                or not math.isfinite(part["weight"]) or not .1 <= part["weight"] <= 12):
            raise SpongeComponentError(error)
        clean.append({k: part[k] for k in ("id", "kind", "weight")})
    if (not 3 <= len(clean) <= 24 or len({p["id"] for p in clean}) != len(clean)
            or any(p["kind"] != "text" for p in clean[next((i for i,p in enumerate(clean) if p["kind"] == "text"),len(clean)):] )):
        raise SpongeComponentError(error)
    with Image.open(source_path) as image:
        width, height = image.size
        rgb = Image.alpha_composite(Image.new("RGBA", image.size, "white"), image.convert("RGBA")).convert("RGB").tobytes()
    with Image.open(mask_path) as mask:
        if mask.mode not in {"L", "P"} or mask.size != (width, height):
            raise SpongeComponentError(error)
        owners = bytearray(mask.tobytes())
    bounds = [[width, height, -1, -1] for _ in clean]
    excluded = 0
    for pixel, owner in enumerate(owners):
        if owner > len(clean):
            raise SpongeComponentError(error)
        if min(rgb[pixel*3:pixel*3+3]) >= 252:
            excluded += bool(owner)
            owners[pixel] = 0
            continue
        if not owner:
            raise SpongeComponentError("comic semantic partition omits visible source pixels")
        x, y = pixel % width, pixel // width
        b = bounds[owner-1]
        b[0] = min(b[0], x); b[1] = min(b[1], y)
        b[2] = max(b[2], x); b[3] = max(b[3], y)
    if any(b[2] < 0 for b in bounds):
        raise SpongeComponentError("comic semantic partition contains an empty visible part")
    runs = bytearray()
    start = 0
    for end in range(1, len(owners)+1):
        if end == len(owners) or owners[end] != owners[start]:
            runs.extend(struct.pack("<IB", end-start, owners[start])); start=end
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    semantic = dict(source_sha256=source_sha, width=width, height=height, parts=clean,
                    ownership_rle=base64.b64encode(runs).decode("ascii"))
    drawing_parts = []
    for part, (x0,y0,x1,y1) in zip(clean, bounds):
        x0=max(0,x0-1)/width; y0=max(0,y0-1)/height
        x1=min(width,x1+2)/width; y1=min(height,y1+2)/height
        drawing_parts.append(dict(part, polygons=[[[x0,y0],[x1,y0],[x1,y1],[x0,y1]]]))
    drawing = dict(source_sha256=source_sha, parts=drawing_parts)
    receipt = dict(source_sha256=source_sha, mask_sha256=hashlib.sha256(mask_path.read_bytes()).hexdigest(),
                   width=width,height=height,part_count=len(clean),missing_visible_pixels=0,
                   near_white_paper_excluded=excluded,maximum_paper_rgb_difference=3)
    return drawing, semantic, receipt


def validate_comic_semantic_parts(value, drawing, plan, project_root):
    error = "comic semantic pixel partition is invalid"
    if (not isinstance(value, dict) or set(value) != {"schema_version", "scenes"}
            or value["schema_version"] != 1 or not isinstance(value["scenes"], dict)
            or set(value["scenes"]) != set(drawing)):
        raise SpongeComponentError(error)
    for scene in plan.scenes:
        entry = value["scenes"][scene.id]
        expected = drawing[scene.id]
        if (not isinstance(entry, dict) or set(entry) != {
                "source_sha256", "width", "height", "parts", "ownership_rle"}
                or entry["source_sha256"] != expected["source_sha256"]
                or entry["parts"] != [{k: p[k] for k in ("id", "kind", "weight")}
                                      for p in expected["parts"]]
                or any(type(entry[k]) is not int or not 1 <= entry[k] <= 8192
                       for k in ("width", "height"))
                or not isinstance(entry["ownership_rle"], str)
                or len(entry["ownership_rle"]) > 32_000_000):
            raise SpongeComponentError(error)
        source = capture_regular_file(project_root / scene.visual_asset, within=project_root)
        if source.sha256 != entry["source_sha256"]:
            raise SpongeComponentError(error)
        try:
            with Image.open(io.BytesIO(source.payload)) as image:
                if image.size != (entry["width"], entry["height"]):
                    raise SpongeComponentError(error)
                rgb = Image.alpha_composite(Image.new("RGBA", image.size, "white"), image.convert("RGBA")).convert("RGB").tobytes()
            raw = base64.b64decode(entry["ownership_rle"], validate=True)
        except (ValueError, binascii.Error, OSError) as exc:
            raise SpongeComponentError(error) from exc
        if not raw or len(raw) % 5:
            raise SpongeComponentError(error)
        total = 0
        owners = set()
        pixels = bytearray()
        maximum = entry["width"] * entry["height"]
        for count, owner in struct.iter_unpack("<IB", raw):
            if not count or owner > len(entry["parts"]):
                raise SpongeComponentError(error)
            total += count
            if total > maximum:
                raise SpongeComponentError(error)
            if owner:
                owners.add(owner)
            pixels.extend(bytes((owner,))*count)
        if total != maximum or owners != set(range(1, len(entry["parts"]) + 1)):
            raise SpongeComponentError(error)
        visible_owners = set()
        for pixel, owner in enumerate(pixels):
            if min(rgb[pixel*3:pixel*3+3]) < 252:
                if not owner:
                    raise SpongeComponentError("comic semantic partition omits visible source pixels")
                visible_owners.add(owner)
        if visible_owners != owners:
            raise SpongeComponentError("comic semantic partition contains an empty visible part")
    return value["scenes"]
