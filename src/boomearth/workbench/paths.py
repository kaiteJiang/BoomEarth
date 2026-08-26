from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkbenchPaths:
    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).resolve(strict=False))

    @property
    def content_root(self) -> Path:
        return self.root / "01-内容生产"

    @property
    def topic_pool(self) -> Path:
        return self.content_root / "00-选题池"

    @property
    def benchmark_library(self) -> Path:
        return self.content_root / "03-对标爆款库"

    @property
    def material_library(self) -> Path:
        return self.content_root / "素材库"

    @property
    def data_stats(self) -> Path:
        return self.content_root / "数据统计"

    @property
    def workbench_root(self) -> Path:
        return self.content_root / "视频工作台"

    @property
    def pending(self) -> Path:
        return self.workbench_root / "待制作"

    @property
    def active(self) -> Path:
        return self.workbench_root / "制作中"

    @property
    def archived(self) -> Path:
        return self.workbench_root / "已制作"

    @property
    def private_wash(self) -> Path:
        return self._ensure_contained(self.workbench_root / ".internal" / "洗稿")

    @property
    def components_root(self) -> Path:
        return self.root / "05-视频组件"

    def public_project(self, state: str, slug: str) -> Path:
        public_roots = {
            "pending": self.pending,
            "active": self.active,
            "archived": self.archived,
        }
        if state not in public_roots:
            raise ValueError("state must be pending, active, or archived")
        return self._safe_relative(public_roots[state], slug)

    def private_source(self, relative: str) -> Path:
        return self._safe_relative(self.private_wash, relative)

    def create_skeleton(self) -> tuple[Path, ...]:
        directories = (
            self.content_root,
            self.topic_pool,
            self.benchmark_library,
            self.material_library,
            self.data_stats,
            self.workbench_root,
            self.pending,
            self.active,
            self.archived,
            self.private_wash,
            self.components_root,
        )
        for directory in directories:
            self._ensure_contained(directory)
            directory.mkdir(parents=True, exist_ok=True)
            self._ensure_contained(directory)
        return directories

    def _ensure_contained(self, path: Path) -> Path:
        resolved_path = path.resolve(strict=False)
        comparison_path = self._normalize_for_containment(resolved_path)
        comparison_root = self._normalize_for_containment(self.root)
        try:
            comparison_path.relative_to(comparison_root)
        except ValueError as exc:
            raise ValueError("path escapes its root") from exc
        return resolved_path

    @staticmethod
    def _normalize_for_containment(path: Path) -> Path:
        path_text = str(path)
        extended_unc_prefix = "\\\\?\\UNC\\"
        if path_text.casefold().startswith(extended_unc_prefix.casefold()):
            return Path("\\\\" + path_text[len(extended_unc_prefix) :])

        extended_prefix = "\\\\?\\"
        if path_text.casefold().startswith(extended_prefix.casefold()):
            return Path(path_text[len(extended_prefix) :])
        return path

    def _safe_relative(self, base: Path, relative: str) -> Path:
        if not isinstance(relative, str):
            raise TypeError("relative must be a string")
        if not relative.strip():
            raise ValueError("relative path must not be empty")

        candidate = Path(relative)
        if candidate.is_absolute() or candidate.drive:
            raise ValueError("relative path must not be absolute")
        if ".." in candidate.parts:
            raise ValueError("relative path must not contain '..'")

        resolved_base = self._ensure_contained(base)
        resolved_candidate = self._ensure_contained(base / candidate)
        try:
            resolved_candidate.relative_to(resolved_base)
        except ValueError as exc:
            raise ValueError("relative path escapes its root") from exc
        return base / candidate
