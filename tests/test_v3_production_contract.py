from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from test_content_render_project import v3_project


SCRIPT = Path(__file__).parents[1] / "automation" / "scripts" / "run_content_production.py"
CHECKER = Path(__file__).parents[1] / "automation" / "scripts" / "check_delivery.py"


def _load_production_module():
    spec = importlib.util.spec_from_file_location("v3_content_production_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_checker_module():
    spec = importlib.util.spec_from_file_location("v3_delivery_checker_under_test", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_v3_content_contract_protects_every_semantic_and_motion_artifact(
    v3_project: Path,
) -> None:
    module = _load_production_module()

    _timeline, protected, _handoff, motion, _cover_sources = module._content_contract(
        SimpleNamespace(active_dir=v3_project)
    )

    assert motion is not None
    relative = {path.relative_to(v3_project).as_posix() for path in protected}
    assert {
        "工程/content-plan.json",
        "工程/scene-timeline.json",
        "工程/motion-plan.json",
        "工程/assets/semantic-handdrawn/illustration-manifest.json",
        "工程/assets/semantic-handdrawn/semantic-qc.json",
        "工程/assets/semantic-handdrawn/prompts/scene-01.md",
        "工程/assets/semantic-handdrawn/prompts/scene-02.md",
        "工程/assets/semantic-handdrawn/prompts/scene-03.md",
        "工程/assets/semantic-handdrawn/prompts/scene-04.md",
        "工程/assets/semantic-handdrawn/candidates/scene-01-candidate-01.png",
        "工程/assets/semantic-handdrawn/candidates/scene-02-candidate-01.png",
        "工程/assets/semantic-handdrawn/candidates/scene-03-candidate-01.png",
        "工程/assets/semantic-handdrawn/candidates/scene-04-candidate-01.png",
        "工程/assets/semantic-handdrawn/scene-01.png",
        "工程/assets/semantic-handdrawn/scene-02.png",
        "工程/assets/semantic-handdrawn/scene-03.png",
        "工程/assets/semantic-handdrawn/scene-04.png",
    } <= relative


def test_v3_delivery_dispatches_to_semantic_manifest_validation(
    v3_project: Path,
) -> None:
    checker = _load_checker_module()
    qc = v3_project / "工程" / "assets" / "semantic-handdrawn" / "semantic-qc.json"
    qc.write_text("{}\n", encoding="utf-8")
    previews = v3_project / "质检" / "scene-previews"
    previews.mkdir(parents=True)
    (v3_project / "质检" / "scene-preview-qc.json").write_text(
        "{}\n", encoding="utf-8"
    )
    errors: list[str] = []

    checker._content_delivery_artifacts(
        v3_project,
        v3_project / "成片" / "missing.mp4",
        {},
        errors,
    )

    assert "rule=illustration-manifest" in errors
    assert "rule=content-plan" not in errors
