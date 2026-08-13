from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class VersionPreflightStatus(str, Enum):
    READY = "ready"
    VERSION_CHECKING = "version_checking"
    UPDATE_REQUIRED = "update_required"
    PATCH_UPDATE_REQUIRED = "patch_update_required"
    SOURCE_MISMATCH = "source_mismatch"
    VERSION_UNKNOWN = "version_unknown"
    CATALOG_UNVERIFIED = "catalog_unverified"


_BLOCKING_STATUSES = {
    VersionPreflightStatus.VERSION_CHECKING,
    VersionPreflightStatus.UPDATE_REQUIRED,
    VersionPreflightStatus.PATCH_UPDATE_REQUIRED,
    VersionPreflightStatus.SOURCE_MISMATCH,
    VersionPreflightStatus.VERSION_UNKNOWN,
}


@dataclass(frozen=True)
class VersionPreflightResult:
    status: VersionPreflightStatus
    required_version: str | None
    live_version: str | None
    destination_version: str | None

    @property
    def blocks_download(self) -> bool:
        return self.status in _BLOCKING_STATUSES


def _clean_version(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def compare_numeric_versions(left: str, right: str) -> int | None:
    """Compare dotted numeric versions, returning None for unfamiliar formats."""

    left_parts = left.split(".")
    right_parts = right.split(".")
    if not left_parts or not right_parts:
        return None
    if any(not part.isdigit() for part in (*left_parts, *right_parts)):
        return None

    width = max(len(left_parts), len(right_parts))
    left_numbers = tuple(int(part) for part in left_parts) + (0,) * (width - len(left_parts))
    right_numbers = tuple(int(part) for part in right_parts) + (0,) * (width - len(right_parts))
    if left_numbers < right_numbers:
        return -1
    if left_numbers > right_numbers:
        return 1
    return 0


def evaluate_version_preflight(
    required_version: str | None,
    live_version: str | None,
    destination_version: str | None,
) -> VersionPreflightResult:
    """Evaluate the lightweight version check performed before package download.

    This check is intentionally separate from the exact source-hash preflight.
    Matching executable versions make the UI ready, while the downloaded
    package still performs the authoritative per-file verification.
    """

    required = _clean_version(required_version)
    live = _clean_version(live_version)
    destination = _clean_version(destination_version)

    if required is None:
        status = VersionPreflightStatus.CATALOG_UNVERIFIED
    elif live is None or destination is None:
        status = VersionPreflightStatus.VERSION_UNKNOWN
    elif live != required:
        comparison = compare_numeric_versions(live, required)
        if comparison is not None and comparison < 0:
            status = VersionPreflightStatus.UPDATE_REQUIRED
        elif comparison is not None and comparison > 0:
            status = VersionPreflightStatus.PATCH_UPDATE_REQUIRED
        else:
            status = VersionPreflightStatus.SOURCE_MISMATCH
    elif destination != required:
        status = VersionPreflightStatus.SOURCE_MISMATCH
    else:
        status = VersionPreflightStatus.READY

    return VersionPreflightResult(
        status=status,
        required_version=required,
        live_version=live,
        destination_version=destination,
    )
