from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable


TRUSTED_REPOSITORY_BASE = "https://52sierra.net/patcher/repo/"
DOWNLOAD_ATTEMPTS = 3
DEFAULT_DOWNLOAD_WORKERS = 12
DEFAULT_MATERIALIZE_WORKERS = 8
_IO_BLOCK_SIZE = 4 * 1024 * 1024

_OBJECT_RE = re.compile(r"^[0-9a-f]{64}$")
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class DownloadError(RuntimeError):
    pass


def _raise_if_cancelled(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise DownloadError("web package preparation cancelled")


def _io_path(path: str | Path) -> str:
    value = os.path.abspath(os.fspath(path))
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _mkdir(path: str | Path) -> None:
    os.makedirs(_io_path(path), exist_ok=True)


def _exists(path: str | Path) -> bool:
    return os.path.exists(_io_path(path))


def _unlink(path: str | Path) -> None:
    try:
        os.unlink(_io_path(path))
    except FileNotFoundError:
        pass


def _size(path: str | Path) -> int:
    return os.path.getsize(_io_path(path))


def _sha256_file(path: str | Path, block_size: int = _IO_BLOCK_SIZE, cancel_event=None) -> str:
    h = hashlib.sha256()
    with open(_io_path(path), "rb") as stream:
        while True:
            _raise_if_cancelled(cancel_event)
            block = stream.read(block_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _verify_file(
    path: str | Path,
    expected_size: int | None,
    expected_sha256: str | None,
    cancel_event=None,
) -> bool:
    try:
        _raise_if_cancelled(cancel_event)
        if expected_size is not None and _size(path) != expected_size:
            return False
        if expected_sha256 and _sha256_file(path, cancel_event=cancel_event).lower() != expected_sha256.lower():
            return False
        return True
    except OSError:
        return False


class _TrustedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_host: str):
        super().__init__()
        self.allowed_host = allowed_host.lower()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() != self.allowed_host:
            raise DownloadError(f"refusing redirect outside trusted host: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _repository_parts() -> tuple[str, str]:
    parsed = urllib.parse.urlparse(TRUSTED_REPOSITORY_BASE)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise RuntimeError("TRUSTED_REPOSITORY_BASE must be an HTTPS URL")
    return TRUSTED_REPOSITORY_BASE.rstrip("/") + "/", parsed.hostname.lower()


def _opener():
    _, host = _repository_parts()
    return urllib.request.build_opener(_TrustedRedirectHandler(host))


def _package_id(value: str) -> str:
    if not _PACKAGE_RE.fullmatch(value or ""):
        raise ValueError("invalid package id")
    return value


def _object_id(value: str) -> str:
    value = (value or "").lower()
    if not _OBJECT_RE.fullmatch(value):
        raise ValueError("invalid object id")
    return value


def _manifest_url(package_id: str) -> str:
    base, _ = _repository_parts()
    return urllib.parse.urljoin(base, f"releases/{_package_id(package_id)}/manifest.json")


def _object_url(object_id: str) -> str:
    object_id = _object_id(object_id)
    base, _ = _repository_parts()
    return urllib.parse.urljoin(base, f"objects/{object_id[:2]}/{object_id}")


def _stream_request(
    url: str,
    destination: Path,
    *,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
    resume: bool = True,
    block_size: int = 1024 * 1024,
    on_progress: Callable[[str, int, int, str], None] | None = None,
    cancel_event=None,
) -> None:
    _raise_if_cancelled(cancel_event)
    _mkdir(destination.parent)
    part = destination.with_suffix(destination.suffix + ".part")

    if (
        _exists(part)
        and (expected_size is not None or expected_sha256 is not None)
        and _verify_file(part, expected_size, expected_sha256, cancel_event)
    ):
        os.replace(_io_path(part), _io_path(destination))
        return

    offset = _size(part) if (resume and _exists(part)) else 0
    headers = {
        "User-Agent": "SierraPatcher/1 web-delivery",
        "Accept-Encoding": "identity",
    }
    if offset:
        headers["Range"] = f"bytes={offset}-"

    request = urllib.request.Request(url, headers=headers, method="GET")
    _raise_if_cancelled(cancel_event)
    try:
        response = _opener().open(request, timeout=30)
    except urllib.error.HTTPError as exc:
        if offset and exc.code in (400, 404, 416):
            _unlink(part)
            return _stream_request(
                url,
                destination,
                expected_size=expected_size,
                expected_sha256=expected_sha256,
                resume=False,
                block_size=block_size,
                on_progress=on_progress,
                cancel_event=cancel_event,
            )
        raise DownloadError(f"HTTP error {exc.code} for {url}") from exc
    except OSError as exc:
        raise DownloadError(f"download failed for {url}: {exc}") from exc

    status = getattr(response, "status", None)
    content_range = response.headers.get("Content-Range", "")
    if offset and (status != 206 or not content_range.startswith(f"bytes {offset}-")):
        response.close()
        _unlink(part)
        return _stream_request(
            url,
            destination,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
            resume=False,
            block_size=block_size,
            on_progress=on_progress,
            cancel_event=cancel_event,
        )

    mode = "ab" if offset else "wb"
    current = offset
    try:
        with response, open(_io_path(part), mode) as output:
            while True:
                _raise_if_cancelled(cancel_event)
                block = response.read(block_size)
                if not block:
                    break
                output.write(block)
                current += len(block)
                if on_progress:
                    on_progress(
                        "web:download-bytes",
                        current,
                        expected_size or max(current, 1),
                        destination.name,
                    )
    except DownloadError:
        raise
    except Exception as exc:
        raise DownloadError(f"stream interrupted for {destination.name}: {exc}") from exc

    _raise_if_cancelled(cancel_event)
    if not _verify_file(part, expected_size, expected_sha256, cancel_event):
        if (
            expected_sha256
            and _exists(part)
            and expected_size is not None
            and _size(part) >= expected_size
        ):
            _unlink(part)
        raise DownloadError(f"download verification failed for {destination.name}")

    os.replace(_io_path(part), _io_path(destination))


def _download_with_retries(
    url: str,
    destination: Path,
    *,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
    resume: bool = True,
    on_progress: Callable[[str, int, int, str], None] | None = None,
    cancel_event=None,
) -> None:
    last_error: Exception | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        _raise_if_cancelled(cancel_event)
        try:
            _stream_request(
                url,
                destination,
                expected_size=expected_size,
                expected_sha256=expected_sha256,
                resume=resume,
                on_progress=on_progress,
                cancel_event=cancel_event,
            )
            return
        except DownloadError as exc:
            last_error = exc
            _raise_if_cancelled(cancel_event)
            if attempt < DOWNLOAD_ATTEMPTS:
                delay = min(2 ** (attempt - 1), 4)
                if cancel_event is not None:
                    if cancel_event.wait(delay):
                        _raise_if_cancelled(cancel_event)
                else:
                    time.sleep(delay)
    raise DownloadError(
        f"download failed after {DOWNLOAD_ATTEMPTS} attempts: {destination.name}"
    ) from last_error


def fetch_manifest(package_id: str, cache_root: str | Path, *, cancel_event=None) -> dict:
    _raise_if_cancelled(cancel_event)
    package_id = _package_id(package_id)
    cache_root = Path(cache_root)
    manifest_path = cache_root / "manifests" / package_id / "manifest.json"

    _download_with_retries(
        _manifest_url(package_id),
        manifest_path,
        resume=False,
        cancel_event=cancel_event,
    )

    try:
        with open(_io_path(manifest_path), "r", encoding="utf-8") as stream:
            data = json.load(stream)
    except Exception as exc:
        raise DownloadError("downloaded manifest is not valid JSON") from exc

    if data.get("format_version") != 1:
        raise DownloadError(f"unsupported manifest version: {data.get('format_version')!r}")
    if data.get("package_id") != package_id:
        raise DownloadError("manifest package_id does not match requested package")
    if not isinstance(data.get("files"), list):
        raise DownloadError("manifest files must be a list")
    return data


def _safe_logical_path(value: str) -> Path:
    if not value or "\\" in value:
        raise DownloadError(f"unsafe package path: {value}")
    raw = PurePosixPath(value)
    if raw.is_absolute() or ".." in raw.parts:
        raise DownloadError(f"unsafe package path: {value}")
    if any(part in ("", ".", "..") or ":" in part for part in raw.parts):
        raise DownloadError(f"unsafe package path: {value}")
    if not raw.parts or raw.parts[0] not in ("patchfiles", "storage"):
        raise DownloadError(f"unsupported package path: {value}")
    return Path(*raw.parts)


@dataclass(frozen=True)
class _ObjectSpec:
    object_id: str
    size: int


@dataclass(frozen=True)
class _FileSpec:
    path: Path
    size: int
    sha256: str
    objects: tuple[_ObjectSpec, ...]


@dataclass(frozen=True)
class MaterializedPackage:
    root: Path
    patch_root: Path
    storage_root: Path
    manifest_path: Path


def _parse_manifest(manifest: dict) -> tuple[list[_FileSpec], dict[str, int]]:
    files: list[_FileSpec] = []
    objects_by_id: dict[str, int] = {}
    seen_paths: set[str] = set()

    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise DownloadError("invalid manifest file entry")
        rel = _safe_logical_path(str(entry.get("path", "")))
        rel_key = rel.as_posix().lower()
        if rel_key in seen_paths:
            raise DownloadError(f"duplicate package path in manifest: {rel}")
        seen_paths.add(rel_key)

        try:
            expected_size = int(entry.get("size", -1))
        except (TypeError, ValueError) as exc:
            raise DownloadError(f"invalid file size for {rel}") from exc
        expected_hash = str(entry.get("sha256", "")).lower()
        raw_objects = entry.get("objects")
        if expected_size < 0 or not _OBJECT_RE.fullmatch(expected_hash):
            raise DownloadError(f"invalid file metadata for {rel}")
        if not isinstance(raw_objects, list):
            raise DownloadError(f"objects must be a list for {rel}")

        object_specs: list[_ObjectSpec] = []
        object_bytes = 0
        for raw_object in raw_objects:
            if not isinstance(raw_object, dict):
                raise DownloadError(f"invalid object entry for {rel}")
            object_id = _object_id(str(raw_object.get("id", "")))
            try:
                object_size = int(raw_object.get("size", -1))
            except (TypeError, ValueError) as exc:
                raise DownloadError(f"invalid object size for {object_id}") from exc
            if object_size <= 0:
                raise DownloadError(f"invalid object size for {object_id}")
            prior_size = objects_by_id.get(object_id)
            if prior_size is not None and prior_size != object_size:
                raise DownloadError(f"conflicting object sizes for {object_id}")
            objects_by_id[object_id] = object_size
            object_specs.append(_ObjectSpec(object_id, object_size))
            object_bytes += object_size

        if object_bytes != expected_size:
            raise DownloadError(
                f"object sizes do not reconstruct {rel}: expected {expected_size}, manifest has {object_bytes}"
            )
        files.append(_FileSpec(rel, expected_size, expected_hash, tuple(object_specs)))
    return files, objects_by_id


class _ObjectProgress:
    def __init__(self, object_sizes: dict[str, int], callback):
        self._sizes = object_sizes
        self._callback = callback
        self._values: dict[str, int] = {}
        self._completed: set[str] = set()
        self._current_bytes = 0
        self._total_bytes = sum(object_sizes.values())
        self._lock = threading.Lock()

    def update(self, object_id: str, current: int, message: str = "") -> None:
        if not self._callback:
            return
        expected = self._sizes[object_id]
        value = max(0, min(int(current), expected))
        with self._lock:
            previous = self._values.get(object_id, 0)
            self._values[object_id] = value
            self._current_bytes += value - previous
            if value >= expected:
                self._completed.add(object_id)
            completed = len(self._completed)
            total_objects = len(self._sizes)
            current_bytes = self._current_bytes
            total_bytes = max(self._total_bytes, 1)
        self._callback(
            "web:objects",
            current_bytes,
            total_bytes,
            f"{completed}/{total_objects} objects {message}".strip(),
        )

    def complete(self, object_id: str, message: str = "") -> None:
        self.update(object_id, self._sizes[object_id], message)


def _ensure_object(
    object_id: str,
    object_size: int,
    object_cache: Path,
    progress: _ObjectProgress,
    cancel_event=None,
) -> str:
    _raise_if_cancelled(cancel_event)
    destination = object_cache / object_id[:2] / object_id
    if _exists(destination) and _size(destination) == object_size:
        progress.complete(object_id, "cached")
        return "cached"
    if _exists(destination):
        _unlink(destination)

    def object_progress(_phase: str, current: int, _total: int, _message: str) -> None:
        progress.update(object_id, current, destination.name[:12])

    _download_with_retries(
        _object_url(object_id),
        destination,
        expected_size=object_size,
        expected_sha256=object_id,
        resume=True,
        on_progress=object_progress,
        cancel_event=cancel_event,
    )
    progress.complete(object_id, "downloaded")
    return "downloaded"


def _download_objects(
    objects_by_id: dict[str, int],
    object_cache: Path,
    *,
    workers: int,
    on_progress,
    cancel_event=None,
) -> None:
    _raise_if_cancelled(cancel_event)
    if not objects_by_id:
        if on_progress:
            on_progress("web:objects", 1, 1, "No objects required")
        return
    max_workers = max(1, min(int(workers), 64))
    progress = _ObjectProgress(objects_by_id, on_progress)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _ensure_object,
                object_id,
                object_size,
                object_cache,
                progress,
                cancel_event,
            ): object_id
            for object_id, object_size in objects_by_id.items()
        }
        for future in as_completed(futures):
            if cancel_event is not None and cancel_event.is_set():
                for pending in futures:
                    pending.cancel()
                _raise_if_cancelled(cancel_event)
            try:
                future.result()
            except Exception:
                for pending in futures:
                    pending.cancel()
                raise


def _materialize_one_file(
    spec: _FileSpec,
    package_root: Path,
    object_cache: Path,
    cancel_event=None,
) -> str:
    _raise_if_cancelled(cancel_event)
    final_path = package_root / spec.path
    _mkdir(final_path.parent)

    if _exists(final_path) and _verify_file(final_path, spec.size, spec.sha256, cancel_event):
        return "cached"

    temp_path = final_path.with_name(
        f"{final_path.name}.assembling-{os.getpid()}-{threading.get_ident()}"
    )
    _unlink(temp_path)
    file_hash = hashlib.sha256()
    written = 0
    try:
        with open(_io_path(temp_path), "wb") as assembled:
            for object_spec in spec.objects:
                _raise_if_cancelled(cancel_event)
                local_object = object_cache / object_spec.object_id[:2] / object_spec.object_id
                if not _exists(local_object) or _size(local_object) != object_spec.size:
                    raise DownloadError(f"required object is missing: {object_spec.object_id}")
                with open(_io_path(local_object), "rb") as source:
                    while True:
                        _raise_if_cancelled(cancel_event)
                        block = source.read(_IO_BLOCK_SIZE)
                        if not block:
                            break
                        assembled.write(block)
                        file_hash.update(block)
                        written += len(block)

        _raise_if_cancelled(cancel_event)
        if written != spec.size or file_hash.hexdigest() != spec.sha256:
            raise DownloadError(f"reconstructed file verification failed: {spec.path}")
        os.replace(_io_path(temp_path), _io_path(final_path))
        return "ready"
    finally:
        _unlink(temp_path)


def _materialize_files(
    files: list[_FileSpec],
    package_root: Path,
    object_cache: Path,
    *,
    workers: int,
    on_progress,
    cancel_event=None,
) -> None:
    _raise_if_cancelled(cancel_event)
    if not files:
        if on_progress:
            on_progress("web:materialize", 1, 1, "No package files")
        return
    max_workers = max(1, min(int(workers), 32))
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_materialize_one_file, spec, package_root, object_cache, cancel_event): spec
            for spec in files
        }
        for future in as_completed(futures):
            if cancel_event is not None and cancel_event.is_set():
                for pending in futures:
                    pending.cancel()
                _raise_if_cancelled(cancel_event)
            spec = futures[future]
            try:
                result = future.result()
            except Exception:
                for pending in futures:
                    pending.cancel()
                raise
            completed += 1
            if on_progress:
                on_progress(
                    "web:materialize",
                    completed,
                    len(files),
                    f"{result}: {spec.path.as_posix()}",
                )


def materialize_web_package(
    package_id: str,
    cache_root: str | Path,
    *,
    download_workers: int = DEFAULT_DOWNLOAD_WORKERS,
    materialize_workers: int = DEFAULT_MATERIALIZE_WORKERS,
    on_progress: Callable[[str, int, int, str], None] | None = None,
    cancel_event=None,
) -> MaterializedPackage:
    _raise_if_cancelled(cancel_event)
    cache_root = Path(cache_root).resolve()
    if on_progress:
        on_progress("web:manifest", 0, 1, "Downloading manifest")
    manifest = fetch_manifest(package_id, cache_root, cancel_event=cancel_event)
    if on_progress:
        on_progress("web:manifest", 1, 1, "Manifest ready")

    files, objects_by_id = _parse_manifest(manifest)
    object_cache = cache_root / "objects"
    package_root = cache_root / "packages" / _package_id(package_id)
    _mkdir(package_root)

    _download_objects(
        objects_by_id,
        object_cache,
        workers=download_workers,
        on_progress=on_progress,
        cancel_event=cancel_event,
    )
    _materialize_files(
        files,
        package_root,
        object_cache,
        workers=materialize_workers,
        on_progress=on_progress,
        cancel_event=cancel_event,
    )

    _raise_if_cancelled(cancel_event)
    manifest_path = cache_root / "manifests" / package_id / "manifest.json"
    return MaterializedPackage(
        root=package_root,
        patch_root=package_root / "patchfiles",
        storage_root=package_root / "storage",
        manifest_path=manifest_path,
    )


def clear_materialized_package(package_id: str, cache_root: str | Path) -> None:
    root = Path(cache_root) / "packages" / _package_id(package_id)
    shutil.rmtree(_io_path(root), ignore_errors=True)
