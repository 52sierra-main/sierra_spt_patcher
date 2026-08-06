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
    "ZSTD_FAILURE",
    "EMPTY_OUTPUT",
    "REPLACE_FAILURE",
    "IO_FAILURE",
    "UNEXPECTED",
}


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
                return _failure(
                    patch_file,
                    relative_text,
                    "ZSTD_FAILURE",
                    f"zstd exit {exc.returncode}: {_called_process_detail(exc)}",
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
            return _failure(
                patch_file,
                relative_text,
                "ZSTD_FAILURE",
                f"zstd exit {exc.returncode}: {_called_process_detail(exc)}",
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
) -> list[PatchAttemptResult]:
    if not patch_files:
        return []

    results: list[PatchAttemptResult] = []
    completed = 0
    max_workers = max(1, min(int(workers), len(patch_files)))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _apply_single_detailed,
                patch_file,
                destination,
                patch_root,
                cancel_event,
            ): patch_file
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

            results.append(result)
            completed += 1
            if on_progress is not None:
                on_progress(
                    phase,
                    completed,
                    len(patch_files),
                    f"{progress_message} {completed}/{len(patch_files)}",
                )

    return results


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
) -> PatchApplyReport:
    """Apply patches with isolated retries and detailed failure reporting.

    The first pass applies every patch once. Only files that fail with a
    potentially transient reason are retried. Successful files are never
    re-applied, which is important because each delta expects the original
    Live file as its reference.
    """

    destination = Path(dest_dir)
    patch_root_path = Path(patch_root)
    patch_files = sorted(patch_root_path.rglob("*.zst"))
    total = len(patch_files)
    retry_attempts = max(0, int(retry_attempts))

    if not patch_files:
        _emit_log(on_log, "[patch] no .zst patches found")
        return PatchApplyReport(total=0, succeeded=0, failures=())

    _emit_log(
        on_log,
        f"[patch] applying {total} patch(es) with {max(1, int(workers))} worker(s)",
    )

    history: dict[Path, list[PatchAttemptResult]] = {path: [] for path in patch_files}
    current: dict[Path, PatchAttemptResult] = {}

    first_results = _run_attempt_batch(
        patch_files,
        destination=destination,
        patch_root=patch_root_path,
        workers=workers,
        cancel_event=cancel_event,
        on_progress=on_progress,
        phase="install:patch",
        progress_message="applied",
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

    initial_failures = [result for result in current.values() if not result.ok]
    if initial_failures:
        _emit_log(
            on_log,
            f"[patch] initial pass complete: {total - len(initial_failures)} succeeded, "
            f"{len(initial_failures)} failed",
        )

    recovered = 0
    for retry_index in range(1, retry_attempts + 1):
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

        retry_results = _run_attempt_batch(
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

    succeeded = total - len(failures)
    if failures:
        _emit_log(
            on_log,
            f"[patch] FINAL RESULT: {succeeded}/{total} succeeded, "
            f"{len(failures)} failed after automatic retry handling",
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
    )


def format_patch_failure_summary(report: PatchApplyReport, max_items: int = 5) -> str:
    if not report.failures:
        return f"All {report.total} patches applied successfully."

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
