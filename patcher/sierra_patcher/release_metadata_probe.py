from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .archived_snapshot import read_archived_snapshot
from .web_download import (
    DownloadError,
    _download_objects,
    _materialize_one_file,
    _package_id,
    _parse_manifest,
    fetch_manifest,
)

_METADATA_PATH = "storage/metadata.info"


def _metadata_version(raw: bytes) -> str | None:
    text = raw.decode("utf-8")
    if text.lstrip().startswith("{"):
        version = json.loads(text).get("version")
    else:
        lines = text.splitlines()
        version = lines[0] if lines else None
    version = str(version or "").strip()
    return version or None


def probe_release_live_version(
    package_id: str,
    cache_root: str | Path,
    *,
    cancel_event=None,
) -> str | None:
    """Download only a release manifest and metadata.info, then return its Live version."""

    cache = Path(cache_root).resolve()
    manifest = fetch_manifest(package_id, cache, cancel_event=cancel_event)
    metadata_entry = next(
        (
            item
            for item in manifest.get("files", [])
            if isinstance(item, dict) and item.get("path") == _METADATA_PATH
        ),
        None,
    )
    if metadata_entry is None:
        raise DownloadError(f"release does not contain {_METADATA_PATH}")
    files, object_sizes = _parse_manifest({"files": [metadata_entry]})
    metadata = files[0]

    object_cache = cache / "objects"
    _download_objects(
        object_sizes,
        object_cache,
        workers=2,
        on_progress=None,
        cancel_event=cancel_event,
    )

    package_root = cache / "packages" / _package_id(package_id)
    _materialize_one_file(
        metadata,
        package_root,
        object_cache,
        cancel_event,
    )
    metadata_path = package_root / _METADATA_PATH
    return _metadata_version(metadata_path.read_bytes())


def probe_archived_live_version(snapshot_root: str | Path) -> str | None:
    """Read metadata.info directly from an Archived snapshot object store."""

    info = read_archived_snapshot(snapshot_root)
    manifest = json.loads(info.manifest_path.read_text(encoding="utf-8"))
    metadata_entry = next(
        (
            item
            for item in manifest.get("files", [])
            if isinstance(item, dict) and item.get("path") == _METADATA_PATH
        ),
        None,
    )
    if metadata_entry is None:
        raise DownloadError(f"archived snapshot does not contain {_METADATA_PATH}")

    files, _ = _parse_manifest({"files": [metadata_entry]})
    metadata = files[0]
    chunks = []
    for object_spec in metadata.objects:
        object_path = info.object_root / object_spec.object_id[:2] / object_spec.object_id
        try:
            chunk = object_path.read_bytes()
        except OSError as exc:
            raise DownloadError(
                f"archived metadata object is missing: {object_spec.object_id}"
            ) from exc
        if len(chunk) != object_spec.size:
            raise DownloadError(
                f"archived metadata object has invalid size: {object_spec.object_id}"
            )
        chunks.append(chunk)

    raw = b"".join(chunks)
    if len(raw) != metadata.size or hashlib.sha256(raw).hexdigest() != metadata.sha256:
        raise DownloadError("archived metadata failed verification")
    return _metadata_version(raw)
