from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sierra_patcher import release_metadata_probe
from sierra_patcher.web_download import DownloadError


class ReleaseMetadataProbeTests(unittest.TestCase):
    def test_downloads_only_metadata_objects_and_returns_version(self) -> None:
        metadata = json.dumps({"version": "1.1.0.46699"}).encode("utf-8")
        patch = b"unused patch data"
        metadata_id = hashlib.sha256(metadata).hexdigest()
        patch_id = hashlib.sha256(patch).hexdigest()
        manifest = {
            "format_version": 1,
            "package_id": "3.11.4",
            "files": [
                {
                    "path": "payloads/example.zst",
                    "size": len(patch),
                    "sha256": patch_id,
                    "objects": [{"id": patch_id, "size": len(patch)}],
                },
                {
                    "path": "storage/metadata.info",
                    "size": len(metadata),
                    "sha256": metadata_id,
                    "objects": [{"id": metadata_id, "size": len(metadata)}],
                },
            ],
        }

        def download_objects(objects, object_cache, **_kwargs):
            self.assertEqual(objects, {metadata_id: len(metadata)})
            path = Path(object_cache) / metadata_id[:2] / metadata_id
            path.parent.mkdir(parents=True)
            path.write_bytes(metadata)

        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(
                release_metadata_probe,
                "fetch_manifest",
                return_value=manifest,
            ),
            mock.patch.object(
                release_metadata_probe,
                "_download_objects",
                side_effect=download_objects,
            ),
        ):
            version = release_metadata_probe.probe_release_live_version(
                "3.11.4",
                temporary,
            )

        self.assertEqual(version, "1.1.0.46699")

    def test_rejects_release_without_metadata(self) -> None:
        manifest = {
            "format_version": 1,
            "package_id": "3.11.4",
            "files": [],
        }
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            release_metadata_probe,
            "fetch_manifest",
            return_value=manifest,
        ):
            with self.assertRaises(DownloadError):
                release_metadata_probe.probe_release_live_version("3.11.4", temporary)


if __name__ == "__main__":
    unittest.main()
