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


def _io_path(path: str | os.PathLike) -> str:
    value = os.path.abspath(os.fspath(path))
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


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
        with open(_io_path(path), "r", encoding="utf-8") as stream:
            data = json.load(stream)
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

    if not os.path.isdir(_io_path(source_path)) or not os.path.isfile(
        _io_path(source_path / "EscapeFromTarkov.exe")
    ):
        return CopyDestinationStatus(False, "source_missing")
    if not os.fspath(destination).strip():
        return CopyDestinationStatus(False, "destination_missing")
    if paths_overlap(source_path, destination_path):
        return CopyDestinationStatus(False, "overlap")
    destination_exists = os.path.exists(_io_path(destination_path))
    if destination_exists and not os.path.isdir(_io_path(destination_path)):
        return CopyDestinationStatus(False, "not_directory")
    if not destination_exists:
        return CopyDestinationStatus(True, "new")

    entries = os.listdir(_io_path(destination_path))
    if not entries:
        return CopyDestinationStatus(True, "empty")

    state_path = _state_path(destination_path)
    state = _read_state(state_path) if os.path.isfile(_io_path(state_path)) else None
    if state is None:
        return CopyDestinationStatus(False, "not_empty")
    if state != _copy_state(source_path, destination_path, source_version):
        return CopyDestinationStatus(False, "state_mismatch")
    return CopyDestinationStatus(True, "resume", resumable=True)


def _raise_if_cancelled(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise proc.Cancelled("Live game copy cancelled")


def _write_state(path: Path, state: dict) -> None:
    os.makedirs(_io_path(path.parent), exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with open(_io_path(temporary), "w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2, ensure_ascii=False)
        os.replace(_io_path(temporary), _io_path(path))
    finally:
        try:
            os.unlink(_io_path(temporary))
        except FileNotFoundError:
            pass


def _same_file(source: Path, destination: Path) -> bool:
    try:
        source_stat = os.stat(_io_path(source))
        destination_stat = os.stat(_io_path(destination))
    except OSError:
        return False
    return (
        source_stat.st_size == destination_stat.st_size
        and abs(source_stat.st_mtime_ns - destination_stat.st_mtime_ns) <= 2_000_000_000
    )


def _disk_usage_root(path: Path) -> Path:
    candidate = path
    while not os.path.exists(_io_path(candidate)) and candidate != candidate.parent:
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
    source_root = _io_path(source_path)
    for root, dirnames, filenames in os.walk(source_root):
        _raise_if_cancelled(cancel_event)
        root_path = Path(root)
        relative = os.path.relpath(root, source_root)
        relative_root = Path() if relative == "." else Path(relative)
        directories.extend(destination_path / relative_root / name for name in dirnames)
        for name in filenames:
            if name == COPY_STATE_FILENAME:
                continue
            source_file = root_path / name
            destination_file = destination_path / relative_root / name
            size = os.path.getsize(_io_path(source_file))
            files.append((source_file, destination_file, size))
            total_bytes += size
            if not _same_file(source_file, destination_file):
                try:
                    existing_size = os.path.getsize(_io_path(destination_file))
                except OSError:
                    existing_size = 0
                remaining_bytes += max(0, size - existing_size)

    free_bytes = shutil.disk_usage(_io_path(_disk_usage_root(destination_path))).free
    if free_bytes < remaining_bytes:
        raise RuntimeError(
            "Not enough free space to copy the Live game "
            f"({remaining_bytes} bytes required, {free_bytes} bytes available)"
        )

    os.makedirs(_io_path(destination_path), exist_ok=True)
    _write_state(
        _state_path(destination_path),
        _copy_state(source_path, destination_path, source_version),
    )
    for directory in directories:
        os.makedirs(_io_path(directory), exist_ok=True)

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

        os.makedirs(_io_path(destination_file.parent), exist_ok=True)
        with open(_io_path(source_file), "rb") as source_stream, open(
            _io_path(destination_file), "wb"
        ) as destination_stream:
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
        shutil.copystat(_io_path(source_file), _io_path(destination_file))

    _raise_if_cancelled(cancel_event)
    try:
        os.unlink(_io_path(_state_path(destination_path)))
    except FileNotFoundError:
        pass
    if on_progress is not None:
        on_progress(
            "install:copy",
            progress_total,
            progress_total,
            "Live game copy complete",
        )
