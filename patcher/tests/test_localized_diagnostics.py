from __future__ import annotations

import unittest
from types import SimpleNamespace

from sierra_patcher import i18n
from sierra_patcher.source_integrity import (
    SourceHashMismatch,
    SourceIntegrityReport,
    describe_source_mismatch,
    format_source_integrity_summary,
)

try:
    from sierra_patcher import prereqs
except ModuleNotFoundError as exc:  # pragma: no cover - prereqs is Windows-targeted
    if exc.name != "winreg":
        raise
    prereqs = None


class SourceIntegrityLocalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_language = i18n.current_language()

    def tearDown(self) -> None:
        i18n.set_language(self.original_language)

    @staticmethod
    def _report() -> SourceIntegrityReport:
        return SourceIntegrityReport(
            total=4,
            matched=1,
            mismatches=(
                SourceHashMismatch(
                    path="UnityPlayer.dll",
                    reason="missing",
                    expected_sha256="a" * 64,
                    expected_size=100,
                ),
                SourceHashMismatch(
                    path="EscapeFromTarkov_Data/example.bundle",
                    reason="size",
                    expected_sha256="b" * 64,
                    expected_size=1000,
                    actual_size=900,
                ),
                SourceHashMismatch(
                    path="EscapeFromTarkov_Data/other.bundle",
                    reason="sha256",
                    expected_sha256="c" * 64,
                    actual_sha256="d" * 64,
                    expected_size=500,
                    actual_size=500,
                ),
            ),
        )

    def test_korean_source_integrity_summary_localizes_ui_but_preserves_data(self) -> None:
        i18n.set_language("ko")
        report = self._report()
        text = format_source_integrity_summary(report)

        self.assertIn("검사한 파일: 4", text)
        self.assertIn("일치: 1", text)
        self.assertIn("불일치: 3", text)
        self.assertIn("대상 폴더의 파일은 변경되지 않았습니다.", text)
        self.assertIn("UnityPlayer.dll: 파일 없음", text)
        self.assertIn("예상 1,000바이트, 실제 900바이트", text)
        self.assertIn("c" * 64, text)
        self.assertIn("d" * 64, text)

        # Support logs intentionally stay canonical English regardless of UI language.
        self.assertEqual(
            describe_source_mismatch(report.mismatches[0]),
            "UnityPlayer.dll: missing",
        )

    def test_english_source_integrity_summary_remains_unchanged(self) -> None:
        i18n.set_language("en")
        text = format_source_integrity_summary(self._report())
        self.assertIn("Checked: 4", text)
        self.assertIn("Matched: 1", text)
        self.assertIn("Mismatched: 3", text)
        self.assertIn("No game files were modified.", text)
        self.assertIn("UnityPlayer.dll: missing", text)


@unittest.skipIf(prereqs is None, "Windows prerequisite module is unavailable")
class RuntimeRequirementLocalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_language = i18n.current_language()

    def tearDown(self) -> None:
        i18n.set_language(self.original_language)

    @staticmethod
    def _runtime_requirement():
        metadata = SimpleNamespace(
            runtime_requirements=[
                {
                    "framework": "Microsoft.NETCore.App",
                    "version": "9.0.18",
                    "sources": ["SPT.Server.runtimeconfig.json"],
                }
            ],
            dependencies=None,
            title="SPT 4.0.13",
        )
        return prereqs.requirements_for_metadata(metadata)[0]

    def test_korean_runtimeconfig_requirement_is_fully_localized(self) -> None:
        i18n.set_language("ko")
        text = prereqs.format_missing_requirements([self._runtime_requirement()])

        self.assertIn(".NET 런타임 9.0 x64", text)
        self.assertIn("Microsoft.NETCore.App 9.0.18", text)
        self.assertIn("9.0 런타임 계열", text)
        self.assertIn("SPT.Server.runtimeconfig.json에서 요구하는 구성 요소.", text)
        self.assertIn("https://dotnet.microsoft.com/en-us/download/dotnet/9.0", text)

    def test_english_runtimeconfig_requirement_keeps_canonical_wording(self) -> None:
        i18n.set_language("en")
        text = prereqs.format_missing_requirements([self._runtime_requirement()])

        self.assertIn(".NET Runtime 9.0 x64", text)
        self.assertIn(
            "Requires Microsoft.NETCore.App 9.0.18 or a newer patch within the 9.0 runtime train.",
            text,
        )
        self.assertIn("Declared by SPT.Server.runtimeconfig.json.", text)


if __name__ == "__main__":
    unittest.main()
