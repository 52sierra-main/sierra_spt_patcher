from __future__ import annotations

from pathlib import Path, PurePosixPath

from . import web_delivery, web_download

HYBRID_PACKAGE_DIRS = ("patchfiles", "payloads", "storage")


def _safe_hybrid_logical_path(value: str) -> Path:
    if not value or "\\" in value:
        raise web_download.DownloadError(f"unsafe package path: {value}")
    raw = PurePosixPath(value)
    if raw.is_absolute() or ".." in raw.parts:
        raise web_download.DownloadError(f"unsafe package path: {value}")
    if any(part in ("", ".", "..") or ":" in part for part in raw.parts):
        raise web_download.DownloadError(f"unsafe package path: {value}")
    if not raw.parts or raw.parts[0] not in HYBRID_PACKAGE_DIRS:
        raise web_download.DownloadError(f"unsupported package path: {value}")
    return Path(*raw.parts)


def enable_hybrid_package_format() -> None:
    """Enable payloads/ as a first-class canonical/web package directory."""

    web_delivery.PACKAGE_DIRS = HYBRID_PACKAGE_DIRS
    web_download._safe_logical_path = _safe_hybrid_logical_path
