from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sierra_patcher import repository_tools, web_catalog, web_delivery
from sierra_patcher.web_catalog import CatalogRelease


class WebCatalogTests(unittest.TestCase):
    def test_parses_legacy_and_extended_release_entries(self) -> None:
        releases = web_catalog.parse_release_catalog(
            {
                "format_version": 1,
                "releases": [
                    "3.10.0",
                    {"id": "3.11.4", "required_live_version": "1.1.0.46699"},
                ],
            }
        )
        self.assertEqual(
            releases,
            [
                CatalogRelease("3.10.0"),
                CatalogRelease("3.11.4", "1.1.0.46699"),
            ],
        )

    def test_builds_legacy_catalog_without_new_required_field(self) -> None:
        self.assertEqual(
            web_catalog.build_catalog(release_ids=["3.10.0"]),
            {"format_version": 1, "releases": [{"id": "3.10.0"}]},
        )

    def test_builds_extended_catalog_with_optional_required_version(self) -> None:
        self.assertEqual(
            web_catalog.build_catalog(
                [CatalogRelease("3.11.4", "1.1.0.46699")]
            ),
            {
                "format_version": 1,
                "releases": [
                    {
                        "id": "3.11.4",
                        "required_live_version": "1.1.0.46699",
                    }
                ],
            },
        )

    def test_release_id_api_remains_backward_compatible(self) -> None:
        details = [CatalogRelease("3.11.4", "1.1.0.46699")]
        with mock.patch.object(
            web_catalog,
            "fetch_release_catalog_details",
            return_value=details,
        ):
            self.assertEqual(web_catalog.fetch_release_catalog(), ["3.11.4"])

    def test_publisher_reads_required_version_from_package_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            storage = package / "storage"
            storage.mkdir()
            (storage / "metadata.info").write_text(
                json.dumps({"version": "1.1.0.46699"}),
                encoding="utf-8",
            )
            self.assertEqual(
                web_delivery._package_required_live_version(package),
                "1.1.0.46699",
            )

    def test_publisher_preserves_existing_release_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "releases" / "3.11.4").mkdir(parents=True)
            (repository / "releases" / "3.11.4" / "manifest.json").write_text(
                "{}",
                encoding="utf-8",
            )
            (repository / "catalog.json").write_text(
                json.dumps(
                    web_catalog.build_catalog(
                        [CatalogRelease("3.10.0", "1.1.0.46000")]
                    )
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                web_delivery._catalog_release_ids(repository, "3.11.4"),
                ["3.10.0", "3.11.4"],
            )

            catalog_path = web_delivery._write_catalog(
                repository,
                "3.11.4",
                required_live_version="1.1.0.46699",
            )

            self.assertEqual(
                web_catalog.parse_release_catalog(
                    json.loads(catalog_path.read_text(encoding="utf-8"))
                ),
                [
                    CatalogRelease("3.10.0", "1.1.0.46000"),
                    CatalogRelease("3.11.4", "1.1.0.46699"),
                ],
            )

    def test_publisher_clears_stale_metadata_for_republished_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "catalog.json").write_text(
                json.dumps(
                    web_catalog.build_catalog(
                        [CatalogRelease("3.11.4", "1.1.0.46000")]
                    )
                ),
                encoding="utf-8",
            )

            catalog_path = web_delivery._write_catalog(
                repository,
                "3.11.4",
            )

            self.assertEqual(
                web_catalog.parse_release_catalog(
                    json.loads(catalog_path.read_text(encoding="utf-8"))
                ),
                [CatalogRelease("3.11.4")],
            )

    def test_repository_rebuild_adds_live_versions_to_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            with (
                mock.patch.object(
                    repository_tools,
                    "list_releases",
                    return_value=["3.10.0", "3.11.4"],
                ),
                mock.patch.object(
                    repository_tools,
                    "load_release_metadata",
                    side_effect=[
                        {"version": "1.1.0.46000"},
                        {"version": "1.1.0.46699"},
                    ],
                ),
            ):
                catalog_path, releases = repository_tools.rebuild_catalog(repository)

            self.assertEqual(releases, ["3.10.0", "3.11.4"])
            self.assertEqual(
                json.loads(catalog_path.read_text(encoding="utf-8")),
                web_catalog.build_catalog(
                    [
                        CatalogRelease("3.10.0", "1.1.0.46000"),
                        CatalogRelease("3.11.4", "1.1.0.46699"),
                    ]
                ),
            )


if __name__ == "__main__":
    unittest.main()
