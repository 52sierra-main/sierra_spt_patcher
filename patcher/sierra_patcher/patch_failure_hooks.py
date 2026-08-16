from __future__ import annotations

from . import gui_resilient, patch_apply


_ENABLED = False

_CHECKSUM_MARKERS = (
    "doesn't match checksum",
    "does not match checksum",
)
_CORRUPTION_MARKERS = (
    "data corruption detected",
    "corruption detected",
)


def _classify_zstd_failure(detail: str) -> str:
    """Classify zstd failures without assuming every corruption is a bad source."""

    text = str(detail or "").lower().replace("’", "'")
    if any(marker in text for marker in _CHECKSUM_MARKERS):
        return "ZSTD_CHECKSUM_MISMATCH"
    if any(marker in text for marker in _CORRUPTION_MARKERS):
        return "ZSTD_CORRUPTION"
    # Sharing violations, read errors and unknown subprocess failures may be
    # transient (for example AV/indexer contention), so retain retry behavior.
    return "ZSTD_IO"


def _format_patch_failure_summary(report, max_items: int = 5) -> str:
    if not report.failures:
        return f"All {report.total} patches applied successfully."

    if report.aborted_early:
        lines = [
            "Installation stopped early after repeated deterministic patch failures.",
            "",
            f"Attempted before stopping: {report.succeeded + report.failed} of {report.total}",
            f"Failed: {report.failed}",
            f"Not attempted: {report.not_attempted}",
            "",
            "Sierra stopped to avoid modifying more files. The source files and patch data "
            "were not compatible enough to continue safely.",
            "",
            "Do not retry this partially patched destination. Use a fresh copy and send "
            "the session log to support if the problem repeats.",
            "",
            "Examples:",
        ]
    else:
        lines = [
            f"Patch stage incomplete: {report.failed}/{report.total} patch(es) could not be applied.",
            "Automatic retries were attempted only for potentially transient failures.",
            "",
        ]

    for failure in report.failures[:max_items]:
        lines.append(f"- {failure.relative_path}")
        lines.append(f"  {failure.code}: {failure.detail}")
    if report.failed > max_items:
        lines.append(f"... and {report.failed - max_items} more. See Logs for details.")
    return "\n".join(lines)


def enable_patch_failure_hooks() -> None:
    """Refine PR4's zstd split while keeping its retry and 25-failure cutoff."""

    global _ENABLED
    if _ENABLED:
        return
    _ENABLED = True

    patch_apply._classify_zstd_failure = _classify_zstd_failure
    patch_apply.RETRYABLE_FAILURE_CODES.discard("ZSTD_FAILURE")
    patch_apply.RETRYABLE_FAILURE_CODES.discard("ZSTD_CHECKSUM_MISMATCH")
    patch_apply.RETRYABLE_FAILURE_CODES.discard("ZSTD_CORRUPTION")
    patch_apply.RETRYABLE_FAILURE_CODES.add("ZSTD_IO")

    # Both checksum mismatch and corruption are deterministic for an unchanged
    # source+delta pair, so retrying them cannot help. They are deliberately kept
    # as separate diagnostic codes because corruption is not proof by itself that
    # the user's source folder is the cause.
    patch_apply.FATAL_SOURCE_FAILURE_CODES.clear()
    patch_apply.FATAL_SOURCE_FAILURE_CODES.update(
        {"ZSTD_CHECKSUM_MISMATCH", "ZSTD_CORRUPTION", "MISSING_SOURCE"}
    )
    patch_apply.format_patch_failure_summary = _format_patch_failure_summary

    # gui_resilient imported these symbols directly, so update its module-level
    # references as well. The underlying apply function still resolves the
    # classification sets from patch_apply at runtime.
    gui_resilient.PatchApplyError = patch_apply.PatchApplyError
