from __future__ import annotations

import hashlib
import json
import ntpath
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from . import proc

COPY_STATE_FILENAME = ".sierra-copy-state.json"
_COPY_STATE_FORMAT_VERSION = 1
_COPY_CHUNK_BYTES = 4 * 1024 * 1024
DEFAULT_COPY_WORKERS = 4
MAX_COPY_WORKERS = 8


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
    path_module = (
        ntpath
        if _windows_style(source_key) or _windows_style(destination_key)
        else os.path
    )
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
    temp = path.with_name(path.name + ".tmp")
    try:
        with open(_io_path(temp), "w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2, ensure_ascii=False)
        os.replace(_io_path(temp), _io_path(path))
    finally:
        try:
            os.unlink(_io_path(temp))
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


def _sha256_file(path: Path, cancel_event=None) -> str:
    digest = hashlib.sha256()
    with open(_io_path(path), "rb") as stream:
        while True:
            _raise_if_cancelled(cancel_event)
            chunk = stream.read(_COPY_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _remove_failed_copy(path: Path) -> None:
    try:
        os.unlink(_io_path(path))
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _worker_count(value: int) -> int:
    try:
        workers = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Copy / verification workers must be a whole number") from exc
    if workers < 1 or workers > MAX_COPY_WORKERS:
        raise ValueError(
            f"Copy / verification workers must be between 1 and {MAX_COPY_WORKERS}"
        )
    return workers


def _relative_key(value: str | os.PathLike) -> str:
    return os.fspath(value).replace("\\", "/").strip("/").casefold()


def _release_hash_map(entries: list[dict] | tuple[dict, ...] | None) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for entry in entries or ():
        relative = str(entry.get("path", "")).replace("\\", "/").strip("/")
        key = _relative_key(relative)
        if not key:
            raise ValueError("release source hash entry has an empty path")
        if key in result:
            raise ValueError(f"duplicate release source hash path: {relative}")
        try:
            size = int(entry["size"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid release source size: {relative}") from exc
        sha256 = str(entry.get("sha256", "")).strip().lower()
        if size < 0 or len(sha256) != 64 or any(
            char not in "0123456789abcdef" for char in sha256
        ):
            raise ValueError(f"invalid release source hash entry: {relative}")
        result[key] = {"path": relative, "size": size, "sha256": sha256}
    return result


def copy_live_game(
    source: str | os.PathLike,
    destination: str | os.PathLike,
    *,
    source_version: str | None = None,
    on_progress=None,
    cancel_event=None,
    workers: int = DEFAULT_COPY_WORKERS,
    release_source_entries: list[dict] | tuple[dict, ...] | None = None,
) -> None:
    """Copy Live into a new SPT folder, resume safely, and verify every file.

    Copying and post-copy hashing run in parallel. Each source file is hashed while
    it is read for the copy (or while validating a resumable reuse). The destination
    is then read exactly once: that SHA-256 must match the bytes read from Live and,
    for delta-source files, the release-required source hash as well.

    A bad destination file is removed so the next resume recopies it. The resume
    state marker is removed only after the entire destination and release checks pass.
    """

    worker_count = _worker_count(workers)
    release_hashes = _release_hash_map(release_source_entries)
    source_path = Path(source)
    destination_path = Path(destination)
    status = inspect_copy_destination(source_path, destination_path, source_version)
    if not status.ready:
        raise RuntimeError(f"Live game copy destination is not usable: {status.reason}")

    _raise_if_cancelled(cancel_event)
    if on_progress is not None:
        on_progress("install:copy", 0, 1, "Scanning Live game...")

    files: list[tuple[Path, Path, int, str]] = []
    directories: list[Path] = []
    total_bytes = 0
    remaining_bytes = 0
    source_root = _io_path(source_path)
    source_keys: set[str] = set()
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
            relative_text = os.path.relpath(_io_path(source_file), source_root).replace("\\", "/")
            relative_key = _relative_key(relative_text)
            source_keys.add(relative_key)
            size = os.path.getsize(_io_path(source_file))
            files.append((source_file, destination_file, size, relative_text))
            total_bytes += size
            if not _same_file(source_file, destination_file):
                try:
                    existing_size = os.path.getsize(_io_path(destination_file))
                except OSError:
                    existing_size = 0
                remaining_bytes += max(0, size - existing_size)

    missing_release = sorted(set(release_hashes) - source_keys)
    if missing_release:
        first = release_hashes[missing_release[0]]["path"]
        raise RuntimeError(
            f"Live game copy release verification failed: source file disappeared before copy: {first}"
        )

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

    progress_total = max(total_bytes, 1)
    copied_bytes = 0
    progress_lock = threading.Lock()

    def advance_copy(amount: int, message: str) -> None:
        nonlocal copied_bytes
        with progress_lock:
            copied_bytes += amount
            current = copied_bytes
        if on_progress is not None:
            on_progress("install:copy", current, progress_total, message)

    def copy_one(item: tuple[Path, Path, int, str]) -> tuple[Path, str, str]:
        source_file, destination_file, size, relative_text = item
        _raise_if_cancelled(cancel_event)
        if _same_file(source_file, destination_file):
            source_hash = _sha256_file(source_file, cancel_event)
            advance_copy(size, f"Reusing {source_file.name}")
            return destination_file, source_hash, relative_text

        os.makedirs(_io_path(destination_file.parent), exist_ok=True)
        source_digest = hashlib.sha256()
        with open(_io_path(source_file), "rb") as source_stream, open(
            _io_path(destination_file), "wb"
        ) as destination_stream:
            while True:
                _raise_if_cancelled(cancel_event)
                chunk = source_stream.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                source_digest.update(chunk)
                destination_stream.write(chunk)
                advance_copy(len(chunk), f"Copying {source_file.name}")
        shutil.copystat(_io_path(source_file), _io_path(destination_file))
        return destination_file, source_digest.hexdigest(), relative_text

    expected_hashes: dict[Path, tuple[str, str]] = {}
    copy_workers = max(1, min(worker_count, len(files) or 1))
    with ThreadPoolExecutor(max_workers=copy_workers) as executor:
        futures = {executor.submit(copy_one, item): item for item in files}
        for future in as_completed(futures):
            _raise_if_cancelled(cancel_event)
            try:
                destination_file, source_hash, relative_text = future.result()
            except Exception:
                for pending in futures:
                    pending.cancel()
                raise
            expected_hashes[destination_file] = (source_hash, relative_text)

    # One parallel destination-read pass proves both copy integrity and release
    # compatibility. Delta-source files are not read a second time afterward.
    verify_total = max(len(files), 1)
    verified = 0
    verify_lock = threading.Lock()

    def verify_one(item: tuple[Path, Path, int, str]) -> str:
        _source_file, destination_file, expected_size, relative_text = item
        _raise_if_cancelled(cancel_event)

        if not os.path.isfile(_io_path(destination_file)):
            raise RuntimeError(f"Live game copy verification failed: missing {relative_text}")
        actual_size = os.path.getsize(_io_path(destination_file))
        if actual_size != expected_size:
            _remove_failed_copy(destination_file)
            raise RuntimeError(
                "Live game copy verification failed: "
                f"{relative_text} size changed (expected {expected_size}, found {actual_size})"
            )

        actual_hash = _sha256_file(destination_file, cancel_event)
        expected_source_hash, _ = expected_hashes[destination_file]
        if actual_hash != expected_source_hash:
            _remove_failed_copy(destination_file)
            raise RuntimeError(
                "Live game copy verification failed: "
                f"{relative_text} SHA-256 mismatch "
                f"(expected {expected_source_hash}, found {actual_hash})"
            )

        release_entry = release_hashes.get(_relative_key(relative_text))
        if release_entry is not None:
            if actual_size != release_entry["size"]:
                _remove_failed_copy(destination_file)
                raise RuntimeError(
                    "Live game copy release verification failed: "
                    f"{relative_text} size mismatch "
                    f"(expected {release_entry['size']}, found {actual_size})"
                )
            if actual_hash != release_entry["sha256"]:
                _remove_failed_copy(destination_file)
                raise RuntimeError(
                    "Live game copy release verification failed: "
                    f"{relative_text} SHA-256 mismatch "
                    f"(expected {release_entry['sha256']}, found {actual_hash})"
                )
        return relative_text

    verify_workers = max(1, min(worker_count, len(files) or 1))
    with ThreadPoolExecutor(max_workers=verify_workers) as executor:
        futures = {executor.submit(verify_one, item): item for item in files}
        for future in as_completed(futures):
            _raise_if_cancelled(cancel_event)
            try:
                relative_text = future.result()
            except Exception:
                for pending in futures:
                    pending.cancel()
                raise
            with verify_lock:
                verified += 1
                current = verified
            if on_progress is not None:
                on_progress(
                    "install:copy",
                    current,
                    verify_total,
                    f"verified {current}/{len(files)} source files",
                )

    # A resume destination should mirror the current Live file set. Unexpected
    # files mean the destination was changed independently or came from a stale
    # source state, so keep the state marker and require the user to resolve it.
    expected_relative_files = {
        os.path.normcase(os.path.relpath(_io_path(destination_file), _io_path(destination_path)))
        for _source_file, destination_file, _size, _relative_text in files
    }
    for root, _dirnames, filenames in os.walk(_io_path(destination_path)):
        _raise_if_cancelled(cancel_event)
        for name in filenames:
            if name == COPY_STATE_FILENAME:
                continue
            destination_file = Path(root) / name
            relative = os.path.normcase(
                os.path.relpath(_io_path(destination_file), _io_path(destination_path))
            )
            if relative not in expected_relative_files:
                raise RuntimeError(
                    f"Live game copy verification failed: unexpected file {relative}"
                )

    _raise_if_cancelled(cancel_event)
    try:
        os.unlink(_io_path(_state_path(destination_path)))
    except FileNotFoundError:
        pass
    if on_progress is not None:
        on_progress(
            "install:copy",
            1,
            1,
            "Live game copy complete",
        )
