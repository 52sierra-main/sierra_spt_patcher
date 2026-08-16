from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from .package_format import HYBRID_PACKAGE_DIRS
from .web_catalog import CatalogRelease, build_catalog
from .web_delivery import _promote_object


METADATA_LOGICAL_PATH = "storage/metadata.info"
_IO_BLOCK_SIZE = 4 * 1024 * 1024


class RepositoryToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseVerification:
    package_id: str
    file_count: int
    object_references: int
    total_logical_bytes: int


def _raise_if_cancelled(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise RepositoryToolError("repository operation cancelled")


def _safe_release_id(value: str) -> str:
    release_id = str(value).strip()
    if not release_id:
        raise RepositoryToolError("release ID is required")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if any(ch not in allowed for ch in release_id):
        raise RepositoryToolError("release ID contains unsupported characters")
    return release_id


def _safe_logical_path(value: str) -> PurePosixPath:
    if not value or "\\" in value:
        raise RepositoryToolError(f"unsafe logical path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise RepositoryToolError(f"unsafe logical path: {value!r}")
    if not path.parts or path.parts[0] not in HYBRID_PACKAGE_DIRS:
        raise RepositoryToolError(f"unsupported logical path: {value!r}")
    return path


def _valid_object_id(value: str) -> bool:
    if len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    last_error: OSError | None = None
    try:
        for attempt in range(7):
            try:
                os.replace(temp, path)
                return
            except OSError as exc:
                last_error = exc
                if attempt < 6:
                    time.sleep(min(0.05 * (2**attempt), 0.8))
        raise RepositoryToolError(f"could not atomically update {path}") from last_error
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def list_releases(repository_root: str | Path) -> list[str]:
    root = Path(repository_root).resolve()
    releases_root = root / "releases"
    if not releases_root.is_dir():
        return []
    result: list[str] = []
    for directory in sorted(releases_root.iterdir(), key=lambda p: p.name.lower()):
        if directory.is_dir() and (directory / "manifest.json").is_file():
            result.append(directory.name)
    return result


def load_manifest(repository_root: str | Path, package_id: str) -> dict:
    root = Path(repository_root).resolve()
    package_id = _safe_release_id(package_id)
    manifest_path = root / "releases" / package_id / "manifest.json"
    if not manifest_path.is_file():
        raise RepositoryToolError(f"release manifest not found: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RepositoryToolError("release manifest is not valid JSON") from exc
    if manifest.get("format_version") != 1:
        raise RepositoryToolError(
            f"unsupported manifest format: {manifest.get('format_version')!r}"
        )
    if manifest.get("package_id") != package_id:
        raise RepositoryToolError("manifest package_id does not match selected release")
    if not isinstance(manifest.get("files"), list):
        raise RepositoryToolError("manifest files must be a list")
    return manifest


def _entry_for_path(manifest: dict, logical_path: str) -> dict:
    for entry in manifest.get("files", []):
        if isinstance(entry, dict) and entry.get("path") == logical_path:
            return entry
    raise RepositoryToolError(f"release does not contain {logical_path}")


def _read_entry_bytes(
    repository_root: Path,
    entry: dict,
    *,
    cancel_event=None,
) -> bytes:
    logical_path = str(entry.get("path", ""))
    _safe_logical_path(logical_path)
    expected_size = entry.get("size")
    expected_hash = str(entry.get("sha256", "")).lower()
    objects = entry.get("objects")
    if not isinstance(expected_size, int) or expected_size < 0:
        raise RepositoryToolError(f"invalid size for {logical_path}")
    if not _valid_object_id(expected_hash):
        raise RepositoryToolError(f"invalid SHA-256 for {logical_path}")
    if not isinstance(objects, list):
        raise RepositoryToolError(f"invalid object list for {logical_path}")

    output = bytearray()
    for obj in objects:
        _raise_if_cancelled(cancel_event)
        if not isinstance(obj, dict):
            raise RepositoryToolError(f"invalid object entry for {logical_path}")
        object_id = str(obj.get("id", "")).lower()
        object_size = obj.get("size")
        if not _valid_object_id(object_id) or not isinstance(object_size, int) or object_size < 0:
            raise RepositoryToolError(f"invalid object metadata for {logical_path}")
        object_path = repository_root / "objects" / object_id[:2] / object_id
        if not object_path.is_file():
            raise RepositoryToolError(f"missing repository object: {object_id}")
        data = object_path.read_bytes()
        if len(data) != object_size:
            raise RepositoryToolError(f"object size mismatch: {object_id}")
        if hashlib.sha256(data).hexdigest() != object_id:
            raise RepositoryToolError(f"object SHA-256 mismatch: {object_id}")
        output.extend(data)

    payload = bytes(output)
    if len(payload) != expected_size:
        raise RepositoryToolError(f"logical file size mismatch: {logical_path}")
    if hashlib.sha256(payload).hexdigest() != expected_hash:
        raise RepositoryToolError(f"logical file SHA-256 mismatch: {logical_path}")
    return payload


def load_release_metadata(repository_root: str | Path, package_id: str) -> dict:
    root = Path(repository_root).resolve()
    manifest = load_manifest(root, package_id)
    payload = _read_entry_bytes(root, _entry_for_path(manifest, METADATA_LOGICAL_PATH))
    try:
        metadata = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise RepositoryToolError(
            "metadata.info is not JSON; this editor supports current-format releases only"
        ) from exc
    if not isinstance(metadata, dict):
        raise RepositoryToolError("metadata.info must contain a JSON object")
    return metadata


def update_release_metadata(
    repository_root: str | Path,
    package_id: str,
    metadata: dict,
    *,
    cancel_event=None,
) -> str:
    if not isinstance(metadata, dict):
        raise RepositoryToolError("metadata must be a JSON object")
    _raise_if_cancelled(cancel_event)
    root = Path(repository_root).resolve()
    package_id = _safe_release_id(package_id)
    manifest = load_manifest(root, package_id)
    entry = _entry_for_path(manifest, METADATA_LOGICAL_PATH)

    payload = json.dumps(metadata, indent=2, ensure_ascii=False).encode("utf-8")
    object_id = hashlib.sha256(payload).hexdigest()
    object_path = root / "objects" / object_id[:2] / object_id
    object_path.parent.mkdir(parents=True, exist_ok=True)

    if object_path.is_file():
        existing = object_path.read_bytes()
        if existing != payload:
            raise RepositoryToolError(
                "content-addressed metadata object exists but its contents do not match its SHA"
            )
    else:
        temp_root = root / "objects" / ".tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        temp_path = temp_root / f"metadata-{uuid.uuid4().hex}.tmp"
        try:
            temp_path.write_bytes(payload)
            _promote_object(
                temp_path,
                object_path,
                object_id,
                len(payload),
                cancel_event,
            )
        finally:
            temp_path.unlink(missing_ok=True)
            try:
                temp_root.rmdir()
            except OSError:
                pass

    entry.clear()
    entry.update(
        {
            "path": METADATA_LOGICAL_PATH,
            "size": len(payload),
            "sha256": object_id,
            "objects": [{"id": object_id, "size": len(payload)}],
        }
    )

    manifest_path = root / "releases" / package_id / "manifest.json"
    _atomic_json(manifest_path, manifest)
    return object_id


def rebuild_catalog(repository_root: str | Path) -> tuple[Path, list[str]]:
    root = Path(repository_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    releases = list_releases(root)
    catalog_releases: list[CatalogRelease] = []
    for release in releases:
        try:
            metadata = load_release_metadata(root, release)
            required_live_version = str(metadata.get("version") or "").strip() or None
        except RepositoryToolError:
            required_live_version = None
        catalog_releases.append(CatalogRelease(release, required_live_version))
    catalog_path = root / "catalog.json"
    _atomic_json(catalog_path, build_catalog(catalog_releases))
    return catalog_path, releases


def verify_release(
    repository_root: str | Path,
    package_id: str,
    *,
    on_progress: Callable[[int, int, str], None] | None = None,
    cancel_event=None,
) -> ReleaseVerification:
    root = Path(repository_root).resolve()
    package_id = _safe_release_id(package_id)
    manifest = load_manifest(root, package_id)
    files = manifest.get("files", [])
    total_files = len(files)
    object_references = 0
    logical_bytes = 0

    for index, entry in enumerate(files, 1):
        _raise_if_cancelled(cancel_event)
        if not isinstance(entry, dict):
            raise RepositoryToolError(f"manifest file entry {index} is invalid")
        logical_path = str(entry.get("path", ""))
        _safe_logical_path(logical_path)
        expected_size = entry.get("size")
        expected_hash = str(entry.get("sha256", "")).lower()
        objects = entry.get("objects")
        if not isinstance(expected_size, int) or expected_size < 0:
            raise RepositoryToolError(f"invalid logical size: {logical_path}")
        if not _valid_object_id(expected_hash):
            raise RepositoryToolError(f"invalid logical SHA-256: {logical_path}")
        if not isinstance(objects, list):
            raise RepositoryToolError(f"invalid object list: {logical_path}")

        file_hash = hashlib.sha256()
        file_bytes = 0
        for obj in objects:
            _raise_if_cancelled(cancel_event)
            if not isinstance(obj, dict):
                raise RepositoryToolError(f"invalid object entry: {logical_path}")
            object_id = str(obj.get("id", "")).lower()
            object_size = obj.get("size")
            if not _valid_object_id(object_id) or not isinstance(object_size, int) or object_size < 0:
                raise RepositoryToolError(f"invalid object metadata: {logical_path}")
            object_path = root / "objects" / object_id[:2] / object_id
            if not object_path.is_file():
                raise RepositoryToolError(f"missing object {object_id} for {logical_path}")

            object_hash = hashlib.sha256()
            object_bytes = 0
            with object_path.open("rb") as stream:
                while True:
                    _raise_if_cancelled(cancel_event)
                    block = stream.read(_IO_BLOCK_SIZE)
                    if not block:
                        break
                    object_hash.update(block)
                    file_hash.update(block)
                    object_bytes += len(block)
                    file_bytes += len(block)

            if object_bytes != object_size:
                raise RepositoryToolError(f"object size mismatch: {object_id}")
            if object_hash.hexdigest() != object_id:
                raise RepositoryToolError(f"object SHA-256 mismatch: {object_id}")
            object_references += 1

        if file_bytes != expected_size:
            raise RepositoryToolError(f"logical file size mismatch: {logical_path}")
        if file_hash.hexdigest() != expected_hash:
            raise RepositoryToolError(f"logical file SHA-256 mismatch: {logical_path}")
        logical_bytes += file_bytes
        if on_progress:
            on_progress(index, max(total_files, 1), logical_path)

    return ReleaseVerification(
        package_id=package_id,
        file_count=total_files,
        object_references=object_references,
        total_logical_bytes=logical_bytes,
    )
