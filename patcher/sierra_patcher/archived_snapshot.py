from __future__ import annotations

import json
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from . import web_download

ARCHIVED_SNAPSHOT_FORMAT_VERSION = 1
ARCHIVED_SNAPSHOT_MARKER = "archived_snapshot.json"


class ArchivedSnapshotError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArchivedSnapshotInfo:
    root: Path
    package_id: str
    manifest_path: Path
    object_root: Path


def _raise_if_cancelled(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ArchivedSnapshotError("Archived snapshot operation cancelled")


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(web_download._io_path(temp), web_download._io_path(path))
    finally:
        temp.unlink(missing_ok=True)


def read_archived_snapshot(snapshot_root: str | Path) -> ArchivedSnapshotInfo:
    root = Path(snapshot_root).resolve()
    marker_path = root / ARCHIVED_SNAPSHOT_MARKER
    if not marker_path.is_file():
        raise ArchivedSnapshotError(
            f"Not a Sierra Archived snapshot: {marker_path.name} is missing"
        )
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ArchivedSnapshotError("Archived snapshot marker is not valid JSON") from exc

    if marker.get("format_version") != ARCHIVED_SNAPSHOT_FORMAT_VERSION:
        raise ArchivedSnapshotError(
            f"Unsupported Archived snapshot version: {marker.get('format_version')!r}"
        )
    if marker.get("type") != "sierra_archived_snapshot":
        raise ArchivedSnapshotError("Folder is not a Sierra Archived snapshot")
    package_id = str(marker.get("package_id", "")).strip()
    if not package_id:
        raise ArchivedSnapshotError("Archived snapshot package_id is missing")

    manifest_path = root / "releases" / package_id / "manifest.json"
    object_root = root / "objects"
    if not manifest_path.is_file():
        raise ArchivedSnapshotError("Archived snapshot manifest is missing")
    if not object_root.is_dir():
        raise ArchivedSnapshotError("Archived snapshot object store is missing")

    return ArchivedSnapshotInfo(root, package_id, manifest_path, object_root)


def _load_manifest(info: ArchivedSnapshotInfo) -> dict:
    try:
        manifest = json.loads(info.manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ArchivedSnapshotError("Archived snapshot manifest is not valid JSON") from exc
    if manifest.get("format_version") != 1:
        raise ArchivedSnapshotError(
            f"Unsupported package manifest version: {manifest.get('format_version')!r}"
        )
    if manifest.get("package_id") != info.package_id:
        raise ArchivedSnapshotError("Archived snapshot manifest package_id mismatch")
    if not isinstance(manifest.get("files"), list):
        raise ArchivedSnapshotError("Archived snapshot manifest files must be a list")
    return manifest


def _copy_current_patcher(snapshot_root: Path) -> None:
    if not getattr(sys, "frozen", False):
        return
    source = Path(sys.executable).resolve()
    if not source.is_file():
        return
    destination = snapshot_root / source.name
    if source == destination:
        return
    shutil.copy2(source, destination)


def _prepare_archived_object_cache(
    objects_by_id: dict[str, int],
    object_root: Path,
    *,
    workers: int,
    on_progress=None,
    cancel_event=None,
) -> None:
    """Verify resumable local objects before trusting them as download cache."""

    existing = []
    for object_id, size in objects_by_id.items():
        path = object_root / object_id[:2] / object_id
        if web_download._exists(path):
            existing.append((object_id, size, path))
    if not existing:
        return

    max_workers = max(1, min(int(workers), 32))
    completed = 0

    def verify(item):
        object_id, size, path = item
        _raise_if_cancelled(cancel_event)
        if not web_download._verify_file(path, size, object_id, cancel_event):
            web_download._unlink(path)
            return object_id, False
        return object_id, True

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(verify, item): item[0] for item in existing}
        for future in as_completed(futures):
            _raise_if_cancelled(cancel_event)
            try:
                object_id, valid = future.result()
            except Exception:
                for pending in futures:
                    pending.cancel()
                raise
            completed += 1
            if on_progress:
                on_progress(
                    "archive:resume",
                    completed,
                    len(existing),
                    f"{'verified' if valid else 'discarded'} {object_id[:12]}",
                )


def archive_web_release(
    package_id: str,
    snapshot_root: str | Path,
    cache_root: str | Path,
    *,
    download_workers: int = web_download.DEFAULT_DOWNLOAD_WORKERS,
    on_progress=None,
    cancel_event=None,
    include_patcher: bool = True,
) -> ArchivedSnapshotInfo:
    """Download a release as an object-only portable Archived snapshot."""

    _raise_if_cancelled(cancel_event)
    root = Path(snapshot_root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    existing_marker = root / ARCHIVED_SNAPSHOT_MARKER
    if any(root.iterdir()) and not existing_marker.is_file():
        allowed = {"objects", "releases", "catalog.json"}
        if getattr(sys, "frozen", False):
            allowed.add(Path(sys.executable).name)
        unexpected = [item.name for item in root.iterdir() if item.name not in allowed]
        if unexpected:
            raise ArchivedSnapshotError(
                "Archived snapshot destination contains unrelated files: "
                + ", ".join(sorted(unexpected)[:5])
            )
    if existing_marker.is_file():
        existing = read_archived_snapshot(root)
        if existing.package_id != package_id:
            raise ArchivedSnapshotError(
                f"This folder already contains Archived snapshot {existing.package_id}."
            )

    if on_progress:
        on_progress("web:manifest", 0, 1, "Downloading manifest for Archived snapshot")
    manifest = web_download.fetch_manifest(package_id, cache_root, cancel_event=cancel_event)
    if on_progress:
        on_progress("web:manifest", 1, 1, "Manifest ready")

    _, objects_by_id = web_download._parse_manifest(manifest)
    _prepare_archived_object_cache(
        objects_by_id,
        root / "objects",
        workers=download_workers,
        on_progress=on_progress,
        cancel_event=cancel_event,
    )
    web_download._download_objects(
        objects_by_id,
        root / "objects",
        workers=download_workers,
        on_progress=on_progress,
        cancel_event=cancel_event,
    )
    _raise_if_cancelled(cancel_event)

    manifest_path = root / "releases" / package_id / "manifest.json"
    _atomic_json(manifest_path, manifest)
    _atomic_json(
        root / "catalog.json",
        {"format_version": 1, "releases": [{"id": package_id}]},
    )
    if include_patcher:
        _copy_current_patcher(root)

    _atomic_json(
        root / ARCHIVED_SNAPSHOT_MARKER,
        {
            "format_version": ARCHIVED_SNAPSHOT_FORMAT_VERSION,
            "type": "sierra_archived_snapshot",
            "package_id": package_id,
        },
    )
    return read_archived_snapshot(root)


def _verify_archived_objects(
    objects_by_id: dict[str, int],
    object_root: Path,
    *,
    workers: int,
    on_progress=None,
    cancel_event=None,
) -> None:
    if not objects_by_id:
        return
    max_workers = max(1, min(int(workers), 32))
    completed = 0

    def verify(item: tuple[str, int]) -> str:
        object_id, size = item
        _raise_if_cancelled(cancel_event)
        path = object_root / object_id[:2] / object_id
        if not web_download._exists(path):
            raise ArchivedSnapshotError(f"Archived object is missing: {object_id}")
        if not web_download._verify_file(path, size, object_id, cancel_event):
            raise ArchivedSnapshotError(f"Archived object failed SHA-256 verification: {object_id}")
        return object_id

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(verify, item): item[0] for item in objects_by_id.items()}
        for future in as_completed(futures):
            _raise_if_cancelled(cancel_event)
            try:
                object_id = future.result()
            except Exception:
                for pending in futures:
                    pending.cancel()
                raise
            completed += 1
            if on_progress:
                on_progress(
                    "archive:objects",
                    completed,
                    len(objects_by_id),
                    f"verified {completed}/{len(objects_by_id)} objects ({object_id[:12]})",
                )


def materialize_archived_snapshot(
    snapshot_root: str | Path,
    cache_root: str | Path,
    *,
    materialize_workers: int = web_download.DEFAULT_MATERIALIZE_WORKERS,
    on_progress=None,
    cancel_event=None,
):
    """Reconstruct an Archived snapshot into cache without modifying the snapshot."""

    info = read_archived_snapshot(snapshot_root)
    manifest = _load_manifest(info)
    files, objects_by_id = web_download._parse_manifest(manifest)
    _verify_archived_objects(
        objects_by_id,
        info.object_root,
        workers=materialize_workers,
        on_progress=on_progress,
        cancel_event=cancel_event,
    )

    package_root = Path(cache_root).resolve() / "packages" / info.package_id
    web_download._mkdir(package_root)
    web_download._materialize_files(
        files,
        package_root,
        info.object_root,
        workers=materialize_workers,
        on_progress=on_progress,
        cancel_event=cancel_event,
    )
    _raise_if_cancelled(cancel_event)
    return web_download.MaterializedPackage(
        root=package_root,
        patch_root=package_root / "patchfiles",
        storage_root=package_root / "storage",
        manifest_path=info.manifest_path,
    )
