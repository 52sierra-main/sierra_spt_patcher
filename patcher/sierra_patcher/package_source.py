from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .paths import PATCH_read_DIR, STORAGE_read_DIR, WORKING_DIR
from .web_download import (
    DEFAULT_DOWNLOAD_WORKERS,
    DEFAULT_MATERIALIZE_WORKERS,
    MaterializedPackage,
    materialize_web_package,
)


@dataclass(frozen=True)
class PackageLayout:
    root: Path
    patch_root: Path
    storage_root: Path
    source_type: str


class LocalPackageSource:
    def prepare(self, on_progress=None) -> PackageLayout:
        root = Path(WORKING_DIR)
        return PackageLayout(
            root=root,
            patch_root=Path(PATCH_read_DIR),
            storage_root=Path(STORAGE_read_DIR),
            source_type="local",
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

    def prepare(
        self,
        on_progress: Callable[[str, int, int, str], None] | None = None,
    ) -> PackageLayout:
        materialized: MaterializedPackage = materialize_web_package(
            self.package_id,
            self.cache_root,
            download_workers=self.download_workers,
            materialize_workers=self.materialize_workers,
            on_progress=on_progress,
        )
        return PackageLayout(
            root=materialized.root,
            patch_root=materialized.patch_root,
            storage_root=materialized.storage_root,
            source_type="web",
        )
