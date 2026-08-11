from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sierra_patcher import i18n


class I18nTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_language = i18n.current_language()

    def tearDown(self) -> None:
        i18n.set_language(self.original_language)

    def test_language_codes_are_normalized(self) -> None:
        self.assertEqual(i18n.normalize_language("ko-KR"), "ko")
        self.assertEqual(i18n.normalize_language("Korean_Korea.949"), "ko")
        self.assertEqual(i18n.normalize_language("EN_us"), "en")
        self.assertIsNone(i18n.normalize_language("ja"))

    def test_english_falls_back_to_source_message(self) -> None:
        i18n.set_language("en")
        self.assertEqual(i18n.tr("Install"), "Install")
        self.assertEqual(i18n.tr("Unknown message"), "Unknown message")

    def test_korean_translation_formats_values(self) -> None:
        i18n.set_language("ko")
        self.assertEqual(i18n.tr("Install"), "설치")
        self.assertEqual(
            i18n.tr("Web release: {release}", release="4.0.1"),
            "웹 릴리스: 4.0.1",
        )

    def test_localized_choices_round_trip_to_internal_value(self) -> None:
        choices = ("Web release", "Archived snapshot")
        i18n.set_language("ko")
        displayed = i18n.localized_choices(choices)
        self.assertEqual(displayed, ("웹 릴리스", "보관 스냅샷"))
        self.assertEqual(i18n.canonical_choice(displayed[1], choices), "Archived snapshot")

    def test_progress_details_are_translated_without_changing_paths(self) -> None:
        i18n.set_language("ko")
        self.assertEqual(i18n.tr_progress("3/9 objects cached"), "객체 3/9 캐시 사용")
        self.assertEqual(
            i18n.tr_progress("ready: patchfiles/Aki_Data/file.bundle"),
            "준비 완료: patchfiles/Aki_Data/file.bundle",
        )

    def test_persisted_language_uses_config_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            with mock.patch.object(i18n, "settings_path", return_value=path):
                i18n.set_language("ko", persist=True)
                self.assertEqual(
                    json.loads(path.read_text(encoding="utf-8"))["language"],
                    "ko",
                )

    def test_failed_persistence_does_not_change_current_language(self) -> None:
        i18n.set_language("en")
        with mock.patch.object(i18n, "_save_language", side_effect=OSError("read-only")):
            with self.assertRaises(OSError):
                i18n.set_language("ko", persist=True)
        self.assertEqual(i18n.current_language(), "en")


if __name__ == "__main__":
    unittest.main()
