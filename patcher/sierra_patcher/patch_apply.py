from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .paths import PATCH_read_DIR, ZSTD_EXE
from .proc import Cancelled, run_quiet
from .zstd_patch import (
    _called_process_detail,
    _external_path_is_long,
    _python_io_path,
    _replace_file,
    _stage_external_input,
)


RETRYABLE_FAILURE_CODES = {
    "ZSTD_IO",
    "EMPTY_OUTPUT",
    "REPLACE_FAILURE",
    "IO_FAILURE",
    "UNEXPECTED",
}

# Failures that prove the destination is not the source this release was built
# against. They are fully determined by the input bytes, so neither retrying nor
# continuing through the remaining patches can change the outcome.
FATAL_SOURCE_FAILURE_CODES = {
    "ZSTD_SOURCE_MISMATCH",
    "MISSING_SOURCE",
}

# Number of fatal source failures tolerated before the first pass gives up.
# Below this, a release-side defect affecting a handful of patches still reports
# its complete failure list. Above it, the destination is simply the wrong build
# and every additional applied patch only damages the user's folder further.
DEFAULT_ABORT_AFTER_SOURCE_FAILURES = 25

# zstd reports both of these as "Decoding error (36)". Either one means the
# reference file handed to --patch-from is not the file the delta was built
# from: the frame either decoded to the wrong bytes or referenced an offset the
# reference cannot satisfy. Running the identical command again cannot help.
_SOURCE_MISMATCH_MARKERS = (
    "doesn't match checksum",
    "does not match checksum",
    "corruption detected",
)


def _classify_zstd_failure(detail: str) -> str:
    """Split zstd's single exit code into deterministic vs transient causes."""

    text = str(detail or "").lower().replace("’", "'")
    if any(marker in text for marker in _SOURCE_MISMATCH_MARKERS):
        return "ZSTD_SOURCE_MISMATCH"
    # Read errors, sharing violations and unknown causes stay retryable: an
    # antivirus or indexer holding the file usually releases it within a second.
    return "ZSTD_IO"


@dataclass(frozen=True)
class PatchAttemptResult:
    patch_file: Path
    relative_path: str
    ok: bool
    code: str = ""
    detail: str = ""


@dataclass(frozen=True)
class PatchFailure:
    patch_file: Path
    relative_path: str
    code: str
    detail: str
    attempts: int
    history: tuple[str, ...]


@dataclass(frozen=True)
class PatchApplyReport:
    total: int
    succeeded: int
    failures: tuple[PatchFailure, ...]
    recovered_on_retry: int = 0
    not_attempted: int = 0
    aborted_early: bool = False

    @property
    def failed(self) -> int:
        return len(self.failures)


class PatchApplyError(RuntimeError):
    def __init__(self, report: PatchApplyReport):
        self.report = report
        super().__init__(format_patch_failure_summary(report))


def _clean_detail(value: str, limit: int = 2400) -> str:
    text = " ".join(str(value or "").replace("\x00", "").split())
    if not text:
        return "no additional diagnostic output"
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _failure(
    patch_file: Path,
    relative_path: str,
    code: str,
    detail: str,
) -> PatchAttemptResult:
    return PatchAttemptResult(
        patch_file=patch_file,
        relative_path=relative_path,
        ok=False,
        code=code,
        detail=_clean_detail(detail),
    )


def _remove_file(path: str | Path) -> None:
    try:
        os.unlink(_python_io_path(path))
    except FileNotFoundError:
        pass


def _apply_single_detailed(
    patch_file: Path,
    dest_dir: Path,
    patch_root: Path,
    cancel_event=None,
) -> PatchAttemptResult:
    if cancel_event is not None and cancel_event.is_set():
        raise Cancelled()

    relative = patch_file.relative_to(patch_root).with_suffix("")
    relative_text = relative.as_posix()
    old_file = dest_dir / relative

    if not os.path.exists(_python_io_path(old_file)):
        return _failure(
            patch_file,
            relative_text,
            "MISSING_SOURCE",
            "required Live/source file does not exist in the selected destination",
        )

    needs_stage = (
        _external_path_is_long(old_file)
        or _external_path_is_long(patch_file)
        or _external_path_is_long(old_file.with_suffix(old_file.suffix + ".new"))
    )

    if not needs_stage:
        tmp = old_file.with_suffix(old_file.suffix + ".new")
        _remove_file(tmp)
        try:
            try:
                run_quiet(
                    [
                        ZSTD_EXE,
                        "-d",
                        "--patch-from",
                        str(old_file),
                        str(patch_file),
                        "-o",
                        str(tmp),
                        "-T1",
                        "--long=31",
                    ],
                    check=True,
                    capture=True,
                    cancel_event=cancel_event,
                )
            except subprocess.CalledProcessError as exc:
                detail = _called_process_detail(exc)
                return _failure(
                    patch_file,
                    relative_text,
                    _classify_zstd_failure(detail),
                    f"zstd exit {exc.returncode}: {detail}",
                )

            if (
                not os.path.exists(_python_io_path(tmp))
                or os.path.getsize(_python_io_path(tmp)) == 0
            ):
                return _failure(
                    patch_file,
                    relative_text,
                    "EMPTY_OUTPUT",
                    "zstd returned success but produced no usable output file",
                )

            try:
                os.replace(_python_io_path(tmp), _python_io_path(old_file))
            except OSError as exc:
                return _failure(
                    patch_file,
                    relative_text,
                    "REPLACE_FAILURE",
                    f"could not replace destination file: {type(exc).__name__}: {exc}",
                )
            return PatchAttemptResult(patch_file, relative_text, True)
        except Cancelled:
            raise
        except OSError as exc:
            return _failure(
                patch_file,
                relative_text,
                "IO_FAILURE",
                f"filesystem operation failed: {type(exc).__name__}: {exc}",
            )
        except Exception as exc:
            return _failure(
                patch_file,
                relative_text,
                "UNEXPECTED",
                f"{type(exc).__name__}: {exc}",
            )
        finally:
            _remove_file(tmp)

    stage_dir: str | None = None
    try:
        stage_dir = tempfile.mkdtemp(prefix="sierra_apply_", dir=str(dest_dir))
        staged_output = os.path.join(stage_dir, "patched.out")
        source_for_zstd = _stage_external_input(old_file, stage_dir, "source.bin")
        patch_for_zstd = _stage_external_input(patch_file, stage_dir, "patch.zst")

        try:
            run_quiet(
                [
                    ZSTD_EXE,
                    "-d",
                    "--patch-from",
                    source_for_zstd,
                    patch_for_zstd,
                    "-o",
                    staged_output,
                    "-T1",
                    "--long=31",
                ],
                check=True,
                capture=True,
                cancel_event=cancel_event,
            )
        except subprocess.CalledProcessError as exc:
            detail = _called_process_detail(exc)
            return _failure(
                patch_file,
                relative_text,
                _classify_zstd_failure(detail),
                f"zstd exit {exc.returncode}: {detail}",
            )

        if (
            not os.path.exists(_python_io_path(staged_output))
            or os.path.getsize(_python_io_path(staged_output)) == 0
        ):
            return _failure(
                patch_file,
                relative_text,
                "EMPTY_OUTPUT",
                "zstd returned success but produced no usable staged output file",
            )

        try:
            _replace_file(staged_output, old_file)
        except OSError as exc:
            return _failure(
                patch_file,
                relative_text,
                "REPLACE_FAILURE",
                f"could not replace destination file: {type(exc).__name__}: {exc}",
            )
        return PatchAttemptResult(patch_file, relative_text, True)
    except Cancelled:
        raise
    except OSError as exc:
        return _failure(
            patch_file,
            relative_text,
            "IO_FAILURE",
            f"staging/filesystem operation failed: {type(exc).__name__}: {exc}",
        )
    except Exception as exc:
        return _failure(
            patch_file,
            relative_text,
            "UNEXPECTED",
            f"{type(exc).__name__}: {exc}",
        )
    finally:
        if stage_dir:
            shutil.rmtree(stage_dir, ignore_errors=True)


def _emit_log(callback: Callable[[str], None] | None, message: str) -> None:
    if callback is not None:
        try:
            callback(message)
            return
        except Exception:
            pass
    print(message)


def _wait_for_retry(cancel_event, seconds: float) -> None:
    if seconds <= 0:
        return
    if cancel_event is not None:
        if cancel_event.wait(seconds):
            raise Cancelled()
    else:
        time.sleep(seconds)


def _run_attempt_batch(
    patch_files: list[Path],
    *,
    destination: Path,
    patch_root: Path,
    workers: int,
    cancel_event=None,
    on_progress=None,
    phase: str,
    progress_message: str,
    abort_after: int = 0,
) -> tuple[list[PatchAttemptResult], bool]:
    """Run one apply pass. Returns (results, aborted_early).

    When ``abort_after`` is positive, the pass stops once that many fatal source
    failures have been seen. Workers already running are allowed to finish and
    their results are still collected so the report stays accurate.
    """

    if not patch_files:
        return [], False

    results: list[PatchAttemptResult] = []
    completed = 0
    max_workers = max(1, min(int(workers), len(patch_files)))

    # The abort has to be visible to the workers, not just to this loop. Every
    # patch is submitted up front, so by the time the consumer has counted N
    # fatal results the pool may already have burned through thousands more.
    # Queued workers check this event first and return without touching a file,
    # which bounds the overrun to roughly one task per worker.
    abort_event = threading.Event()
    fatal_lock = threading.Lock()
    fatal_seen = 0

    def guarded_apply(patch_file: Path) -> PatchAttemptResult | None:
        nonlocal fatal_seen
        if abort_event.is_set():
            return None

        # Resolved from module globals on purpose: gui_resilient replaces
        # _apply_single_detailed to skip volatile runtime files.
        result = _apply_single_detailed(patch_file, destination, patch_root, cancel_event)

        if not result.ok and result.code in FATAL_SOURCE_FAILURE_CODES:
            with fatal_lock:
                fatal_seen += 1
                if abort_after and fatal_seen >= abort_after:
                    abort_event.set()
        return result

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(guarded_apply, patch_file): patch_file
            for patch_file in patch_files
        }

        for future in as_completed(futures):
            if cancel_event is not None and cancel_event.is_set():
                for pending in futures:
                    pending.cancel()
                raise Cancelled()

            try:
                result = future.result()
            except Cancelled:
                for pending in futures:
                    pending.cancel()
                raise

            # Skipped by the abort guard: this patch was never attempted, so it
            # is neither a success nor a failure.
            if result is None:
                continue

            results.append(result)
            completed += 1
            if on_progress is not None:
                on_progress(
                    phase,
                    completed,
                    len(patch_files),
                    f"{progress_message} {completed}/{len(patch_files)}",
                )

    return results, abort_event.is_set()


def apply_patches_resilient(
    dest_dir: str | Path,
    workers: int = 8,
    on_progress=None,
    cancel_event=None,
    patch_root: str | Path = PATCH_read_DIR,
    *,
    retry_attempts: int = 2,
    retry_delay_seconds: float = 0.75,
    on_log: Callable[[str], None] | None = None,
    abort_after: int = DEFAULT_ABORT_AFTER_SOURCE_FAILURES,
) -> PatchApplyReport:
    """Apply patches with isolated retries and detailed failure reporting.

    The first pass applies every patch once. Only files that fail with a
    potentially transient reason are retried. Successful files are never
    re-applied, which is important because each delta expects the original
    Live file as its reference.

    The pass stops early once ``abort_after`` fatal source failures prove the
    destination is the wrong build, so a doomed run damages as few files as
    possible instead of rewriting thousands before giving up.
    """

    destination = Path(dest_dir)
    patch_root_path = Path(patch_root)
    patch_files = sorted(patch_root_path.rglob("*.zst"))
    total = len(patch_files)
    retry_attempts = max(0, int(retry_attempts))
    abort_after = max(0, int(abort_after))

    if not patch_files:
        _emit_log(on_log, "[patch] no .zst patches found")
        return PatchApplyReport(total=0, succeeded=0, failures=())

    _emit_log(
        on_log,
        f"[patch] applying {total} patch(es) with {max(1, int(workers))} worker(s)"
        + (f"; aborting after {abort_after} source mismatches" if abort_after else ""),
    )

    history: dict[Path, list[PatchAttemptResult]] = {path: [] for path in patch_files}
    current: dict[Path, PatchAttemptResult] = {}

    first_results, aborted_early = _run_attempt_batch(
        patch_files,
        destination=destination,
        patch_root=patch_root_path,
        workers=workers,
        cancel_event=cancel_event,
        on_progress=on_progress,
        phase="install:patch",
        progress_message="applied",
        abort_after=abort_after,
    )

    for result in first_results:
        history[result.patch_file].append(result)
        current[result.patch_file] = result
        if not result.ok:
            _emit_log(
                on_log,
                f"[patch] attempt 1 failed: {result.relative_path} "
                f"[{result.code}] {result.detail}",
            )

    attempted = len(current)
    initial_failures = [result for result in current.values() if not result.ok]
    if initial_failures:
        _emit_log(
            on_log,
            f"[patch] initial pass complete: {attempted - len(initial_failures)} succeeded, "
            f"{len(initial_failures)} failed"
            + (f", {total - attempted} not attempted" if total > attempted else ""),
        )

    if aborted_early:
        _emit_log(
            on_log,
            f"[patch] ABORTED EARLY: {abort_after}+ patches reported that the destination "
            "file is not the source this release was built from.",
        )
        _emit_log(
            on_log,
            "[patch] The remaining patches were not attempted because they would fail "
            "identically. This destination is a different Tarkov build than the release "
            "expects; make a fresh copy of Live Tarkov instead of reusing this folder.",
        )

    recovered = 0
    for retry_index in range(1, retry_attempts + 1):
        if aborted_early:
            break
        retryable = [
            result.patch_file
            for result in current.values()
            if not result.ok and result.code in RETRYABLE_FAILURE_CODES
        ]
        if not retryable:
            break

        delay = retry_delay_seconds * retry_index
        _emit_log(
            on_log,
            f"[patch] retry pass {retry_index}/{retry_attempts}: "
            f"{len(retryable)} patch(es) after {delay:.2f}s",
        )
        _wait_for_retry(cancel_event, delay)

        retry_results, _ = _run_attempt_batch(
            retryable,
            destination=destination,
            patch_root=patch_root_path,
            workers=workers,
            cancel_event=cancel_event,
            on_progress=on_progress,
            phase="install:retry",
            progress_message=f"retry {retry_index}/{retry_attempts}",
        )

        for result in retry_results:
            previous = current[result.patch_file]
            history[result.patch_file].append(result)
            current[result.patch_file] = result
            if result.ok:
                recovered += 1
                _emit_log(
                    on_log,
                    f"[patch] recovered on retry {retry_index}: {result.relative_path} "
                    f"(previously {previous.code})",
                )
            else:
                _emit_log(
                    on_log,
                    f"[patch] retry {retry_index} failed: {result.relative_path} "
                    f"[{result.code}] {result.detail}",
                )

    failures: list[PatchFailure] = []
    for patch_file in patch_files:
        result = current.get(patch_file)
        if result is None or result.ok:
            continue
        attempts = history[patch_file]
        failures.append(
            PatchFailure(
                patch_file=patch_file,
                relative_path=result.relative_path,
                code=result.code,
                detail=result.detail,
                attempts=len(attempts),
                history=tuple(
                    f"attempt {index}: [{attempt.code}] {attempt.detail}"
                    for index, attempt in enumerate(attempts, start=1)
                    if not attempt.ok
                ),
            )
        )

    # Derive from real results rather than total-minus-failures: after an early
    # abort the unattempted patches are neither successes nor failures.
    succeeded = sum(1 for result in current.values() if result.ok)
    not_attempted = max(0, total - len(current))

    if failures:
        _emit_log(
            on_log,
            f"[patch] FINAL RESULT: {succeeded}/{total} succeeded, "
            f"{len(failures)} failed"
            + (f", {not_attempted} not attempted" if not_attempted else "")
            + " after automatic retry handling",
        )
        for failure in failures:
            _emit_log(on_log, f"[patch] FINAL FAILURE: {failure.relative_path}")
            _emit_log(
                on_log,
                f"[patch]   reason={failure.code} attempts={failure.attempts} detail={failure.detail}",
            )
            for item in failure.history:
                _emit_log(on_log, f"[patch]   {item}")
    else:
        if recovered:
            _emit_log(
                on_log,
                f"[patch] all {total} patch(es) succeeded; {recovered} recovered by retry",
            )
        else:
            _emit_log(on_log, f"[patch] all {total} patch(es) succeeded on the first pass")

    return PatchApplyReport(
        total=total,
        succeeded=succeeded,
        failures=tuple(failures),
        recovered_on_retry=recovered,
        not_attempted=not_attempted,
        aborted_early=aborted_early,
    )


def format_patch_failure_summary(report: PatchApplyReport, max_items: int = 5) -> str:
    if not report.failures:
        return f"All {report.total} patches applied successfully."

    if report.aborted_early:
        lines = [
            "Installation stopped early: the selected folder is not the Tarkov build "
            "this release was built from.",
            "",
            f"Checked before stopping: {report.succeeded + report.failed} of {report.total}",
            f"Mismatched: {report.failed}",
            "",
            "Sierra stopped instead of continuing so that as few files as possible were "
            "changed. This destination can no longer be used.",
            "",
            "Delete this folder, make a fresh copy of your Live Tarkov installation, and "
            "run the installer again on the new copy.",
            "",
            "Examples:",
        ]
    else:
        lines = [
            f"Patch stage incomplete: {report.failed}/{report.total} patch(es) could not be applied.",
            "Automatic retries were attempted for transient failures.",
            "",
        ]

    for failure in report.failures[:max_items]:
        lines.append(f"- {failure.relative_path}")
        lines.append(f"  {failure.code}: {failure.detail}")
    if len(report.failures) > max_items:
        lines.append(f"... and {len(report.failures) - max_items} more. See Logs for details.")
    return "\n".join(lines)
