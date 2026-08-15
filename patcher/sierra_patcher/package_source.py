from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .paths import PATCH_read_DIR, STORAGE_read_DIR, WORKING_DIR
from .web_download import (
    DEFAULT_DOWNLOAD_WORKERS,
    DEFAULT_MATERIALIZE_WORKERS,
    MaterializedPackage,
    is_storage_path,
    materialize_web_package,
)


@dataclass(frozen=True)
class PackageLayout:
    root: Path
    patch_root: Path
    payload_root: Path
    storage_root: Path
    source_type: str


def _layout(root: Path, patch_root: Path, storage_root: Path, source_type: str) -> PackageLayout:
    legacy = storage_root / "storage.sierra"
    if legacy.is_file():
        raise RuntimeError(
            "This release uses the retired storage.sierra/7-Zip format. "
            "Regenerate and republish it with the current Sierra Patcher before installing."
        )
    return PackageLayout(
        root=root,
        patch_root=patch_root,
        payload_root=root / "payloads",
        storage_root=storage_root,
        source_type=source_type,
    )


class LocalPackageSource:
    def prepare(self, on_progress=None, cancel_event=None) -> PackageLayout:
        root = Path(WORKING_DIR)
        return _layout(
            root,
            Path(PATCH_read_DIR),
            Path(STORAGE_read_DIR),
            "local",
        )


class WebPackageSource:
    def __init__(
        self,
        package_id: str,
        cache_root: str | Path,
        *,
        download_workers: int = DEFAULT_DOWNLOAD_WORKERS,
        materialize_workers: int = DEFAULT_MATERIALIZE_WORKERS,
    ):
        self.package_id = package_id
        self.cache_root = Path(cache_root)
        self.download_workers = download_workers
        self.materialize_workers = materialize_workers

    def prepare_storage(
        self,
        on_progress: Callable[[str, int, int, str], None] | None = None,
        cancel_event=None,
    ) -> Path:
        """Fetch only the package's small ``storage/`` tree.

        This is roughly 1/5000th of a release and carries source_hashes.json, so
        the destination can be verified before the full download is started.
        """

        materialized: MaterializedPackage = materialize_web_package(
            self.package_id,
            self.cache_root,
            download_workers=self.download_workers,
            materialize_workers=self.materialize_workers,
            on_progress=on_progress,
            cancel_event=cancel_event,
            path_filter=is_storage_path,
        )
        return materialized.storage_root

    def prepare(
        self,
        on_progress: Callable[[str, int, int, str], None] | None = None,
        cancel_event=None,
    ) -> PackageLayout:
        materialized: MaterializedPackage = materialize_web_package(
            self.package_id,
            self.cache_root,
            download_workers=self.download_workers,
            materialize_workers=self.materialize_workers,
            on_progress=on_progress,
            cancel_event=cancel_event,
        )
        return _layout(
            materialized.root,
            materialized.patch_root,
            materialized.storage_root,
            "web",
        )


class ArchivedSnapshotSource:
    def __init__(
        self,
        snapshot_root: str | Path,
        cache_root: str | Path,
        *,
        materialize_workers: int = DEFAULT_MATERIALIZE_WORKERS,
    ):
        self.snapshot_root = Path(snapshot_root)
        self.cache_root = Path(cache_root)
        self.materialize_workers = materialize_workers

    def prepare(
        self,
        on_progress: Callable[[str, int, int, str], None] | None = None,
        cancel_event=None,
    ) -> PackageLayout:
        from .archived_snapshot import materialize_archived_snapshot

        materialized: MaterializedPackage = materialize_archived_snapshot(
            self.snapshot_root,
            self.cache_root,
            materialize_workers=self.materialize_workers,
            on_progress=on_progress,
            cancel_event=cancel_event,
        )
        return _layout(
            materialized.root,
            materialized.patch_root,
            materialized.storage_root,
            "archived_snapshot",
        )
