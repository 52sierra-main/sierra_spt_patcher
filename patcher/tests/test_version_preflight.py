from __future__ import annotations

import unittest

from sierra_patcher.version_preflight import (
    VersionPreflightStatus,
    compare_numeric_versions,
    evaluate_version_preflight,
)


class VersionPreflightTests(unittest.TestCase):
    def test_compares_dotted_numeric_versions(self) -> None:
        self.assertEqual(compare_numeric_versions("1.1.0.46657", "1.1.0.46699"), -1)
        self.assertEqual(compare_numeric_versions("1.1.0.46710", "1.1.0.46699"), 1)
        self.assertEqual(compare_numeric_versions("1.1", "1.1.0.0"), 0)
        self.assertIsNone(compare_numeric_versions("latest", "1.1.0.46699"))

    def test_matching_versions_are_ready(self) -> None:
        result = evaluate_version_preflight(
            "1.1.0.46699",
            "1.1.0.46699",
            "1.1.0.46699",
        )
        self.assertEqual(result.status, VersionPreflightStatus.READY)
        self.assertFalse(result.blocks_download)

    def test_older_live_client_requires_update(self) -> None:
        result = evaluate_version_preflight(
            "1.1.0.46699",
            "1.1.0.46657",
            "1.1.0.46657",
        )
        self.assertEqual(result.status, VersionPreflightStatus.UPDATE_REQUIRED)
        self.assertTrue(result.blocks_download)

    def test_newer_live_client_requires_patch_update(self) -> None:
        result = evaluate_version_preflight(
            "1.1.0.46699",
            "1.1.0.46710",
            "1.1.0.46710",
        )
        self.assertEqual(result.status, VersionPreflightStatus.PATCH_UPDATE_REQUIRED)
        self.assertTrue(result.blocks_download)

    def test_wrong_destination_is_a_source_mismatch(self) -> None:
        result = evaluate_version_preflight(
            "1.1.0.46699",
            "1.1.0.46699",
            "0.16.9.40743",
        )
        self.assertEqual(result.status, VersionPreflightStatus.SOURCE_MISMATCH)
        self.assertTrue(result.blocks_download)

    def test_missing_executable_version_blocks_download(self) -> None:
        result = evaluate_version_preflight("1.1.0.46699", None, None)
        self.assertEqual(result.status, VersionPreflightStatus.VERSION_UNKNOWN)
        self.assertTrue(result.blocks_download)

    def test_legacy_catalog_remains_installable_but_unverified(self) -> None:
        result = evaluate_version_preflight(None, "1.1.0.46699", "1.1.0.46699")
        self.assertEqual(result.status, VersionPreflightStatus.CATALOG_UNVERIFIED)
        self.assertFalse(result.blocks_download)


if __name__ == "__main__":
    unittest.main()
