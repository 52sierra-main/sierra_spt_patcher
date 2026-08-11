from __future__ import annotations

import json
from pathlib import Path

from .registry import exe_version
from .runtime_requirements import load_runtime_requirements_manifest


class Meta:
    def __init__(
        self,
        version: str,
        title: str,
        description: str,
        dependencies=None,
        integrity_folders: dict[str, int] | None = None,
        diff_profile: str | None = None,
        zstd_patch_args: list[str] | None = None,
        runtime_requirements: list[dict] | None = None,
    ):
        self.version = version
        self.title = title
        self.description = description
        self.dependencies = dependencies
        self.integrity_folders: dict[str, int] = integrity_folders or {}
        self.diff_profile = diff_profile
        self.zstd_patch_args = zstd_patch_args
        self.runtime_requirements: list[dict] = runtime_requirements or []

    @staticmethod
    def read(info_dir: str | Path) -> "Meta":
        """Read metadata.info (JSON if possible, fall back to legacy 3-line text)."""

        info_dir_path = Path(info_dir)
        info_file = next(info_dir_path.glob("*.info"), None)
        if not info_file:
            raise FileNotFoundError("Metadata .info file not found")

        raw = info_file.read_text(encoding="utf-8")
        text = raw.lstrip()

        companion_runtime_requirements = load_runtime_requirements_manifest(info_dir_path)

        # New JSON format
        if text.startswith("{"):
            data = json.loads(raw)
            embedded_runtime_requirements = data.get("runtime_requirements")
            runtime_requirements = (
                embedded_runtime_requirements
                if isinstance(embedded_runtime_requirements, list)
                else companion_runtime_requirements
            )
            return Meta(
                version=data.get("version", ""),
                title=data.get("title", ""),
                description=data.get("description", ""),
                dependencies=data.get("dependencies"),
                integrity_folders=data.get("integrity_folders", {}) or {},
                diff_profile=data.get("diff_profile"),
                zstd_patch_args=data.get("zstd_patch_args"),
                runtime_requirements=runtime_requirements or [],
            )

        # Legacy 3-line format: version, title, description, [dependencies?]
        lines = raw.splitlines()
        while len(lines) < 4:
            lines.append("")

        return Meta(
            lines[0].strip(),
            lines[1].strip(),
            lines[2].strip(),
            lines[3].strip() or None,
            integrity_folders={},
            runtime_requirements=companion_runtime_requirements or [],
        )

    @staticmethod
    def write(
        info_path: str | Path,
        version: str,
        title: str,
        date_str: str,
        dependencies=None,
        integrity_folders: dict[str, int] | None = None,
        diff_profile: str | None = None,
        zstd_patch_args: list[str] | None = None,
        runtime_requirements: list[dict] | None = None,
    ) -> None:
        """Write JSON metadata (new format)."""

        p = Path(info_path)
        p.parent.mkdir(parents=True, exist_ok=True)

        if runtime_requirements is None:
            runtime_requirements = load_runtime_requirements_manifest(p.parent) or []

        data = {
            "version": version,
            "title": title,
            "description": date_str,
            "dependencies": dependencies,
            "integrity_folders": integrity_folders or {},
        }

        # Optional fields (backward compatible)
        if diff_profile is not None:
            data["diff_profile"] = diff_profile
        if zstd_patch_args is not None:
            data["zstd_patch_args"] = zstd_patch_args
        if runtime_requirements:
            data["runtime_requirements"] = runtime_requirements

        p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def stamp_from_game_exe(
    info_path: str | Path,
    source_dir: str | Path,
    target_title: str,
    date_str: str,
    integrity_folders: dict[str, int] | None = None,
    diff_profile: str | None = None,
    zstd_patch_args: list[str] | None = None,
    runtime_requirements: list[dict] | None = None,
) -> None:
    """Convenience for generator: stamp version from EscapeFromTarkov.exe."""

    v = exe_version(str(Path(source_dir) / "EscapeFromTarkov.exe")) or "0.0.0.0"
    Meta.write(
        info_path,
        v,
        target_title,
        date_str,
        integrity_folders=integrity_folders,
        diff_profile=diff_profile,
        zstd_patch_args=zstd_patch_args,
        runtime_requirements=runtime_requirements,
    )
