from __future__ import annotations

import json
import os
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

    def test_first_run_uses_korean_system_locale(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(i18n, "_saved_language", return_value=None):
                with mock.patch.object(
                    i18n.locale,
                    "getlocale",
                    return_value=("ko_KR", "UTF-8"),
                ):
                    self.assertEqual(i18n.detect_language(), "ko")

    def test_first_run_defaults_to_english_for_other_system_locales(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(i18n, "_saved_language", return_value=None):
                with mock.patch.object(
                    i18n.locale,
                    "getlocale",
                    return_value=("ja_JP", "UTF-8"),
                ):
                    self.assertEqual(i18n.detect_language(), "en")

    def test_saved_language_takes_priority_over_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(i18n, "_saved_language", return_value="ko"):
                self.assertEqual(i18n.detect_language(), "ko")

    def test_environment_language_takes_priority_over_saved_value(self) -> None:
        with mock.patch.dict(
            os.environ,
            {i18n.LANGUAGE_ENV: "en"},
            clear=True,
        ):
            with mock.patch.object(i18n, "_saved_language", return_value="ko"):
                self.assertEqual(i18n.detect_language(), "en")

    def test_korean_translation_formats_values(self) -> None:
        i18n.set_language("ko")
        self.assertEqual(i18n.tr("Install"), "설치")
        self.assertEqual(
            i18n.tr("Live Tarkov Folder"),
            "라이브 타르코프 폴더",
        )
        self.assertEqual(
            i18n.tr("Web release: {release}", release="4.0.1"),
            "웹 릴리스: 4.0.1",
        )

    def test_version_preflight_messages_are_localized(self) -> None:
        i18n.set_language("ko")
        self.assertEqual(i18n.tr("UPDATE REQUIRED  ⚠"), "업데이트 필요  ⚠")
        self.assertEqual(i18n.tr("CHECKING..."), "확인 중...")
        self.assertEqual(
            i18n.tr(
                "Official Live Tarkov must be updated. Current: {current} · Required: {required}",
                current="1.1.0.46657",
                required="1.1.0.46699",
            ),
            "본섭 타르코프를 업데이트해야 해요. 현재: 1.1.0.46657 · 필요: 1.1.0.46699",
        )
        self.assertEqual(
            i18n.tr("No patch data was downloaded."),
            "패치 데이터는 다운로드하지 않았어요.",
        )

    def test_localized_choices_round_trip_to_internal_value(self) -> None:
        choices = ("Web release", "Archived snapshot")
        i18n.set_language("ko")
        displayed = i18n.localized_choices(choices)
        self.assertEqual(displayed, ("웹 릴리스", "보관 스냅샷"))
        self.assertEqual(i18n.canonical_choice(displayed[1], choices), "Archived snapshot")

    def test_canonical_choice_survives_language_change(self) -> None:
        choices = ("Web release", "Archived snapshot")
        i18n.set_language("ko")
        displayed = i18n.tr("Web release")
        i18n.set_language("en")
        self.assertEqual(i18n.canonical_choice(displayed, choices), "Web release")

    def test_alternate_language_toggles_english_and_korean(self) -> None:
        self.assertEqual(i18n.alternate_language("en"), "ko")
        self.assertEqual(i18n.alternate_language("ko"), "en")

    def test_rendered_text_is_retranslated_without_touching_unknown_values(self) -> None:
        i18n.set_language("ko")
        self.assertEqual(i18n.retranslate("Install"), "설치")
        self.assertEqual(
            i18n.retranslate("16 cores / 32 threads"),
            "16코어 / 32스레드",
        )
        self.assertEqual(i18n.retranslate(r"D:\\Games\\Tarkov"), r"D:\\Games\\Tarkov")

        i18n.set_language("en")
        self.assertEqual(i18n.retranslate("설치"), "Install")
        self.assertEqual(
            i18n.retranslate("16코어 / 32스레드"),
            "16 cores / 32 threads",
        )

    def test_progress_details_are_translated_without_changing_paths(self) -> None:
        i18n.set_language("ko")
        self.assertEqual(i18n.tr_progress("Install"), "Install")
        self.assertEqual(
            i18n.tr_progress("Validating package..."),
            "패키지를 확인하는 중...",
        )
        self.assertEqual(i18n.tr_progress("3/9 objects cached"), "객체 3/9 캐시 사용")
        self.assertEqual(
            i18n.tr_progress("ready: patchfiles/Aki_Data/file.bundle"),
            "준비 완료: patchfiles/Aki_Data/file.bundle",
        )

        i18n.set_language("en")
        self.assertEqual(
            i18n.tr_progress("패키지를 확인하는 중..."),
            "Validating package...",
        )
        self.assertEqual(i18n.tr_progress("객체 3/9 캐시 사용"), "3/9 objects cached")
        self.assertEqual(
            i18n.retranslate("준비 완료: patchfiles/Aki_Data/file.bundle"),
            "ready: patchfiles/Aki_Data/file.bundle",
        )

    def test_all_dynamic_progress_formats_round_trip(self) -> None:
        messages = (
            "verified ab12cd34",
            "discarded ab12cd34",
            "verifying 3/9 objects",
            "verified 3/9 objects (cached)",
            "processed 3/9 (delta)",
            "compressed 3/9",
            "applied patchfiles/Aki_Data/file.bundle",
            "patched 3/9",
            "Audited 3/9",
            "Validating patches 3/9",
            "retry 1/3 2/9",
            "hashed 3/9 delta sources",
            "verified 3/9 source files",
        )
        for message in messages:
            with self.subTest(message=message):
                i18n.set_language("ko")
                translated = i18n.tr_progress(message)
                self.assertNotEqual(translated, message)
                i18n.set_language("en")
                self.assertEqual(i18n.retranslate(translated), message)

    def test_exact_retranslation_does_not_transform_formatted_domain_data(self) -> None:
        i18n.set_language("ko")
        self.assertEqual(i18n.retranslate_exact("Install"), "설치")
        self.assertEqual(i18n.retranslate_exact("Version: 4.0.1"), "Version: 4.0.1")

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
