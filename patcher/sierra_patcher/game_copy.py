from __future__ import annotations

import json
import ntpath
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import proc


COPY_STATE_FILENAME = ".sierra-copy-state.json"
_COPY_STATE_FORMAT_VERSION = 1
_COPY_CHUNK_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class CopyDestinationStatus:
    ready: bool
    reason: str
    resumable: bool = False


def _windows_style(path: str) -> bool:
    return os.name == "nt" or bool(ntpath.splitdrive(path)[0]) or "\\" in path


def _canonical_path(path: str | os.PathLike) -> str:
    raw = os.fspath(path)
    if os.name == "nt":
        return os.path.normcase(os.path.realpath(os.path.abspath(raw)))
    if _windows_style(raw):
        return ntpath.normcase(ntpath.abspath(raw.replace("/", "\\")))
    return os.path.normcase(os.path.realpath(os.path.abspath(raw)))


def paths_overlap(source: str | os.PathLike, destination: str | os.PathLike) -> bool:
    """Return whether either install path contains the other."""

    source_key = _canonical_path(source)
    destination_key = _canonical_path(destination)
    path_module = ntpath if _windows_style(source_key) or _windows_style(destination_key) else os.path
    try:
        common = path_module.commonpath((source_key, destination_key))
    except ValueError:
        return False
    return common == source_key or common == destination_key


def _state_path(destination: Path) -> Path:
    return destination / COPY_STATE_FILENAME


def _copy_state(source: Path, destination: Path, source_version: str | None) -> dict:
    return {
        "format_version": _COPY_STATE_FORMAT_VERSION,
        "source": _canonical_path(source),
        "destination": _canonical_path(destination),
        "source_version": str(source_version or "").strip(),
    }


def _read_state(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def inspect_copy_destination(
    source: str | os.PathLike,
    destination: str | os.PathLike,
    source_version: str | None = None,
) -> CopyDestinationStatus:
    source_path = Path(source)
    destination_path = Path(destination)

    if not source_path.is_dir() or not (source_path / "EscapeFromTarkov.exe").is_file():
        return CopyDestinationStatus(False, "source_missing")
    if not os.fspath(destination).strip():
        return CopyDestinationStatus(False, "destination_missing")
    if paths_overlap(source_path, destination_path):
        return CopyDestinationStatus(False, "overlap")
    if destination_path.exists() and not destination_path.is_dir():
        return CopyDestinationStatus(False, "not_directory")
    if not destination_path.exists():
        return CopyDestinationStatus(True, "new")

    entries = list(destination_path.iterdir())
    if not entries:
        return CopyDestinationStatus(True, "empty")

    state_path = _state_path(destination_path)
    state = _read_state(state_path) if state_path.is_file() else None
    if state is None:
        return CopyDestinationStatus(False, "not_empty")
    if state != _copy_state(source_path, destination_path, source_version):
        return CopyDestinationStatus(False, "state_mismatch")
    return CopyDestinationStatus(True, "resume", resumable=True)


def _raise_if_cancelled(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise proc.Cancelled("Live game copy cancelled")


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _same_file(source: Path, destination: Path) -> bool:
    try:
        source_stat = source.stat()
        destination_stat = destination.stat()
    except OSError:
        return False
    return (
        source_stat.st_size == destination_stat.st_size
        and abs(source_stat.st_mtime_ns - destination_stat.st_mtime_ns) <= 2_000_000_000
    )


def _disk_usage_root(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def copy_live_game(
    source: str | os.PathLike,
    destination: str | os.PathLike,
    *,
    source_version: str | None = None,
    on_progress=None,
    cancel_event=None,
) -> None:
    """Copy a detected Live install into a new SPT folder with copy-only resume."""

    source_path = Path(source)
    destination_path = Path(destination)
    status = inspect_copy_destination(source_path, destination_path, source_version)
    if not status.ready:
        raise RuntimeError(f"Live game copy destination is not usable: {status.reason}")

    _raise_if_cancelled(cancel_event)
    if on_progress is not None:
        on_progress("install:copy", 0, 1, "Scanning Live game...")

    files: list[tuple[Path, Path, int]] = []
    directories: list[Path] = []
    total_bytes = 0
    remaining_bytes = 0
    for root, dirnames, filenames in os.walk(source_path):
        _raise_if_cancelled(cancel_event)
        root_path = Path(root)
        relative_root = root_path.relative_to(source_path)
        directories.extend(destination_path / relative_root / name for name in dirnames)
        for name in filenames:
            if name == COPY_STATE_FILENAME:
                continue
            source_file = root_path / name
            destination_file = destination_path / relative_root / name
            size = source_file.stat().st_size
            files.append((source_file, destination_file, size))
            total_bytes += size
            if not _same_file(source_file, destination_file):
                remaining_bytes += size

    free_bytes = shutil.disk_usage(_disk_usage_root(destination_path)).free
    if free_bytes < remaining_bytes:
        raise RuntimeError(
            "Not enough free space to copy the Live game "
            f"({remaining_bytes} bytes required, {free_bytes} bytes available)"
        )

    destination_path.mkdir(parents=True, exist_ok=True)
    _write_state(
        _state_path(destination_path),
        _copy_state(source_path, destination_path, source_version),
    )
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

    copied_bytes = 0
    progress_total = max(total_bytes, 1)
    for source_file, destination_file, size in files:
        _raise_if_cancelled(cancel_event)
        if _same_file(source_file, destination_file):
            copied_bytes += size
            if on_progress is not None:
                on_progress(
                    "install:copy",
                    copied_bytes,
                    progress_total,
                    f"Reusing {source_file.name}",
                )
            continue

        destination_file.parent.mkdir(parents=True, exist_ok=True)
        with source_file.open("rb") as source_stream, destination_file.open("wb") as destination_stream:
            while True:
                _raise_if_cancelled(cancel_event)
                chunk = source_stream.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                destination_stream.write(chunk)
                copied_bytes += len(chunk)
                if on_progress is not None:
                    on_progress(
                        "install:copy",
                        copied_bytes,
                        progress_total,
                        f"Copying {source_file.name}",
                    )
        shutil.copystat(source_file, destination_file)

    _raise_if_cancelled(cancel_event)
    _state_path(destination_path).unlink(missing_ok=True)
    if on_progress is not None:
        on_progress(
            "install:copy",
            progress_total,
            progress_total,
            "Live game copy complete",
        )
