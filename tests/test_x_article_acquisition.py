from __future__ import annotations

import importlib.util
import json
import shutil
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image

from boomearth.providers.x_article import (
    XArticleAssetRef,
    XArticleBlock,
    XArticleDocument,
    XArticleImage,
    XArticleProviderError,
)
from boomearth.workbench.source_artifacts import (
    canonical_json_bytes,
    publish_json_exclusive,
    sha256_file,
)
from boomearth.workbench.source_intake import create_x_article_intake
from boomearth.workbench.source_ledger import WashEventLedger
from boomearth.workbench.source_ledger import SourceLedgerError
import boomearth.workbench.x_article_acquisition as acquisition_module
from boomearth.workbench.x_article_acquisition import (
    XArticleAcquisitionError,
    plan_x_article_acquisition,
    run_x_article_acquisition,
)


WORK_ID = "00000000-0000-4000-8000-000000000009"
FIXED_UUID = UUID(WORK_ID)
FIXED_NOW = datetime(2026, 8, 14, 4, 5, 6, tzinfo=timezone.utc)
CANONICAL = "https://x.com/fixture_author/status/1234567890123456789"
FIXTURE = Path(__file__).parent / "fixtures" / "x_article" / "article-code-images.html"


def _work_root(root: Path) -> Path:
    return root / "01-内容生产" / "视频工作台" / ".internal" / "洗稿" / WORK_ID


def _load_cli_module():
    script_path = (
        Path(__file__).parents[1]
        / "automation"
        / "scripts"
        / "acquire_x_article.py"
    )
    spec = importlib.util.spec_from_file_location("acquire_x_article_cli", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _intake(root: Path) -> None:
    input_file = root / "private-url.txt"
    input_file.write_text(CANONICAL + "\n", encoding="utf-8")
    create_x_article_intake(
        root,
        input_file,
        authorized=True,
        now=lambda: FIXED_NOW,
        uuid_factory=lambda: FIXED_UUID,
    )


def _approval(root: Path, *, request_count: int | None = None) -> Path:
    work_root = _work_root(root)
    plan_path = work_root / "x-article-acquisition-plan.json"
    plan = json.loads(plan_path.read_text("utf-8"))
    receipt = {
        "action": plan["action"],
        "approved": True,
        "input_sha256": plan["input_sha256"],
        "no_fallback": True,
        "no_retry": True,
        "plan_sha256": sha256_file(plan_path),
        "provider": plan["provider"],
        "request_count": (
            plan["total_request_budget"]
            if request_count is None
            else request_count
        ),
        "work_id": WORK_ID,
    }
    target = work_root / "x-article-acquisition-approval.json"
    publish_json_exclusive(work_root, target, receipt)
    return target


def _png() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (16, 9), (20, 40, 60)).save(stream, format="PNG")
    return stream.getvalue()


class FakeTransport:
    def __init__(self, *, fail_image: bool = False) -> None:
        self.page_calls = 0
        self.image_calls = 0
        self.fail_image = fail_image

    def fetch_page(self, url: str) -> bytes:
        self.page_calls += 1
        assert url == CANONICAL
        return FIXTURE.read_bytes()

    def fetch_image(self, url: str) -> XArticleImage:
        self.image_calls += 1
        if self.fail_image:
            raise XArticleProviderError("x-article-image-invalid")
        payload = _png()
        return XArticleImage(payload=payload, format="png", width=16, height=9)


def _numbered_png(index: int) -> bytes:
    stream = BytesIO()
    Image.new(
        "RGB",
        (16, 9),
        (index % 256, (index * 3) % 256, (index * 7) % 256),
    ).save(stream, format="PNG")
    return stream.getvalue()


def _document(asset_count: int) -> XArticleDocument:
    assets = tuple(
        XArticleAssetRef(
            key=f"asset-{index:03d}",
            remote_url=f"https://pbs.twimg.com/media/image-{index:03d}",
        )
        for index in range(asset_count)
    )
    blocks = tuple(
        XArticleBlock(type="image", asset_key=asset.key)
        for asset in assets
    )
    return XArticleDocument(
        title="Bounded fixture",
        author_handle="@fixture_author",
        blocks=blocks,
        assets=assets,
    )


class CountingTransport(FakeTransport):
    def __init__(self, *, fail_at: int | None = None) -> None:
        super().__init__()
        self.fail_at = fail_at

    def fetch_image(self, url: str) -> XArticleImage:
        self.image_calls += 1
        index = int(url.rsplit("-", 1)[1])
        if index == self.fail_at:
            raise XArticleProviderError("x-article-image-invalid")
        payload = _numbered_png(index)
        return XArticleImage(payload=payload, format="png", width=16, height=9)


def test_plan_is_exact_offline_hash_bound_twenty_one_request_budget(
    tmp_path: Path,
) -> None:
    _intake(tmp_path)

    plan = plan_x_article_acquisition(tmp_path, WORK_ID)

    path = _work_root(tmp_path) / "x-article-acquisition-plan.json"
    value = json.loads(path.read_text("utf-8"))
    assert value == {
        "action": "x-article-acquisition",
        "fee_possible": False,
        "follow_redirects": False,
        "image_request_budget": 20,
        "input_sha256": sha256_file(_work_root(tmp_path) / "source-input.txt"),
        "network_required": True,
        "no_fallback": True,
        "no_retry": True,
        "page_request_budget": 1,
        "provider": "x-public-relay-html",
        "schema_version": 1,
        "total_request_budget": 21,
        "work_id": WORK_ID,
    }
    assert path.read_bytes() == canonical_json_bytes(value)
    assert plan.total_request_budget == 21


def test_high_media_plan_is_exact_offline_hash_bound_sixty_one_request_budget(
    tmp_path: Path,
) -> None:
    _intake(tmp_path)

    plan = plan_x_article_acquisition(tmp_path, WORK_ID, profile="high-media")

    path = _work_root(tmp_path) / "x-article-acquisition-plan.json"
    value = json.loads(path.read_text("utf-8"))
    assert value == {
        "action": "x-article-acquisition-high-media",
        "fee_possible": False,
        "follow_redirects": False,
        "image_request_budget": 60,
        "input_sha256": sha256_file(_work_root(tmp_path) / "source-input.txt"),
        "network_required": True,
        "no_fallback": True,
        "no_retry": True,
        "page_request_budget": 1,
        "provider": "x-public-relay-html",
        "schema_version": 1,
        "total_request_budget": 61,
        "work_id": WORK_ID,
    }
    assert path.read_bytes() == canonical_json_bytes(value)
    assert plan.total_request_budget == 61


def test_run_publishes_complete_local_article_manifest_and_article_ready(
    tmp_path: Path,
) -> None:
    _intake(tmp_path)
    plan_x_article_acquisition(tmp_path, WORK_ID)
    approval = _approval(tmp_path)
    transport = FakeTransport()

    result = run_x_article_acquisition(
        tmp_path, WORK_ID, approval, transport=transport
    )

    work_root = _work_root(tmp_path)
    manifest_path = work_root / "x-article-manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    assert result.path == manifest_path
    assert result.sha256 == sha256_file(manifest_path)
    assert transport.page_calls == 1
    assert transport.image_calls == 1
    assert manifest["assets"] == [
        {
            "asset_id": "image-001",
            "bytes": len(_png()),
            "format": "png",
            "height": 9,
            "relative_path": "article-source/images/image-001.png",
            "sha256": sha256_file(work_root / "article-source/images/image-001.png"),
            "width": 16,
        }
    ]
    markdown = (work_root / "article-source/article.md").read_text("utf-8")
    assert markdown.count("images/image-001.png") == 2
    assert "pbs.twimg.com" not in markdown + manifest_path.read_text("utf-8")
    assert not (work_root / "article-source" / acquisition_module.RECOVERY_MANIFEST).exists()
    assert WashEventLedger(tmp_path).status(WORK_ID) == "article_ready"


def test_high_media_approval_matches_stored_action_and_request_count(
    tmp_path: Path,
) -> None:
    _intake(tmp_path)
    plan_x_article_acquisition(tmp_path, WORK_ID, profile="high-media")
    approval = _approval(tmp_path)
    transport = FakeTransport()

    run_x_article_acquisition(
        tmp_path, WORK_ID, approval, transport=transport
    )

    assert transport.page_calls == 1
    assert transport.image_calls == 1
    assert WashEventLedger(tmp_path).status(WORK_ID) == "article_ready"


@pytest.mark.parametrize(
    ("profile", "wrong_request_count"),
    [("standard", 61), ("high-media", 21)],
)
def test_run_rejects_cross_profile_request_count_before_transport(
    tmp_path: Path,
    profile: str,
    wrong_request_count: int,
) -> None:
    _intake(tmp_path)
    plan_x_article_acquisition(tmp_path, WORK_ID, profile=profile)
    approval = _approval(tmp_path, request_count=wrong_request_count)
    transport = FakeTransport()

    with pytest.raises(XArticleAcquisitionError, match="^x-article-not-approved$"):
        run_x_article_acquisition(
            tmp_path, WORK_ID, approval, transport=transport
        )

    assert transport.page_calls == 0


def test_image_failure_leaves_no_formal_article_or_manifest(tmp_path: Path) -> None:
    _intake(tmp_path)
    plan_x_article_acquisition(tmp_path, WORK_ID)
    approval = _approval(tmp_path)

    with pytest.raises(XArticleAcquisitionError, match="^x-article-image-invalid$"):
        run_x_article_acquisition(
            tmp_path, WORK_ID, approval, transport=FakeTransport(fail_image=True)
        )

    work_root = _work_root(tmp_path)
    assert not (work_root / "article-source").exists()
    assert not (work_root / "x-article-manifest.json").exists()
    assert not list(work_root.glob(".x-article-staging-*"))
    assert WashEventLedger(tmp_path).status(WORK_ID) == "source_registered"


def test_default_profile_rejects_twenty_first_asset_before_image_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _intake(tmp_path)
    plan_x_article_acquisition(tmp_path, WORK_ID)
    approval = _approval(tmp_path)
    monkeypatch.setattr(
        acquisition_module,
        "parse_x_article_html",
        lambda payload, url: _document(21),
    )
    transport = CountingTransport()

    with pytest.raises(
        XArticleAcquisitionError,
        match="^x-article-request-budget-exceeded$",
    ):
        run_x_article_acquisition(
            tmp_path, WORK_ID, approval, transport=transport
        )

    assert (transport.page_calls, transport.image_calls) == (1, 0)
    assert WashEventLedger(tmp_path).status(WORK_ID) == "source_registered"
    assert not (_work_root(tmp_path) / "article-source").exists()
    assert not (_work_root(tmp_path) / "x-article-manifest.json").exists()


@pytest.mark.parametrize("asset_count", [21, 60])
def test_high_media_profile_publishes_up_to_sixty_unique_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    asset_count: int,
) -> None:
    _intake(tmp_path)
    plan_x_article_acquisition(tmp_path, WORK_ID, profile="high-media")
    approval = _approval(tmp_path)
    monkeypatch.setattr(
        acquisition_module,
        "parse_x_article_html",
        lambda payload, url: _document(asset_count),
    )
    transport = CountingTransport()

    run_x_article_acquisition(
        tmp_path, WORK_ID, approval, transport=transport
    )

    manifest = json.loads(
        (_work_root(tmp_path) / "x-article-manifest.json").read_text("utf-8")
    )
    assert (transport.page_calls, transport.image_calls) == (1, asset_count)
    assert len(manifest["assets"]) == asset_count
    assert WashEventLedger(tmp_path).status(WORK_ID) == "article_ready"


def test_high_media_profile_rejects_sixty_first_asset_before_image_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _intake(tmp_path)
    plan_x_article_acquisition(tmp_path, WORK_ID, profile="high-media")
    approval = _approval(tmp_path)
    monkeypatch.setattr(
        acquisition_module,
        "parse_x_article_html",
        lambda payload, url: _document(61),
    )
    transport = CountingTransport()

    with pytest.raises(
        XArticleAcquisitionError,
        match="^x-article-request-budget-exceeded$",
    ):
        run_x_article_acquisition(
            tmp_path, WORK_ID, approval, transport=transport
        )

    assert (transport.page_calls, transport.image_calls) == (1, 0)
    assert WashEventLedger(tmp_path).status(WORK_ID) == "source_registered"
    assert not (_work_root(tmp_path) / "article-source").exists()
    assert not (_work_root(tmp_path) / "x-article-manifest.json").exists()


def test_high_media_profile_enforces_aggregate_image_byte_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _intake(tmp_path)
    plan_x_article_acquisition(tmp_path, WORK_ID, profile="high-media")
    approval = _approval(tmp_path)
    monkeypatch.setattr(
        acquisition_module,
        "parse_x_article_html",
        lambda payload, url: _document(2),
    )
    monkeypatch.setattr(acquisition_module, "TOTAL_IMAGE_BYTES", len(_numbered_png(0)))
    transport = CountingTransport()

    with pytest.raises(
        XArticleAcquisitionError,
        match="^x-article-request-budget-exceeded$",
    ):
        run_x_article_acquisition(
            tmp_path, WORK_ID, approval, transport=transport
        )

    assert (transport.page_calls, transport.image_calls) == (1, 2)
    assert WashEventLedger(tmp_path).status(WORK_ID) == "source_registered"
    assert not (_work_root(tmp_path) / "article-source").exists()
    assert not (_work_root(tmp_path) / "x-article-manifest.json").exists()


def test_high_media_image_failure_is_not_retried_or_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _intake(tmp_path)
    plan_x_article_acquisition(tmp_path, WORK_ID, profile="high-media")
    approval = _approval(tmp_path)
    monkeypatch.setattr(
        acquisition_module,
        "parse_x_article_html",
        lambda payload, url: _document(3),
    )
    transport = CountingTransport(fail_at=1)

    with pytest.raises(XArticleAcquisitionError, match="^x-article-image-invalid$"):
        run_x_article_acquisition(
            tmp_path, WORK_ID, approval, transport=transport
        )

    assert (transport.page_calls, transport.image_calls) == (1, 2)
    assert WashEventLedger(tmp_path).status(WORK_ID) == "source_registered"
    assert not (_work_root(tmp_path) / "article-source").exists()
    assert not (_work_root(tmp_path) / "x-article-manifest.json").exists()


def test_high_media_crash_journal_recovers_twenty_one_assets_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    donor = tmp_path / "donor-high-media"
    target = tmp_path / "target-high-media"
    for root in (donor, target):
        root.mkdir()
        _intake(root)
        plan_x_article_acquisition(root, WORK_ID, profile="high-media")
    donor_approval = _approval(donor)
    target_approval = _approval(target)
    monkeypatch.setattr(
        acquisition_module,
        "parse_x_article_html",
        lambda payload, url: _document(21),
    )
    run_x_article_acquisition(
        donor, WORK_ID, donor_approval, transport=CountingTransport()
    )

    donor_root = _work_root(donor)
    target_root = _work_root(target)
    article = target_root / "article-source"
    shutil.copytree(donor_root / "article-source", article)
    shutil.copyfile(
        donor_root / "x-article-manifest.json",
        article / acquisition_module.RECOVERY_MANIFEST,
    )
    transport = CountingTransport()

    result = run_x_article_acquisition(
        target, WORK_ID, target_approval, transport=transport
    )

    assert result.path == target_root / "x-article-manifest.json"
    assert (transport.page_calls, transport.image_calls) == (0, 0)
    assert WashEventLedger(target).status(WORK_ID) == "article_ready"


def _pending_recovery_package(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    asset_count: int,
    target_profile: str = "high-media",
) -> tuple[Path, Path, Path]:
    donor = root / "donor-recovery"
    target = root / "target-recovery"
    for workspace, profile in (
        (donor, "high-media"),
        (target, target_profile),
    ):
        workspace.mkdir()
        _intake(workspace)
        plan_x_article_acquisition(workspace, WORK_ID, profile=profile)
    donor_approval = _approval(donor)
    target_approval = _approval(target)
    monkeypatch.setattr(
        acquisition_module,
        "parse_x_article_html",
        lambda payload, url: _document(asset_count),
    )
    run_x_article_acquisition(
        donor, WORK_ID, donor_approval, transport=CountingTransport()
    )

    donor_root = _work_root(donor)
    target_root = _work_root(target)
    article = target_root / "article-source"
    shutil.copytree(donor_root / "article-source", article)
    shutil.copyfile(
        donor_root / "x-article-manifest.json",
        article / acquisition_module.RECOVERY_MANIFEST,
    )
    return target, target_approval, article


def test_recovery_rejects_aggregate_image_bytes_above_plan_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, approval, article = _pending_recovery_package(
        tmp_path, monkeypatch, asset_count=2
    )
    monkeypatch.setattr(
        acquisition_module,
        "TOTAL_IMAGE_BYTES",
        len(_numbered_png(0)),
    )
    transport = CountingTransport()

    with pytest.raises(XArticleAcquisitionError, match="^x-article-publication-failed$"):
        run_x_article_acquisition(
            target, WORK_ID, approval, transport=transport
        )

    assert (transport.page_calls, transport.image_calls) == (0, 0)
    assert WashEventLedger(target).status(WORK_ID) == "source_registered"
    assert not (_work_root(target) / "x-article-manifest.json").exists()
    assert (article / acquisition_module.RECOVERY_MANIFEST).exists()


def test_recovery_rejects_single_image_above_plan_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, approval, article = _pending_recovery_package(
        tmp_path, monkeypatch, asset_count=1
    )
    monkeypatch.setattr(
        acquisition_module,
        "IMAGE_MAX_BYTES",
        len(_numbered_png(0)) - 1,
        raising=False,
    )
    transport = CountingTransport()

    with pytest.raises(XArticleAcquisitionError, match="^x-article-publication-failed$"):
        run_x_article_acquisition(
            target, WORK_ID, approval, transport=transport
        )

    assert (transport.page_calls, transport.image_calls) == (0, 0)
    assert WashEventLedger(target).status(WORK_ID) == "source_registered"
    assert not (_work_root(target) / "x-article-manifest.json").exists()
    assert (article / acquisition_module.RECOVERY_MANIFEST).exists()


def test_recovery_ledger_failure_rolls_back_new_manifest_and_keeps_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, approval, article = _pending_recovery_package(
        tmp_path, monkeypatch, asset_count=1
    )
    original_append = acquisition_module.WashEventLedger.append

    def fail_article_append(self, event):
        if event.stage == "article_ready":
            raise SourceLedgerError("ledger-unavailable")
        return original_append(self, event)

    monkeypatch.setattr(acquisition_module.WashEventLedger, "append", fail_article_append)
    transport = CountingTransport()

    with pytest.raises(XArticleAcquisitionError, match="^x-article-publication-failed$"):
        run_x_article_acquisition(
            target, WORK_ID, approval, transport=transport
        )

    assert (transport.page_calls, transport.image_calls) == (0, 0)
    assert WashEventLedger(target).status(WORK_ID) == "source_registered"
    assert not (_work_root(target) / "x-article-manifest.json").exists()
    assert (article / acquisition_module.RECOVERY_MANIFEST).exists()


def test_recovery_ledger_failure_replaces_preexisting_manifest_with_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, approval, article = _pending_recovery_package(
        tmp_path, monkeypatch, asset_count=1
    )
    pending = article / acquisition_module.RECOVERY_MANIFEST
    manifest = _work_root(target) / "x-article-manifest.json"
    shutil.copyfile(pending, manifest)
    pending.unlink()
    original_append = acquisition_module.WashEventLedger.append

    def fail_article_append(self, event):
        if event.stage == "article_ready":
            raise SourceLedgerError("ledger-unavailable")
        return original_append(self, event)

    monkeypatch.setattr(acquisition_module.WashEventLedger, "append", fail_article_append)
    transport = CountingTransport()

    with pytest.raises(XArticleAcquisitionError, match="^x-article-publication-failed$"):
        run_x_article_acquisition(
            target, WORK_ID, approval, transport=transport
        )

    assert (transport.page_calls, transport.image_calls) == (0, 0)
    assert WashEventLedger(target).status(WORK_ID) == "source_registered"
    assert not manifest.exists()
    assert pending.exists()


def test_recovery_pending_cleanup_failure_is_not_reported_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, approval, article = _pending_recovery_package(
        tmp_path, monkeypatch, asset_count=1
    )
    pending = article / acquisition_module.RECOVERY_MANIFEST
    manifest = _work_root(target) / "x-article-manifest.json"
    original_unlink = Path.unlink

    def fail_pending_unlink(self, *args, **kwargs):
        if self == pending:
            raise OSError("pending-cleanup-failed")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_pending_unlink)
    transport = CountingTransport()

    with pytest.raises(XArticleAcquisitionError, match="^x-article-publication-failed$"):
        run_x_article_acquisition(
            target, WORK_ID, approval, transport=transport
        )

    assert (transport.page_calls, transport.image_calls) == (0, 0)
    assert WashEventLedger(target).status(WORK_ID) == "source_registered"
    assert not manifest.exists()
    assert pending.exists()


def test_standard_profile_rejects_twenty_one_asset_recovery_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, approval, article = _pending_recovery_package(
        tmp_path,
        monkeypatch,
        asset_count=21,
        target_profile="standard",
    )
    transport = CountingTransport()

    with pytest.raises(XArticleAcquisitionError, match="^x-article-publication-failed$"):
        run_x_article_acquisition(
            target, WORK_ID, approval, transport=transport
        )

    assert (transport.page_calls, transport.image_calls) == (0, 0)
    assert WashEventLedger(target).status(WORK_ID) == "source_registered"
    assert not (_work_root(target) / "x-article-manifest.json").exists()
    assert (article / acquisition_module.RECOVERY_MANIFEST).exists()


def test_run_rejects_input_change_and_existing_target_before_transport(
    tmp_path: Path,
) -> None:
    _intake(tmp_path)
    plan_x_article_acquisition(tmp_path, WORK_ID)
    approval = _approval(tmp_path)
    (_work_root(tmp_path) / "source-input.txt").write_text(
        "https://x.com/changed/status/1\n", encoding="utf-8"
    )
    transport = FakeTransport()
    with pytest.raises(XArticleAcquisitionError, match="^x-article-input-changed$"):
        run_x_article_acquisition(tmp_path, WORK_ID, approval, transport=transport)
    assert transport.page_calls == 0


def test_run_rejects_receipt_mismatch_and_no_clobber(tmp_path: Path) -> None:
    _intake(tmp_path)
    plan_x_article_acquisition(tmp_path, WORK_ID)
    approval = _approval(tmp_path)
    receipt = json.loads(approval.read_text("utf-8"))
    receipt["plan_sha256"] = "0" * 64
    approval.write_bytes(canonical_json_bytes(receipt))
    transport = FakeTransport()
    with pytest.raises(XArticleAcquisitionError, match="^x-article-not-approved$"):
        run_x_article_acquisition(tmp_path, WORK_ID, approval, transport=transport)
    assert transport.page_calls == 0


def test_existing_formal_target_is_never_clobbered(tmp_path: Path) -> None:
    _intake(tmp_path)
    plan_x_article_acquisition(tmp_path, WORK_ID)
    approval = _approval(tmp_path)
    article = _work_root(tmp_path) / "article-source"
    article.mkdir()
    marker = article / "owner.txt"
    marker.write_text("keep", encoding="utf-8")
    transport = FakeTransport()

    with pytest.raises(XArticleAcquisitionError, match="^x-article-publication-failed$"):
        run_x_article_acquisition(tmp_path, WORK_ID, approval, transport=transport)

    assert marker.read_text("utf-8") == "keep"
    assert transport.page_calls == 0


def test_crash_journal_recovers_without_a_second_network_request(
    tmp_path: Path,
) -> None:
    donor = tmp_path / "donor"
    target = tmp_path / "target"
    for root in (donor, target):
        root.mkdir()
        _intake(root)
        plan_x_article_acquisition(root, WORK_ID)
    donor_approval = _approval(donor)
    target_approval = _approval(target)
    run_x_article_acquisition(
        donor, WORK_ID, donor_approval, transport=FakeTransport()
    )

    donor_root = _work_root(donor)
    target_root = _work_root(target)
    article = target_root / "article-source"
    shutil.copytree(donor_root / "article-source", article)
    shutil.copyfile(
        donor_root / "x-article-manifest.json",
        article / ".x-article-manifest.pending.json",
    )
    transport = FakeTransport()

    result = run_x_article_acquisition(
        target, WORK_ID, target_approval, transport=transport
    )

    assert result.path == target_root / "x-article-manifest.json"
    assert transport.page_calls == 0
    assert transport.image_calls == 0
    assert not (article / ".x-article-manifest.pending.json").exists()
    assert WashEventLedger(target).status(WORK_ID) == "article_ready"


def test_ledger_failure_rolls_back_formal_article_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _intake(tmp_path)
    plan_x_article_acquisition(tmp_path, WORK_ID)
    approval = _approval(tmp_path)
    original_append = acquisition_module.WashEventLedger.append

    def fail_article_append(self, event):
        if event.stage == "article_ready":
            raise SourceLedgerError("ledger-unavailable")
        return original_append(self, event)

    monkeypatch.setattr(acquisition_module.WashEventLedger, "append", fail_article_append)

    with pytest.raises(XArticleAcquisitionError, match="^x-article-publication-failed$"):
        run_x_article_acquisition(
            tmp_path, WORK_ID, approval, transport=FakeTransport()
        )

    assert not (_work_root(tmp_path) / "article-source").exists()
    assert not (_work_root(tmp_path) / "x-article-manifest.json").exists()


def test_cli_plan_is_offline_and_redacted(tmp_path: Path, capsys) -> None:
    _intake(tmp_path)
    module = _load_cli_module()

    assert module.main(["plan", WORK_ID], root=tmp_path) == 0
    captured = capsys.readouterr()
    assert captured.out == "work=000000000000 action=x-article-acquisition status=planned\n"
    assert captured.err == ""
    assert CANONICAL not in captured.out


def test_cli_plan_selects_high_media_explicitly_and_run_has_no_profile(
    tmp_path: Path, capsys
) -> None:
    _intake(tmp_path)
    module = _load_cli_module()

    assert module.main(
        ["plan", WORK_ID, "--profile", "high-media"], root=tmp_path
    ) == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "work=000000000000 "
        "action=x-article-acquisition-high-media status=planned\n"
    )
    assert captured.err == ""
    assert CANONICAL not in captured.out
    plan = json.loads(
        (_work_root(tmp_path) / "x-article-acquisition-plan.json").read_text(
            "utf-8"
        )
    )
    assert plan["image_request_budget"] == 60
    assert plan["total_request_budget"] == 61

    assert module.main(
        [
            "run",
            WORK_ID,
            "--approval",
            "receipt.json",
            "--profile",
            "high-media",
        ],
        root=tmp_path,
    ) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error=invalid-arguments\n"
