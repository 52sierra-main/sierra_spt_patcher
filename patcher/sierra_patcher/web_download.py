from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


# Fixed first-party repository root. Manifests never supply URLs.
TRUSTED_REPOSITORY_BASE = "https://52sierra.net/patcher/repo/"

_OBJECT_RE = re.compile(r"^[0-9a-f]{64}$")
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class DownloadError(RuntimeError):
    pass


def _sha256_file(path: Path, block_size: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(block_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _verify_file(path: Path, expected_size: int | None, expected_sha256: str | None) -> bool:
    try:
        if expected_size is not None and path.stat().st_size != expected_size:
            return False
        if expected_sha256 and _sha256_file(path).lower() != expected_sha256.lower():
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
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")

    # A previous run may have finished the transfer but exited before rename.
    if part.exists() and _verify_file(part, expected_size, expected_sha256):
        os.replace(part, destination)
        return

    offset = part.stat().st_size if (resume and part.exists()) else 0
    headers = {"User-Agent": "SierraPatcher/1 web-delivery"}
    if offset:
        headers["Range"] = f"bytes={offset}-"

    req = urllib.request.Request(url, headers=headers, method="GET")
    opener = _opener()

    try:
        response = opener.open(req, timeout=30)
    except urllib.error.HTTPError as e:
        # Some origins refuse Range. Restart once from zero rather than append
        # a non-range response to a partial file.
        if offset and e.code in (400, 404, 416):
            part.unlink(missing_ok=True)
            return _stream_request(
                url,
                destination,
                expected_size=expected_size,
                expected_sha256=expected_sha256,
                resume=False,
                block_size=block_size,
                on_progress=on_progress,
            )
        raise DownloadError(f"HTTP error {e.code} for {url}") from e
    except OSError as e:
        raise DownloadError(f"download failed for {url}: {e}") from e

    status = getattr(response, "status", None)
    if offset and status != 206:
        response.close()
        part.unlink(missing_ok=True)
        return _stream_request(
            url,
            destination,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
            resume=False,
            block_size=block_size,
            on_progress=on_progress,
        )

    mode = "ab" if offset else "wb"
    current = offset
    with response, part.open(mode) as out:
        while True:
            block = response.read(block_size)
            if not block:
                break
            out.write(block)
            current += len(block)
            if on_progress:
                total = expected_size or max(current, 1)
                on_progress("web:download", current, total, destination.name)

    if not _verify_file(part, expected_size, expected_sha256):
        # Hash failures are not resumable because the stored bytes are not
        # trusted. Leave size-only failures as .part so a short transfer can
        # continue on the next attempt.
        if expected_sha256 and part.exists() and expected_size is not None and part.stat().st_size >= expected_size:
            part.unlink(missing_ok=True)
        raise DownloadError(f"download verification failed for {destination.name}")

    os.replace(part, destination)


def fetch_manifest(package_id: str, cache_root: str | Path) -> dict:
    package_id = _package_id(package_id)
    cache_root = Path(cache_root)
    manifest_path = cache_root / "manifests" / package_id / "manifest.json"

    _stream_request(_manifest_url(package_id), manifest_path, resume=False)

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise DownloadError("downloaded manifest is not valid JSON") from e

    if data.get("format_version") != 1:
        raise DownloadError(f"unsupported manifest version: {data.get('format_version')!r}")
    if data.get("package_id") != package_id:
        raise DownloadError("manifest package_id does not match requested package")
    if not isinstance(data.get("files"), list):
        raise DownloadError("manifest files must be a list")

    return data


def _safe_logical_path(value: str) -> Path:
    raw = Path(value)
    if raw.is_absolute() or ".." in raw.parts:
        raise DownloadError(f"unsafe package path: {value}")
    if not raw.parts or raw.parts[0] not in ("patchfiles", "storage"):
        raise DownloadError(f"unsupported package path: {value}")
    return raw


@dataclass(frozen=True)
class MaterializedPackage:
    root: Path
    patch_root: Path
    storage_root: Path
    manifest_path: Path


def materialize_web_package(
    package_id: str,
    cache_root: str | Path,
    *,
    on_progress: Callable[[str, int, int, str], None] | None = None,
) -> MaterializedPackage:
    cache_root = Path(cache_root).resolve()
    manifest = fetch_manifest(package_id, cache_root)

    object_cache = cache_root / "objects"
    package_root = cache_root / "packages" / _package_id(package_id)
    package_root.mkdir(parents=True, exist_ok=True)

    files = manifest["files"]
    for index, entry in enumerate(files, start=1):
        if not isinstance(entry, dict):
            raise DownloadError("invalid manifest file entry")

        rel = _safe_logical_path(str(entry.get("path", "")))
        expected_size = int(entry.get("size", -1))
        expected_hash = str(entry.get("sha256", "")).lower()
        objects = entry.get("objects")

        if expected_size < 0 or not _OBJECT_RE.fullmatch(expected_hash):
            raise DownloadError(f"invalid file metadata for {rel}")
        if not isinstance(objects, list):
            raise DownloadError(f"objects must be a list for {rel}")

        final_path = package_root / rel
        final_path.parent.mkdir(parents=True, exist_ok=True)

        if final_path.exists() and _verify_file(final_path, expected_size, expected_hash):
            if on_progress:
                on_progress("web:materialize", index, len(files), f"Cached: {rel}")
            continue

        temp_final = final_path.with_suffix(final_path.suffix + ".assembling")
        temp_final.unlink(missing_ok=True)

        file_hash = hashlib.sha256()
        written = 0
        with temp_final.open("wb") as assembled:
            for obj in objects:
                if not isinstance(obj, dict):
                    raise DownloadError(f"invalid object entry for {rel}")
                oid = _object_id(str(obj.get("id", "")))
                osize = int(obj.get("size", -1))
                if osize < 0:
                    raise DownloadError(f"invalid object size for {oid}")

                local_object = object_cache / oid[:2] / oid
                if not _verify_file(local_object, osize, oid):
                    local_object.unlink(missing_ok=True)
                    _stream_request(
                        _object_url(oid),
                        local_object,
                        expected_size=osize,
                        expected_sha256=oid,
                        resume=True,
                        on_progress=on_progress,
                    )

                with local_object.open("rb") as src:
                    while True:
                        block = src.read(4 * 1024 * 1024)
                        if not block:
                            break
                        assembled.write(block)
                        file_hash.update(block)
                        written += len(block)

        if written != expected_size or file_hash.hexdigest() != expected_hash:
            temp_final.unlink(missing_ok=True)
            raise DownloadError(f"reconstructed file verification failed: {rel}")

        os.replace(temp_final, final_path)

        if on_progress:
            on_progress("web:materialize", index, len(files), f"Ready: {rel}")

    manifest_path = cache_root / "manifests" / package_id / "manifest.json"
    return MaterializedPackage(
        root=package_root,
        patch_root=package_root / "patchfiles",
        storage_root=package_root / "storage",
        manifest_path=manifest_path,
    )


def clear_materialized_package(package_id: str, cache_root: str | Path) -> None:
    """Remove reconstructed package files while retaining verified object cache."""
    root = Path(cache_root) / "packages" / _package_id(package_id)
    shutil.rmtree(root, ignore_errors=True)
