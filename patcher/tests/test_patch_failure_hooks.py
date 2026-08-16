from __future__ import annotations

import unittest

from sierra_patcher import patch_failure_hooks


class PatchFailureClassificationTests(unittest.TestCase):
    def test_checksum_mismatch_is_deterministic_and_specific(self) -> None:
        code = patch_failure_hooks._classify_zstd_failure(
            "Decoding error (36): Restored data doesn't match checksum"
        )
        self.assertEqual(code, "ZSTD_CHECKSUM_MISMATCH")

    def test_corruption_is_deterministic_but_not_called_source_mismatch(self) -> None:
        code = patch_failure_hooks._classify_zstd_failure(
            "Decoding error (36): Data corruption detected"
        )
        self.assertEqual(code, "ZSTD_CORRUPTION")

    def test_unknown_or_io_failure_remains_retryable_class(self) -> None:
        code = patch_failure_hooks._classify_zstd_failure(
            "Access denied while opening source file"
        )
        self.assertEqual(code, "ZSTD_IO")

    def test_hook_sets_expected_retry_and_abort_classes(self) -> None:
        patch_failure_hooks.enable_patch_failure_hooks()
        from sierra_patcher import patch_apply

        self.assertIn("ZSTD_IO", patch_apply.RETRYABLE_FAILURE_CODES)
        self.assertNotIn("ZSTD_CHECKSUM_MISMATCH", patch_apply.RETRYABLE_FAILURE_CODES)
        self.assertNotIn("ZSTD_CORRUPTION", patch_apply.RETRYABLE_FAILURE_CODES)
        self.assertIn("ZSTD_CHECKSUM_MISMATCH", patch_apply.FATAL_SOURCE_FAILURE_CODES)
        self.assertIn("ZSTD_CORRUPTION", patch_apply.FATAL_SOURCE_FAILURE_CODES)


if __name__ == "__main__":
    unittest.main()
