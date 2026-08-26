"""Validate one formal V2, V3, or profiled V4 illustration manifest."""

from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from boomearth.video.illustration_manifest import IllustrationManifestError, load_illustration_manifest_snapshot  # noqa: E402

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="validate_illustrations.py")
    parser.add_argument("project_root", type=Path)
    try:
        args = parser.parse_args(argv)
        snapshot = load_illustration_manifest_snapshot(args.project_root)
        theme = snapshot.manifest.visual_theme or "legacy"
        print(
            f"illustrations={len(snapshot.manifest.assets)} "
            f"theme={theme} status=pass"
        )
        return 0
    except SystemExit as exc:
        if exc.code == 0:
            return 0
        print("error=illustration-validation-failed", file=sys.stderr)
        return 2
    except IllustrationManifestError:
        print("error=illustration-validation-failed", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
