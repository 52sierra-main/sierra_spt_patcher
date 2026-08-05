from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


MANIFEST_FORMAT_VERSION = 1
DEFAULT_CHUNK_SIZE = 256 * 1024 * 1024
PACKAGE_DIRS = ("patchfiles", "storage")


def _sha256_file(path: Path, block_size: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(block_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


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


@dataclass(frozen=True)
class PublishResult:
    manifest_path: Path
    object_root: Path
    file_count: int
    object_count: int
    new_object_count: int
    reused_object_count: int
    total_input_bytes: int


def publish_web_package(
    canonical_root: str | Path,
    repository_root: str | Path,
    package_id: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    on_progress: Callable[[str, int, int, str], None] | None = None,
) -> PublishResult:
    """Publish a canonical Sierra package into a web repository.

    Layout:
      repository_root/
        releases/<package_id>/manifest.json
        objects/<first-two-hash-chars>/<sha256>

    The shared object namespace is organizational. Objects are reused only
    when their bytes are exactly identical; no cross-version deduplication is
    assumed for Tarkov-derived high-entropy patch data.
    """

    canonical_root = Path(canonical_root).resolve()
    repository_root = Path(repository_root).resolve()
    package_id = _safe_package_id(package_id)

    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if not canonical_root.is_dir():
        raise FileNotFoundError(f"canonical package does not exist: {canonical_root}")

    release_dir = repository_root / "releases" / package_id
    object_root = repository_root / "objects"
    release_dir.mkdir(parents=True, exist_ok=True)
    object_root.mkdir(parents=True, exist_ok=True)

    package_files = list(_iter_package_files(canonical_root))
    manifest_files: list[dict] = []
    object_ids: set[str] = set()
    new_objects = 0
    reused_objects = 0
    total_input_bytes = 0

    for file_index, (logical_path, source_path) in enumerate(package_files, start=1):
        file_size = source_path.stat().st_size
        total_input_bytes += file_size
        file_hash = hashlib.sha256()
        objects: list[dict] = []

        with source_path.open("rb") as src:
            while True:
                chunk = src.read(chunk_size)
                if not chunk:
                    break

                file_hash.update(chunk)
                object_id = hashlib.sha256(chunk).hexdigest()
                object_path = object_root / object_id[:2] / object_id
                object_path.parent.mkdir(parents=True, exist_ok=True)

                if object_path.exists():
                    if (
                        object_path.stat().st_size != len(chunk)
                        or _sha256_file(object_path) != object_id
                    ):
                        raise RuntimeError(f"existing object is damaged: {object_path}")
                    reused_objects += 1
                else:
                    tmp = object_path.with_suffix(".tmp")
                    try:
                        with tmp.open("wb") as out:
                            out.write(chunk)
                            out.flush()
                            os.fsync(out.fileno())
                        os.replace(tmp, object_path)
                    finally:
                        tmp.unlink(missing_ok=True)
                    new_objects += 1

                object_ids.add(object_id)
                objects.append({"id": object_id, "size": len(chunk)})

        manifest_files.append(
            {
                "path": logical_path,
                "size": file_size,
                "sha256": file_hash.hexdigest(),
                "objects": objects,
            }
        )

        if on_progress:
            on_progress(
                "web:publish",
                file_index,
                len(package_files),
                f"Published {file_index}/{len(package_files)}: {logical_path}",
            )

    manifest = {
        "format_version": MANIFEST_FORMAT_VERSION,
        "package_id": package_id,
        "chunk_size": chunk_size,
        "files": manifest_files,
    }

    manifest_path = release_dir / "manifest.json"
    tmp_manifest = manifest_path.with_suffix(".json.tmp")
    tmp_manifest.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp_manifest, manifest_path)

    return PublishResult(
        manifest_path=manifest_path,
        object_root=object_root,
        file_count=len(package_files),
        object_count=len(object_ids),
        new_object_count=new_objects,
        reused_object_count=reused_objects,
        total_input_bytes=total_input_bytes,
    )
