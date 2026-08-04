from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


TEMP_SUFFIXES = (".tmp_out", ".tmp_src", ".new")
IGNORED_DIRS = {"__pycache__"}


@dataclass(frozen=True)
class HygieneReport:
    removed_files: int = 0
    removed_dirs: int = 0
    removed_bytes: int = 0
    remaining_files: int = 0
    remaining_bytes: int = 0


def relative_package_path(path: str | Path, root: str | Path) -> Path:
    return Path(path).resolve().relative_to(Path(root).resolve())


def is_package_excluded(path: str | Path, root: str | Path) -> bool:
    """Return True for generated/debug files that should never ship."""

    rel = relative_package_path(path, root)
    parts = rel.parts
    if not parts:
        return False

    if parts[0].lower() == "logs":
        return True

    if any(part in IGNORED_DIRS for part in parts):
        return True

    name = rel.name.lower()
    return name.endswith(TEMP_SUFFIXES)


def copy_package_file(src: str | Path, package_root: str | Path, rel: str | Path) -> None:
    dst = Path(package_root) / Path(rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def prune_package_exclusions(root: str | Path) -> HygieneReport:
    """Remove excluded files from a generated package staging directory."""

    root_path = Path(root)
    if not root_path.is_dir():
        return HygieneReport()

    removed_files = 0
    removed_dirs = 0
    removed_bytes = 0

    for path in list(root_path.rglob("*")):
        if path.is_file() and is_package_excluded(path, root_path):
            try:
                removed_bytes += path.stat().st_size
            except OSError:
                pass
            path.unlink(missing_ok=True)
            removed_files += 1

    for path in sorted((p for p in root_path.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not path.exists():
            continue
        if path.name in IGNORED_DIRS or not any(path.iterdir()):
            shutil.rmtree(path, ignore_errors=True)
            removed_dirs += 1

    remaining_files = 0
    remaining_bytes = 0
    for path in root_path.rglob("*"):
        if path.is_file():
            remaining_files += 1
            try:
                remaining_bytes += path.stat().st_size
            except OSError:
                pass

    return HygieneReport(
        removed_files=removed_files,
        removed_dirs=removed_dirs,
        removed_bytes=removed_bytes,
        remaining_files=remaining_files,
        remaining_bytes=remaining_bytes,
    )


def format_size(num_bytes: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024


def largest_package_files(root: str | Path, limit: int = 8) -> list[tuple[Path, int]]:
    root_path = Path(root)
    files: list[tuple[Path, int]] = []
    if not root_path.is_dir():
        return files

    for path in root_path.rglob("*"):
        if not path.is_file():
            continue
        try:
            files.append((path.relative_to(root_path), path.stat().st_size))
        except OSError:
            pass

    files.sort(key=lambda item: item[1], reverse=True)
    return files[:limit]
