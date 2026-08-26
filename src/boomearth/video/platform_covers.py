from __future__ import annotations

from dataclasses import dataclass
import hashlib
from itertools import combinations
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import unicodedata

from PIL import (
    Image,
    ImageDraw,
    ImageEnhance,
    ImageFilter,
    ImageFont,
    ImageOps,
    UnidentifiedImageError,
)

from boomearth.workbench.handoff import validate_public_handoff
from boomearth.video.cover_contracts import LEGACY_PLATFORM_COVER_CONTRACT


COVER_CONTRACT = LEGACY_PLATFORM_COVER_CONTRACT
COVER_IMAGE_DIMENSIONS: dict[str, tuple[int, int]] = {
    "封面/抖音-作品封面-1080x1920.png": (1080, 1920),
    "封面/抖音-主页预览-1080x1440.png": (1080, 1440),
    "封面/视频号-作品封面-1080x1260.png": (1080, 1260),
    "质检/cover-contact-sheet.jpg": (960, 640),
}
COVER_SAFE_BOXES: dict[str, tuple[int, int, int, int]] = {
    "封面/抖音-作品封面-1080x1920.png": (72, 300, 1008, 1620),
    "封面/抖音-主页预览-1080x1440.png": (72, 60, 1008, 1380),
    "封面/视频号-作品封面-1080x1260.png": (72, 60, 1008, 1200),
}
COVER_QC_RELATIVE = "质检/cover-qc.json"
COVER_ARTIFACT_RELATIVES = (*COVER_IMAGE_DIMENSIONS, COVER_QC_RELATIVE)
_QC_KEYS = {
    "schema_version",
    "status",
    "contract",
    "source_sha256",
    "headline_sha256",
    "font_sha256",
    "font_size_px",
    "headline_line_count",
    "dimensions",
    "safe_boxes",
    "artifacts_sha256",
}
_FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/msyhbd.ttc"),
    Path("C:/Windows/Fonts/simhei.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/System/Library/Fonts/PingFang.ttc"),
)


class PlatformCoverError(RuntimeError):
    """A fixed, redacted local cover composition failure."""


@dataclass(frozen=True, slots=True)
class CoverRenderResult:
    status: str
    artifacts: tuple[Path, ...]
    reused: bool


def cover_artifact_paths(project_root: Path) -> tuple[Path, ...]:
    root = Path(project_root).absolute()
    return tuple(root / Path(relative) for relative in COVER_ARTIFACT_RELATIVES)


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return path.is_symlink()
    return path.is_symlink() or bool(attributes & 0x0400)


def _safe_file(path: Path) -> bool:
    try:
        return not _is_reparse_point(path) and stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _safe_directory(path: Path) -> bool:
    try:
        return not _is_reparse_point(path) and stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


def _has_safe_ancestors(path: Path) -> bool:
    current = path
    while True:
        if (current.exists() or _is_reparse_point(current)) and _is_reparse_point(
            current
        ):
            return False
        if current.parent == current:
            return True
        current = current.parent


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate cover QC key")
        result[key] = value
    return result


def _project_root(value: Path) -> Path:
    candidate = Path(value)
    if ".." in candidate.parts:
        raise PlatformCoverError("cover-project-invalid")
    root = candidate.absolute()
    if not _safe_directory(root) or not _has_safe_ancestors(root):
        raise PlatformCoverError("cover-project-invalid")
    return root


def _strict_project_image(root: Path, value: Path) -> Path:
    supplied = Path(value)
    if ".." in supplied.parts:
        raise PlatformCoverError("cover-source-invalid")
    path = (
        supplied.absolute()
        if supplied.is_absolute()
        else (root / supplied).absolute()
    )
    try:
        lexical = path.relative_to(root)
    except ValueError:
        raise PlatformCoverError("cover-source-invalid") from None
    if not lexical.parts or not _safe_file(path):
        raise PlatformCoverError("cover-source-invalid")
    current = path
    while current != root:
        if _is_reparse_point(current):
            raise PlatformCoverError("cover-source-invalid")
        current = current.parent
    try:
        resolved = path.resolve(strict=True)
        relative = resolved.relative_to(root.resolve(strict=True))
    except (OSError, RuntimeError, ValueError):
        raise PlatformCoverError("cover-source-invalid") from None
    if not relative.parts:
        raise PlatformCoverError("cover-source-invalid")
    return path


def _headline(value: str) -> str:
    if not isinstance(value, str):
        raise PlatformCoverError("cover-headline-invalid")
    normalized = re.sub(r" {2,}", " ", value.strip())
    if (
        len(normalized) < 2
        or len(normalized) > 42
        or len(normalized.encode("utf-8")) > 180
        or any(unicodedata.category(character).startswith("C") for character in normalized)
        or re.search(r"(?:https?://|www\.)", normalized, flags=re.IGNORECASE)
        or validate_public_handoff(normalized)
    ):
        raise PlatformCoverError("cover-headline-invalid")
    return normalized


def _font_supports_chinese(font: ImageFont.FreeTypeFont) -> bool:
    samples = []
    for character in ("中", "文"):
        mask = font.getmask(character)
        samples.append((mask.size, hashlib.sha256(bytes(mask)).digest()))
    return all(
        width > 0 and height > 0 for (width, height), _digest in samples
    ) and samples[0] != samples[1]


def _load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    try:
        font = ImageFont.truetype(str(path), size=size)
    except (OSError, TypeError, ValueError):
        raise PlatformCoverError("cover-font-invalid") from None
    if not _font_supports_chinese(font):
        raise PlatformCoverError("cover-font-invalid")
    return font


def _resolve_font(value: Path | None) -> tuple[Path, str]:
    candidates = (Path(value),) if value is not None else _FONT_CANDIDATES
    for supplied in candidates:
        if ".." in supplied.parts:
            continue
        path = supplied.absolute()
        if not _safe_file(path):
            continue
        try:
            _load_font(path, 64)
            return path, _sha256(path)
        except (OSError, PlatformCoverError):
            continue
    raise PlatformCoverError("cover-font-invalid")


def _load_source(path: Path) -> Image.Image:
    try:
        with Image.open(path) as opened:
            opened.load()
            if opened.width < 64 or opened.height < 64 or max(opened.size) > 16_384:
                raise PlatformCoverError("cover-source-invalid")
            return ImageOps.exif_transpose(opened).convert("RGB")
    except PlatformCoverError:
        raise
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        raise PlatformCoverError("cover-source-invalid") from None


def _text_size(font: ImageFont.FreeTypeFont, text: str) -> tuple[int, int]:
    left, top, right, bottom = font.getbbox(text, stroke_width=3)
    return right - left, bottom - top


def _fit_headline(
    text: str, font_path: Path, *, maximum_width: int = 860, maximum_height: int = 390
) -> tuple[ImageFont.FreeTypeFont, tuple[str, ...], int]:
    for size in range(112, 63, -4):
        font = _load_font(font_path, size)
        for line_count in (2, 3):
            candidates: list[tuple[float, tuple[str, ...]]] = []
            for cuts in combinations(range(1, len(text)), line_count - 1):
                boundaries = (0, *cuts, len(text))
                lines = tuple(
                    text[boundaries[index] : boundaries[index + 1]].strip()
                    for index in range(line_count)
                )
                if any(not line for line in lines):
                    continue
                sizes = [_text_size(font, line) for line in lines]
                widths = [width for width, _height in sizes]
                if max(widths) > maximum_width:
                    continue
                heights = [height for _width, height in sizes]
                if sum(heights) + 22 * (line_count - 1) > maximum_height:
                    continue
                average = sum(widths) / line_count
                score = sum(abs(width - average) for width in widths)
                candidates.append((score, lines))
            if candidates:
                return font, min(candidates, key=lambda item: item[0])[1], size
    raise PlatformCoverError("cover-headline-invalid")


def _rounded_panel(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    contained = ImageOps.contain(image, size, Image.Resampling.LANCZOS)
    panel = Image.new("RGB", size, (12, 20, 29))
    x = (size[0] - contained.width) // 2
    y = (size[1] - contained.height) // 2
    panel.paste(contained, (x, y))
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size[0] - 1, size[1] - 1), radius=34, fill=255
    )
    panel.putalpha(mask)
    return panel


def _render_upload(
    source: Image.Image, font: ImageFont.FreeTypeFont, lines: tuple[str, ...]
) -> Image.Image:
    background = ImageOps.fit(source, (1080, 1920), Image.Resampling.LANCZOS)
    background = (
        ImageEnhance.Color(background)
        .enhance(0.72)
        .filter(ImageFilter.GaussianBlur(30))
    )
    canvas = background.convert("RGBA")
    canvas.alpha_composite(Image.new("RGBA", canvas.size, (4, 10, 18, 158)))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (72, 300, 1008, 1620),
        radius=52,
        fill=(4, 12, 22, 102),
        outline=(255, 205, 62, 190),
        width=3,
    )
    badge_font = font.font_variant(size=34)
    draw.rounded_rectangle((90, 330, 392, 392), radius=28, fill=(255, 205, 62, 235))
    draw.text(
        (241, 361),
        "BOOMEARTH",
        font=badge_font,
        fill=(17, 28, 40, 255),
        anchor="mm",
    )

    headline_top, headline_bottom = 420, 790
    heights = [_text_size(font, line)[1] for line in lines]
    total_height = sum(heights) + 22 * (len(lines) - 1)
    y = headline_top + (headline_bottom - headline_top - total_height) // 2
    for line, height in zip(lines, heights):
        draw.text(
            (540, y),
            line,
            font=font,
            fill=(255, 255, 250, 255),
            stroke_width=3,
            stroke_fill=(5, 9, 14, 230),
            anchor="ma",
        )
        y += height + 22

    panel = _rounded_panel(source, (840, 650))
    canvas.alpha_composite(panel, (120, 850))
    draw.rounded_rectangle(
        (118, 848, 962, 1502),
        radius=36,
        outline=(255, 255, 255, 180),
        width=4,
    )
    return canvas.convert("RGB")


def _render_contact_sheet(
    images: tuple[Image.Image, ...], font_path: Path
) -> Image.Image:
    sheet = Image.new("RGB", (960, 640), (235, 232, 222))
    draw = ImageDraw.Draw(sheet)
    label_font = _load_font(font_path, 30)
    labels = ("抖音作品", "抖音主页", "视频号作品")
    for index, (image, label) in enumerate(zip(images, labels)):
        left = 20 + index * 315
        draw.text(
            (left + 145, 35),
            label,
            font=label_font,
            fill=(17, 30, 43),
            anchor="mm",
        )
        preview = ImageOps.contain(image, (285, 535), Image.Resampling.LANCZOS)
        x = left + (285 - preview.width) // 2
        y = 82 + (535 - preview.height) // 2
        draw.rounded_rectangle(
            (left - 2, 78, left + 287, 621), radius=16, fill=(16, 25, 35)
        )
        sheet.paste(preview, (x, y))
    return sheet


def _decoded_size(path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as image:
            image.load()
            return image.size
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return None


def _reusable(
    root: Path, *, source_sha256: str, headline_sha256: str, font_sha256: str
) -> bool:
    artifacts = cover_artifact_paths(root)
    if not all(_safe_file(path) for path in artifacts):
        return False
    try:
        qc = json.loads(
            (root / COVER_QC_RELATIVE).read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return False
    expected_dimensions = {
        key: list(value) for key, value in COVER_IMAGE_DIMENSIONS.items()
    }
    expected_safe_boxes = {key: list(value) for key, value in COVER_SAFE_BOXES.items()}
    if (
        not isinstance(qc, dict)
        or set(qc) != _QC_KEYS
        or qc.get("schema_version") != 1
        or qc.get("status") != "pass"
        or qc.get("contract") != COVER_CONTRACT
        or qc.get("source_sha256") != source_sha256
        or qc.get("headline_sha256") != headline_sha256
        or qc.get("font_sha256") != font_sha256
        or qc.get("dimensions") != expected_dimensions
        or qc.get("safe_boxes") != expected_safe_boxes
        or type(qc.get("font_size_px")) is not int
        or qc["font_size_px"] not in range(64, 113, 4)
        or qc.get("headline_line_count") not in {2, 3}
        or not isinstance(qc.get("artifacts_sha256"), dict)
        or set(qc["artifacts_sha256"]) != set(COVER_IMAGE_DIMENSIONS)
    ):
        return False
    hashes = qc["artifacts_sha256"]
    for relative, dimensions in COVER_IMAGE_DIMENSIONS.items():
        path = root / relative
        if _decoded_size(path) != dimensions or hashes.get(relative) != _sha256(path):
            return False
    try:
        with Image.open(root / "封面/抖音-作品封面-1080x1920.png") as upload, Image.open(
            root / "封面/抖音-主页预览-1080x1440.png"
        ) as preview:
            upload.load()
            preview.load()
            if upload.mode not in {"RGB", "RGBA"} or preview.mode != upload.mode:
                return False
            decoded_upload = upload.crop((0, 240, 1080, 1680)).convert(upload.mode)
            decoded_preview = preview.convert(upload.mode)
            if (
                decoded_upload.tobytes()
                != decoded_preview.tobytes()
            ):
                return False
    except (OSError, ValueError, UnidentifiedImageError):
        return False
    return True


def _prepare_output_directories(root: Path) -> None:
    for path in (root / "封面", root / "质检"):
        if path.exists() or _is_reparse_point(path):
            if not _safe_directory(path):
                raise PlatformCoverError("cover-output-invalid")
        else:
            try:
                path.mkdir()
            except OSError:
                raise PlatformCoverError("cover-output-invalid") from None
            if not _safe_directory(path):
                raise PlatformCoverError("cover-output-invalid")


def _publish_staged(root: Path, staged_root: Path) -> None:
    published: list[tuple[Path, Path]] = []
    try:
        for relative in COVER_ARTIFACT_RELATIVES:
            staged = staged_root / relative
            target = root / relative
            if not _safe_directory(target.parent) or not _has_safe_ancestors(
                target.parent
            ):
                raise PlatformCoverError("cover-output-invalid")
            if target.exists() or _is_reparse_point(target):
                raise PlatformCoverError("cover-output-exists")
            os.link(staged, target)
            published.append((target, staged))
            try:
                target.resolve(strict=True).relative_to(root.resolve(strict=True))
            except (OSError, RuntimeError, ValueError):
                raise PlatformCoverError("cover-output-invalid") from None
            if not _safe_file(target) or _sha256(target) != _sha256(staged):
                raise PlatformCoverError("cover-output-invalid")
    except PlatformCoverError:
        for target, staged in reversed(published):
            try:
                if target.exists() and os.path.samefile(target, staged):
                    target.unlink()
            except OSError:
                pass
        raise
    except OSError:
        for target, staged in reversed(published):
            try:
                if target.exists() and os.path.samefile(target, staged):
                    target.unlink()
            except OSError:
                pass
        raise PlatformCoverError("cover-output-invalid") from None


def render_platform_covers(
    project_root: Path,
    source_image: Path,
    headline: str,
    font_path: Path | None = None,
) -> CoverRenderResult:
    root = _project_root(project_root)
    normalized_headline = _headline(headline)
    source_path = _strict_project_image(root, source_image)
    resolved_font, font_sha256 = _resolve_font(font_path)
    source_sha256 = _sha256(source_path)
    headline_sha256 = hashlib.sha256(normalized_headline.encode("utf-8")).hexdigest()
    _prepare_output_directories(root)
    outputs = cover_artifact_paths(root)
    if any(path.exists() or _is_reparse_point(path) for path in outputs):
        if _reusable(
            root,
            source_sha256=source_sha256,
            headline_sha256=headline_sha256,
            font_sha256=font_sha256,
        ):
            return CoverRenderResult("pass", outputs, True)
        if any(_is_reparse_point(path) for path in outputs):
            raise PlatformCoverError("cover-output-invalid")
        raise PlatformCoverError("cover-output-exists")

    source = _load_source(source_path)
    font, lines, font_size = _fit_headline(normalized_headline, resolved_font)
    upload = _render_upload(source, font, lines)
    preview = upload.crop((0, 240, 1080, 1680))
    channels = upload.crop((0, 330, 1080, 1590))
    contact = _render_contact_sheet((upload, preview, channels), resolved_font)
    try:
        with tempfile.TemporaryDirectory(prefix=".cover-render-", dir=root) as value:
            staged_root = Path(value)
            staged_images = {
                "封面/抖音-作品封面-1080x1920.png": upload,
                "封面/抖音-主页预览-1080x1440.png": preview,
                "封面/视频号-作品封面-1080x1260.png": channels,
                "质检/cover-contact-sheet.jpg": contact,
            }
            for relative, image in staged_images.items():
                path = staged_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.suffix.casefold() == ".jpg":
                    image.save(path, quality=92, subsampling=0, optimize=False)
                else:
                    image.save(path, optimize=False, compress_level=6)
            artifact_hashes = {
                relative: _sha256(staged_root / relative)
                for relative in COVER_IMAGE_DIMENSIONS
            }
            qc = {
                "schema_version": 1,
                "status": "pass",
                "contract": COVER_CONTRACT,
                "source_sha256": source_sha256,
                "headline_sha256": headline_sha256,
                "font_sha256": font_sha256,
                "font_size_px": font_size,
                "headline_line_count": len(lines),
                "dimensions": {
                    key: list(value) for key, value in COVER_IMAGE_DIMENSIONS.items()
                },
                "safe_boxes": {
                    key: list(value) for key, value in COVER_SAFE_BOXES.items()
                },
                "artifacts_sha256": artifact_hashes,
            }
            qc_path = staged_root / COVER_QC_RELATIVE
            qc_path.write_text(
                json.dumps(qc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n",
                encoding="utf-8",
            )
            if (
                _sha256(source_path) != source_sha256
                or _sha256(resolved_font) != font_sha256
            ):
                raise PlatformCoverError("cover-input-changed")
            _publish_staged(root, staged_root)
    except PlatformCoverError:
        raise
    except (OSError, ValueError):
        raise PlatformCoverError("cover-render-failed") from None
    if not _reusable(
        root,
        source_sha256=source_sha256,
        headline_sha256=headline_sha256,
        font_sha256=font_sha256,
    ):
        raise PlatformCoverError("cover-render-failed")
    return CoverRenderResult("pass", outputs, False)


__all__ = (
    "COVER_ARTIFACT_RELATIVES",
    "COVER_CONTRACT",
    "COVER_IMAGE_DIMENSIONS",
    "COVER_QC_RELATIVE",
    "COVER_SAFE_BOXES",
    "CoverRenderResult",
    "PlatformCoverError",
    "cover_artifact_paths",
    "render_platform_covers",
)
