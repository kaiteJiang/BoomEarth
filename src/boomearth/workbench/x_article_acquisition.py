"""Plan and execute one approved, private X Article acquisition transaction."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from boomearth.providers.x_article import (
    IMAGE_MAX_BYTES,
    XArticleProviderError,
    XArticleTransport,
    article_json_value,
    normalize_x_article_url,
    parse_x_article_html,
    render_article_markdown,
)
from boomearth.workbench.source_artifacts import (
    ArtifactRecord,
    SourceContractError,
    canonical_json_bytes,
    load_exact_json,
    publish_json_exclusive,
    sha256_file,
    verify_approval_payload,
    verify_private_relative,
)
from boomearth.workbench.source_intake import load_work_order
from boomearth.workbench.paths import WorkbenchPaths
from boomearth.workbench.source_ledger import (
    SourceLedgerError,
    StageEvent,
    WashEventLedger,
    event_sha256,
)


PROVIDER = "x-public-relay-html"
STANDARD_PROFILE = "standard"
HIGH_MEDIA_PROFILE = "high-media"
DEFAULT_ACTION = "x-article-acquisition"
HIGH_MEDIA_ACTION = "x-article-acquisition-high-media"
PAGE_REQUEST_BUDGET = 1
IMAGE_REQUEST_BUDGET = 20
TOTAL_REQUEST_BUDGET = 21
TOTAL_IMAGE_BYTES = 150 * 1024 * 1024
RECOVERY_MANIFEST = ".x-article-manifest.pending.json"
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "work_id",
        "provider",
        "source_input_sha256",
        "article_json_sha256",
        "article_markdown_sha256",
        "assets",
    }
)
_MANIFEST_ASSET_KEYS = frozenset(
    {
        "asset_id",
        "relative_path",
        "sha256",
        "bytes",
        "width",
        "height",
        "format",
    }
)
_PLAN_KEYS = frozenset(
    {
        "schema_version",
        "work_id",
        "provider",
        "action",
        "input_sha256",
        "network_required",
        "fee_possible",
        "page_request_budget",
        "image_request_budget",
        "total_request_budget",
        "follow_redirects",
        "no_retry",
        "no_fallback",
    }
)


class XArticleAcquisitionError(RuntimeError):
    """A fixed-message acquisition error that never renders source data."""

    def __repr__(self) -> str:
        return "XArticleAcquisitionError(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class XArticleActionPlan:
    schema_version: int
    work_id: str
    provider: str
    action: str
    input_sha256: str
    network_required: bool
    fee_possible: bool
    page_request_budget: int
    image_request_budget: int
    total_request_budget: int
    follow_redirects: bool
    no_retry: bool
    no_fallback: bool

    def __repr__(self) -> str:
        return "XArticleActionPlan(<redacted>)"


@dataclass(frozen=True, slots=True)
class _ProfileSpec:
    action: str
    image_request_budget: int
    total_request_budget: int


_PROFILE_SPECS = {
    STANDARD_PROFILE: _ProfileSpec(
        DEFAULT_ACTION,
        IMAGE_REQUEST_BUDGET,
        TOTAL_REQUEST_BUDGET,
    ),
    HIGH_MEDIA_PROFILE: _ProfileSpec(HIGH_MEDIA_ACTION, 60, 61),
}
_PROFILE_BY_ACTION = {spec.action: spec for spec in _PROFILE_SPECS.values()}


def _plan_value(plan: XArticleActionPlan) -> dict[str, object]:
    return {
        "action": plan.action,
        "fee_possible": plan.fee_possible,
        "follow_redirects": plan.follow_redirects,
        "image_request_budget": plan.image_request_budget,
        "input_sha256": plan.input_sha256,
        "network_required": plan.network_required,
        "no_fallback": plan.no_fallback,
        "no_retry": plan.no_retry,
        "page_request_budget": plan.page_request_budget,
        "provider": plan.provider,
        "schema_version": plan.schema_version,
        "total_request_budget": plan.total_request_budget,
        "work_id": plan.work_id,
    }


def _verified_input(root: Path, work_id: str) -> tuple[Path, str, Path]:
    try:
        order = load_work_order(root, work_id)
        current = WashEventLedger(root).current(work_id)
        if (
            order.source_kind != "x-article"
            or current.source_kind != "x-article"
            or current.stage != "source_registered"
        ):
            raise ValueError
        source_input = verify_private_relative(order.private_root, "source-input.txt")
        digest = sha256_file(source_input)
        if digest != order.source_input_sha256:
            raise ValueError
        lines = source_input.read_text(encoding="utf-8").splitlines()
        if len(lines) != 1:
            raise ValueError
        normalize_x_article_url(lines[0])
        return source_input, digest, order.private_root
    except (SourceContractError, SourceLedgerError, XArticleProviderError, OSError, UnicodeError, ValueError):
        raise XArticleAcquisitionError("x-article-input-invalid") from None


def plan_x_article_acquisition(
    root: Path,
    work_id: str,
    *,
    profile: str = STANDARD_PROFILE,
) -> XArticleActionPlan:
    """Publish the immutable, offline acquisition plan."""

    try:
        profile_spec = _PROFILE_SPECS[profile]
    except KeyError:
        raise XArticleAcquisitionError("x-article-profile-invalid") from None
    _, digest, private_root = _verified_input(Path(root), work_id)
    plan = XArticleActionPlan(
        schema_version=1,
        work_id=work_id,
        provider=PROVIDER,
        action=profile_spec.action,
        input_sha256=digest,
        network_required=True,
        fee_possible=False,
        page_request_budget=PAGE_REQUEST_BUDGET,
        image_request_budget=profile_spec.image_request_budget,
        total_request_budget=profile_spec.total_request_budget,
        follow_redirects=False,
        no_retry=True,
        no_fallback=True,
    )
    try:
        target = verify_private_relative(private_root, "x-article-acquisition-plan.json")
        publish_json_exclusive(private_root, target, _plan_value(plan))
        return plan
    except SourceContractError:
        raise XArticleAcquisitionError("x-article-publication-failed") from None


def _load_approved_plan(
    root: Path, work_id: str, approval: Path
) -> tuple[XArticleActionPlan, Path, Path]:
    try:
        current = WashEventLedger(root).current(work_id)
        if (
            current.source_kind != "x-article"
            or current.stage != "source_registered"
        ):
            raise SourceContractError("approval-scope-mismatch")
        private_root = WorkbenchPaths(root).private_source(work_id)
        source_input = verify_private_relative(private_root, "source-input.txt")
        plan_path = verify_private_relative(private_root, "x-article-acquisition-plan.json")
        value = load_exact_json(plan_path, _PLAN_KEYS)
        action = value["action"]
        if not isinstance(action, str):
            raise SourceContractError("action-plan-invalid")
        try:
            profile_spec = _PROFILE_BY_ACTION[action]
        except KeyError:
            raise SourceContractError("action-plan-invalid") from None
        expected = XArticleActionPlan(
            schema_version=1,
            work_id=work_id,
            provider=PROVIDER,
            action=profile_spec.action,
            input_sha256=value["input_sha256"],  # type: ignore[arg-type]
            network_required=True,
            fee_possible=False,
            page_request_budget=PAGE_REQUEST_BUDGET,
            image_request_budget=profile_spec.image_request_budget,
            total_request_budget=profile_spec.total_request_budget,
            follow_redirects=False,
            no_retry=True,
            no_fallback=True,
        )
        if value != _plan_value(expected):
            raise SourceContractError("action-plan-invalid")
        if expected.input_sha256 != current.source_id:
            raise SourceContractError("action-plan-invalid")
        verify_approval_payload(
            value,
            approval,
            request_count=profile_spec.total_request_budget,
        )
        digest = sha256_file(source_input)
    except (SourceContractError, SourceLedgerError, OSError, TypeError, ValueError):
        raise XArticleAcquisitionError("x-article-not-approved") from None
    if digest != expected.input_sha256:
        raise XArticleAcquisitionError("x-article-input-changed")
    return expected, source_input, private_root


def _write_exclusive(path: Path, payload: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        raise XArticleAcquisitionError("x-article-publication-failed") from None


def _timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _validated_recovery_manifest(
    private_root: Path,
    path: Path,
    *,
    work_id: str,
    input_sha256: str,
    image_request_budget: int,
) -> dict[str, object]:
    try:
        value = load_exact_json(path, _MANIFEST_KEYS)
        if (
            value["schema_version"] != 1
            or value["work_id"] != work_id
            or value["provider"] != PROVIDER
            or value["source_input_sha256"] != input_sha256
            or not isinstance(value["article_json_sha256"], str)
            or not isinstance(value["article_markdown_sha256"], str)
            or not isinstance(value["assets"], list)
            or len(value["assets"]) > image_request_budget
        ):
            raise ValueError
        article_json = verify_private_relative(
            private_root, "article-source/article.json"
        )
        article_markdown = verify_private_relative(
            private_root, "article-source/article.md"
        )
        if (
            sha256_file(article_json) != value["article_json_sha256"]
            or sha256_file(article_markdown) != value["article_markdown_sha256"]
        ):
            raise ValueError
        seen_assets: set[str] = set()
        total_image_bytes = 0
        for raw in value["assets"]:
            if not isinstance(raw, dict) or set(raw) != _MANIFEST_ASSET_KEYS:
                raise ValueError
            asset_id = raw["asset_id"]
            relative = raw["relative_path"]
            if (
                not isinstance(asset_id, str)
                or asset_id in seen_assets
                or not isinstance(relative, str)
                or relative
                != f"article-source/images/{asset_id}."
                + ("jpg" if raw["format"] == "jpeg" else str(raw["format"]))
                or type(raw["bytes"]) is not int
                or type(raw["width"]) is not int
                or type(raw["height"]) is not int
                or raw["bytes"] <= 0
                or raw["bytes"] > IMAGE_MAX_BYTES
                or raw["width"] <= 0
                or raw["height"] <= 0
                or raw["format"] not in {"jpeg", "png", "webp"}
                or not isinstance(raw["sha256"], str)
            ):
                raise ValueError
            asset = verify_private_relative(private_root, relative)
            if asset.stat().st_size != raw["bytes"] or sha256_file(asset) != raw["sha256"]:
                raise ValueError
            total_image_bytes += raw["bytes"]
            if total_image_bytes > TOTAL_IMAGE_BYTES:
                raise ValueError
            seen_assets.add(asset_id)
        return value
    except (KeyError, OSError, SourceContractError, TypeError, ValueError):
        raise XArticleAcquisitionError("x-article-publication-failed") from None


def _recover_interrupted_publication(
    root: Path,
    plan: XArticleActionPlan,
    private_root: Path,
    formal_article: Path,
    formal_manifest: Path,
) -> ArtifactRecord | None:
    article_exists = formal_article.exists() or formal_article.is_symlink()
    manifest_exists = formal_manifest.exists() or formal_manifest.is_symlink()
    if not article_exists and not manifest_exists:
        return None
    if not article_exists or formal_article.is_symlink() or not formal_article.is_dir():
        raise XArticleAcquisitionError("x-article-publication-failed")
    pending = formal_article / RECOVERY_MANIFEST
    pending_exists = pending.exists() or pending.is_symlink()
    if not pending_exists and not manifest_exists:
        raise XArticleAcquisitionError("x-article-publication-failed")
    source = pending if pending_exists else formal_manifest
    value = _validated_recovery_manifest(
        private_root,
        source,
        work_id=plan.work_id,
        input_sha256=plan.input_sha256,
        image_request_budget=plan.image_request_budget,
    )
    manifest_accepted = False
    if manifest_exists:
        formal_value = _validated_recovery_manifest(
            private_root,
            formal_manifest,
            work_id=plan.work_id,
            input_sha256=plan.input_sha256,
            image_request_budget=plan.image_request_budget,
        )
        if canonical_json_bytes(formal_value) != canonical_json_bytes(value):
            raise XArticleAcquisitionError("x-article-publication-failed")
        manifest_sha = sha256_file(formal_manifest)
        manifest_accepted = True
    try:
        if not manifest_exists:
            manifest_sha = publish_json_exclusive(
                private_root, formal_manifest, value
            )
            manifest_accepted = True
        manifest_size = formal_manifest.stat().st_size
        if pending_exists:
            pending.unlink()
        current = WashEventLedger(root).current(plan.work_id)
        WashEventLedger(root).append(
            StageEvent(
                schema_version=2,
                work_id=plan.work_id,
                source_id=plan.input_sha256,
                source_kind="x-article",
                stage="article_ready",
                result="ok",
                artifact_label="x-article-manifest",
                artifact_sha256=manifest_sha,
                timestamp=_timestamp(),
                previous_event_sha256=event_sha256(current),
            )
        )
    except (OSError, SourceContractError, SourceLedgerError):
        if manifest_accepted:
            if not pending.exists() and not pending.is_symlink():
                try:
                    _write_exclusive(pending, canonical_json_bytes(value))
                except XArticleAcquisitionError:
                    pass
            try:
                formal_manifest.unlink(missing_ok=True)
            except OSError:
                pass
        raise XArticleAcquisitionError("x-article-publication-failed") from None
    return ArtifactRecord(
        relative_path="x-article-manifest.json",
        sha256=manifest_sha,
        size_bytes=manifest_size,
        path=formal_manifest,
    )


def run_x_article_acquisition(
    root: Path,
    work_id: str,
    approval: Path,
    *,
    transport: XArticleTransport | None = None,
) -> ArtifactRecord:
    """Execute exactly one approved page request and its bounded image requests."""

    plan, source_input, private_root = _load_approved_plan(
        Path(root), work_id, Path(approval)
    )
    formal_article = verify_private_relative(private_root, "article-source")
    formal_manifest = verify_private_relative(private_root, "x-article-manifest.json")
    recovered = _recover_interrupted_publication(
        Path(root), plan, private_root, formal_article, formal_manifest
    )
    if recovered is not None:
        return recovered
    try:
        lines = source_input.read_text(encoding="utf-8").splitlines()
        if sha256_file(source_input) != plan.input_sha256:
            raise XArticleAcquisitionError("x-article-input-changed")
        if len(lines) != 1:
            raise XArticleAcquisitionError("x-article-input-changed")
        canonical_url = normalize_x_article_url(lines[0])
    except XArticleAcquisitionError:
        raise
    except (SourceContractError, XArticleProviderError, OSError, UnicodeError):
        raise XArticleAcquisitionError("x-article-input-changed") from None

    staging: Path | None = None
    article_published = False
    manifest_published = False
    completed = False
    owned_client: httpx.Client | None = None
    try:
        if transport is None:
            owned_client = httpx.Client(
                timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False
            )
            active_transport = XArticleTransport(owned_client)
        else:
            active_transport = transport
        page = active_transport.fetch_page(canonical_url)
        document = parse_x_article_html(page, canonical_url)
        if len(document.assets) > plan.image_request_budget:
            raise XArticleAcquisitionError("x-article-request-budget-exceeded")

        staging = Path(tempfile.mkdtemp(prefix=".x-article-staging-", dir=private_root))
        article_staging = staging / "article-source"
        image_dir = article_staging / "images"
        image_dir.mkdir(parents=True)
        asset_paths: dict[str, str] = {}
        asset_rows: list[dict[str, object]] = []
        digest_to_path: dict[str, tuple[str, str]] = {}
        total_image_bytes = 0
        for asset in document.assets:
            image = active_transport.fetch_image(asset.remote_url)
            total_image_bytes += len(image.payload)
            if total_image_bytes > TOTAL_IMAGE_BYTES:
                raise XArticleAcquisitionError("x-article-request-budget-exceeded")
            digest = hashlib.sha256(image.payload).hexdigest()
            existing = digest_to_path.get(digest)
            if existing is not None:
                asset_paths[asset.key] = existing[0]
                continue
            number = len(asset_rows) + 1
            extension = "jpg" if image.format == "jpeg" else image.format
            asset_id = f"image-{number:03d}"
            local_relative = f"images/{asset_id}.{extension}"
            manifest_relative = f"article-source/{local_relative}"
            _write_exclusive(article_staging / local_relative, image.payload)
            asset_paths[asset.key] = local_relative
            digest_to_path[digest] = (local_relative, asset_id)
            asset_rows.append(
                {
                    "asset_id": asset_id,
                    "relative_path": manifest_relative,
                    "sha256": digest,
                    "bytes": len(image.payload),
                    "width": image.width,
                    "height": image.height,
                    "format": image.format,
                }
            )

        article_value = article_json_value(document, asset_paths)
        article_json = canonical_json_bytes(article_value)
        article_markdown = render_article_markdown(article_value)
        _write_exclusive(article_staging / "article.json", article_json)
        _write_exclusive(article_staging / "article.md", article_markdown)
        manifest_value: dict[str, object] = {
            "schema_version": 1,
            "work_id": work_id,
            "provider": PROVIDER,
            "source_input_sha256": plan.input_sha256,
            "article_json_sha256": hashlib.sha256(article_json).hexdigest(),
            "article_markdown_sha256": hashlib.sha256(article_markdown).hexdigest(),
            "assets": asset_rows,
        }
        _write_exclusive(
            article_staging / RECOVERY_MANIFEST,
            canonical_json_bytes(manifest_value),
        )
        if sha256_file(source_input) != plan.input_sha256:
            raise XArticleAcquisitionError("x-article-input-changed")
        if formal_article.exists() or formal_manifest.exists():
            raise XArticleAcquisitionError("x-article-publication-failed")
        article_staging.rename(formal_article)
        article_published = True
        manifest_sha = publish_json_exclusive(
            private_root, formal_manifest, manifest_value
        )
        manifest_published = True
        (formal_article / RECOVERY_MANIFEST).unlink()
        current = WashEventLedger(root).current(work_id)
        WashEventLedger(root).append(
            StageEvent(
                schema_version=2,
                work_id=work_id,
                source_id=plan.input_sha256,
                source_kind="x-article",
                stage="article_ready",
                result="ok",
                artifact_label="x-article-manifest",
                artifact_sha256=manifest_sha,
                timestamp=_timestamp(),
                previous_event_sha256=event_sha256(current),
            )
        )
        completed = True
        return ArtifactRecord(
            relative_path="x-article-manifest.json",
            sha256=manifest_sha,
            size_bytes=formal_manifest.stat().st_size,
            path=formal_manifest,
        )
    except XArticleAcquisitionError:
        raise
    except XArticleProviderError as error:
        code = str(error)
        allowed = {
            "x-article-page-unavailable",
            "x-article-response-invalid",
            "x-article-structure-unsupported",
            "x-article-body-missing",
            "x-article-image-invalid",
        }
        raise XArticleAcquisitionError(code if code in allowed else "x-article-response-invalid") from None
    except (SourceContractError, SourceLedgerError, OSError, TypeError, ValueError):
        raise XArticleAcquisitionError("x-article-publication-failed") from None
    finally:
        if owned_client is not None:
            owned_client.close()
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        if manifest_published and not completed:
            try:
                formal_manifest.unlink(missing_ok=True)
            except OSError:
                pass
        if article_published and not completed:
            shutil.rmtree(formal_article, ignore_errors=True)


__all__ = [
    "HIGH_MEDIA_PROFILE",
    "STANDARD_PROFILE",
    "XArticleAcquisitionError",
    "XArticleActionPlan",
    "plan_x_article_acquisition",
    "run_x_article_acquisition",
]
