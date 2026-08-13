from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .web_catalog import CatalogRelease, build_catalog, parse_release_catalog


MANIFEST_FORMAT_VERSION = 1
DEFAULT_CHUNK_SIZE = 256 * 1024 * 1024
DEFAULT_PUBLISH_WORKERS = min(8, max(2, os.cpu_count() or 4))
_IO_BLOCK_SIZE = 4 * 1024 * 1024
PACKAGE_DIRS = ("patchfiles", "storage")
_OBJECT_PROMOTION_ATTEMPTS = 9
_OBJECT_PROMOTION_LOCK = threading.Lock()


def _raise_if_cancelled(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("web package publishing cancelled")


def _safe_package_id(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("package_id must not be empty")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if any(ch not in allowed for ch in value):
        raise ValueError("package_id may only contain letters, numbers, '.', '_' and '-'")
    return value


def _iter_package_files(canonical_root: Path) -> Iterable[tuple[str, Path]]:
    for dirname in PACKAGE_DIRS:
        base = canonical_root / dirname
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file():
                yield path.relative_to(canonical_root).as_posix(), path


def _catalog_release_ids(repository_root: Path, current_package_id: str) -> list[str]:
    """Collect release IDs without opening any manifests."""
    result: list[str] = []
    seen: set[str] = set()

    catalog_path = repository_root / "catalog.json"
    if catalog_path.is_file():
        try:
            existing = json.loads(catalog_path.read_text(encoding="utf-8"))
            for item in existing.get("releases", []):
                release_id = item.get("id") if isinstance(item, dict) else item
                if isinstance(release_id, str):
                    release_id = release_id.strip()
                    if release_id and release_id not in seen:
                        seen.add(release_id)
                        result.append(release_id)
        except Exception:
            # Rebuild from the local releases directory if an old catalog is
            # damaged rather than blocking package publication.
            pass

    releases_root = repository_root / "releases"
    if releases_root.is_dir():
        for release_dir in sorted(releases_root.iterdir(), key=lambda p: p.name.lower()):
            if not release_dir.is_dir() or not (release_dir / "manifest.json").is_file():
                continue
            release_id = release_dir.name
            if release_id not in seen:
                seen.add(release_id)
                result.append(release_id)

    if current_package_id not in seen:
        result.append(current_package_id)
    return result


def _package_required_live_version(canonical_root: Path) -> str | None:
    metadata_root = canonical_root / "storage"
    metadata_path = next(metadata_root.glob("*.info"), None)
    if metadata_path is None:
        return None
    try:
        raw = metadata_path.read_text(encoding="utf-8")
        stripped = raw.lstrip()
        if stripped.startswith("{"):
            data = json.loads(raw)
            version = data.get("version")
        else:
            lines = raw.splitlines()
            version = lines[0] if lines else None
    except Exception:
        return None
    cleaned = str(version or "").strip()
    return cleaned or None


def _write_catalog(
    repository_root: Path,
    package_id: str,
    cancel_event=None,
    *,
    required_live_version: str | None = None,
) -> Path:
    _raise_if_cancelled(cancel_event)
    catalog_path = repository_root / "catalog.json"
    temp_catalog = repository_root / "catalog.json.tmp"
    existing_versions = {}
    if catalog_path.is_file():
        try:
            existing = json.loads(catalog_path.read_text(encoding="utf-8"))
            existing_versions = {
                release.id: release.required_live_version
                for release in parse_release_catalog(existing)
            }
        except Exception:
            pass
    existing_versions[package_id] = required_live_version
    releases = [
        CatalogRelease(release_id, existing_versions.get(release_id))
        for release_id in _catalog_release_ids(repository_root, package_id)
    ]
    data = build_catalog(releases)
    try:
        temp_catalog.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _raise_if_cancelled(cancel_event)
        os.replace(temp_catalog, catalog_path)
    finally:
        temp_catalog.unlink(missing_ok=True)
    return catalog_path


def _sha256_path(path: Path, cancel_event=None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            _raise_if_cancelled(cancel_event)
            block = stream.read(_IO_BLOCK_SIZE)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _wait_for_promotion_retry(attempt: int, cancel_event=None) -> None:
    # Freshly-created files can be held briefly by Defender/indexers on Windows.
    # Keep the total retry window bounded while giving those scanners time to
    # release their handle. The cancel event remains responsive during waits.
    delay = min(0.05 * (2 ** attempt), 0.8)
    if cancel_event is not None:
        if cancel_event.wait(delay):
            _raise_if_cancelled(cancel_event)
    else:
        time.sleep(delay)


def _promote_object(
    temp_path: Path,
    object_path: Path,
    object_id: str,
    chunk_bytes: int,
    cancel_event=None,
) -> bool:
    """Atomically publish one content-addressed object.

    Returns True when this call created the object and False when an identical
    object was already present. Promotion itself is serialized because multiple
    publisher workers can discover the same SHA at the same time. Windows AV or
    indexing software can also temporarily hold a freshly closed temp/object
    file, so access-denied rename failures are retried instead of aborting an
    otherwise successful generation.
    """

    with _OBJECT_PROMOTION_LOCK:
        _raise_if_cancelled(cancel_event)

        if object_path.exists():
            if object_path.stat().st_size != chunk_bytes:
                raise RuntimeError(f"existing object has wrong size: {object_path}")
            return False

        last_error: OSError | None = None
        for attempt in range(_OBJECT_PROMOTION_ATTEMPTS):
            _raise_if_cancelled(cancel_event)
            try:
                os.replace(temp_path, object_path)
                return True
            except OSError as exc:
                last_error = exc

                # Another process may have won the same content-addressed race
                # between our existence check and rename. In this exceptional
                # path, verify the actual SHA before treating it as reusable.
                if object_path.exists():
                    try:
                        if object_path.stat().st_size == chunk_bytes:
                            existing_hash = _sha256_path(object_path, cancel_event)
                            if existing_hash == object_id:
                                return False
                    except OSError:
                        # The same transient scanner lock may also block the
                        # verification read; retry below instead of failing yet.
                        pass

                if attempt + 1 < _OBJECT_PROMOTION_ATTEMPTS:
                    _wait_for_promotion_retry(attempt, cancel_event)

        raise RuntimeError(
            "could not promote web object after transient-lock retries: "
            f"{object_id} -> {object_path}"
        ) from last_error


@dataclass(frozen=True)
class PublishResult:
    manifest_path: Path
    object_root: Path
    catalog_path: Path
    file_count: int
    object_count: int
    new_object_count: int
    reused_object_count: int
    total_input_bytes: int


@dataclass(frozen=True)
class _PublishedFile:
    index: int
    manifest_entry: dict
    object_ids: tuple[str, ...]
    new_objects: int
    reused_objects: int
    input_bytes: int


def _publish_one_file(
    index: int,
    logical_path: str,
    source_path: Path,
    object_root: Path,
    temp_root: Path,
    chunk_size: int,
    cancel_event=None,
) -> _PublishedFile:
    """Publish one logical package file without buffering a full chunk in RAM."""

    _raise_if_cancelled(cancel_event)
    file_size = source_path.stat().st_size
    file_hash = hashlib.sha256()
    objects: list[dict] = []
    object_ids: list[str] = []
    new_objects = 0
    reused_objects = 0

    with source_path.open("rb") as src:
        while True:
            _raise_if_cancelled(cancel_event)
            temp_path = temp_root / (
                f"{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex}.tmp"
            )
            chunk_hash = hashlib.sha256()
            chunk_bytes = 0

            try:
                with temp_path.open("wb") as temp:
                    while chunk_bytes < chunk_size:
                        _raise_if_cancelled(cancel_event)
                        block = src.read(min(_IO_BLOCK_SIZE, chunk_size - chunk_bytes))
                        if not block:
                            break
                        temp.write(block)
                        file_hash.update(block)
                        chunk_hash.update(block)
                        chunk_bytes += len(block)

                if chunk_bytes == 0:
                    break

                _raise_if_cancelled(cancel_event)
                object_id = chunk_hash.hexdigest()
                object_path = object_root / object_id[:2] / object_id
                object_path.parent.mkdir(parents=True, exist_ok=True)

                created = _promote_object(
                    temp_path,
                    object_path,
                    object_id,
                    chunk_bytes,
                    cancel_event,
                )
                if created:
                    new_objects += 1
                else:
                    reused_objects += 1

                object_ids.append(object_id)
                objects.append({"id": object_id, "size": chunk_bytes})
            finally:
                temp_path.unlink(missing_ok=True)

    _raise_if_cancelled(cancel_event)
    return _PublishedFile(
        index=index,
        manifest_entry={
            "path": logical_path,
            "size": file_size,
            "sha256": file_hash.hexdigest(),
            "objects": objects,
        },
        object_ids=tuple(object_ids),
        new_objects=new_objects,
        reused_objects=reused_objects,
        input_bytes=file_size,
    )


def publish_web_package(
    canonical_root: str | Path,
    repository_root: str | Path,
    package_id: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    workers: int | None = None,
    on_progress: Callable[[str, int, int, str], None] | None = None,
    cancel_event=None,
) -> PublishResult:
    """Publish a canonical Sierra package into a web repository.

    Layout:
      repository_root/
        catalog.json
        releases/<package_id>/manifest.json
        objects/<first-two-hash-chars>/<sha256>

    catalog.json is intentionally tiny and contains release IDs plus an
    optional required Live version. Clients can perform a lightweight
    compatibility check without downloading every manifest.
    """

    _raise_if_cancelled(cancel_event)
    canonical_root = Path(canonical_root).resolve()
    repository_root = Path(repository_root).resolve()
    package_id = _safe_package_id(package_id)

    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if not canonical_root.is_dir():
        raise FileNotFoundError(f"canonical package does not exist: {canonical_root}")

    max_workers = max(1, min(int(workers or DEFAULT_PUBLISH_WORKERS), 32))
    release_dir = repository_root / "releases" / package_id
    object_root = repository_root / "objects"
    temp_root = object_root / ".tmp"
    release_dir.mkdir(parents=True, exist_ok=True)
    object_root.mkdir(parents=True, exist_ok=True)
    temp_root.mkdir(parents=True, exist_ok=True)

    package_files = list(_iter_package_files(canonical_root))
    results: list[_PublishedFile | None] = [None] * len(package_files)
    completed = 0

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _publish_one_file,
                    index,
                    logical_path,
                    source_path,
                    object_root,
                    temp_root,
                    chunk_size,
                    cancel_event,
                ): (index, logical_path)
                for index, (logical_path, source_path) in enumerate(package_files)
            }

            for future in as_completed(futures):
                if cancel_event is not None and cancel_event.is_set():
                    for pending in futures:
                        pending.cancel()
                    _raise_if_cancelled(cancel_event)

                index, logical_path = futures[future]
                try:
                    result = future.result()
                except Exception:
                    for pending in futures:
                        pending.cancel()
                    raise

                results[index] = result
                completed += 1
                if on_progress:
                    on_progress("web:publish", completed, len(package_files), logical_path)
    finally:
        for temp_path in temp_root.glob("*.tmp"):
            temp_path.unlink(missing_ok=True)
        try:
            temp_root.rmdir()
        except OSError:
            pass

    _raise_if_cancelled(cancel_event)
    published = [item for item in results if item is not None]
    if len(published) != len(package_files):
        raise RuntimeError("web package publishing ended before every file completed")

    manifest_files = [item.manifest_entry for item in published]
    object_ids = {oid for item in published for oid in item.object_ids}
    manifest = {
        "format_version": MANIFEST_FORMAT_VERSION,
        "package_id": package_id,
        "chunk_size": chunk_size,
        "files": manifest_files,
    }

    manifest_path = release_dir / "manifest.json"
    temp_manifest = manifest_path.with_suffix(".json.tmp")
    try:
        temp_manifest.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _raise_if_cancelled(cancel_event)
        os.replace(temp_manifest, manifest_path)
    finally:
        temp_manifest.unlink(missing_ok=True)

    # Publish/update the tiny version index only after the release manifest is
    # complete. When deploying to HFS, catalog.json should likewise be uploaded
    # after the release manifest so clients never discover a half-published ID.
    catalog_path = _write_catalog(
        repository_root,
        package_id,
        cancel_event,
        required_live_version=_package_required_live_version(canonical_root),
    )

    return PublishResult(
        manifest_path=manifest_path,
        object_root=object_root,
        catalog_path=catalog_path,
        file_count=len(published),
        object_count=len(object_ids),
        new_object_count=sum(item.new_objects for item in published),
        reused_object_count=sum(item.reused_objects for item in published),
        total_input_bytes=sum(item.input_bytes for item in published),
    )
