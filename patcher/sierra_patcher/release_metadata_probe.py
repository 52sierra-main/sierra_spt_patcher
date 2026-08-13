from __future__ import annotations

import json
from pathlib import Path

from .web_download import (
    DownloadError,
    _download_objects,
    _materialize_one_file,
    _package_id,
    _parse_manifest,
    fetch_manifest,
)


_METADATA_PATH = "storage/metadata.info"


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
    raw = metadata_path.read_text(encoding="utf-8")
    if raw.lstrip().startswith("{"):
        version = json.loads(raw).get("version")
    else:
        lines = raw.splitlines()
        version = lines[0] if lines else None
    version = str(version or "").strip()
    return version or None
