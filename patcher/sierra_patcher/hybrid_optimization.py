from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from .paths import ZSTD_EXE
from .proc import run_quiet


_ENABLED = False


def enable_deferred_payload_verification() -> None:
    """Avoid testing full-file candidates that lose hybrid selection.

    The hybrid engine still produces exactly the same delta/full candidates and
    uses the same size-selection policy. Only the timing of the ordinary-Zstd
    integrity test changes: candidate compression no longer immediately runs
    ``zstd -t``. The test runs immediately before a winning/new full payload is
    promoted out of its temporary candidate file.
    """

    global _ENABLED
    if _ENABLED:
        return

    from . import hybrid_payload as hp

    original_replace = hp._replace_file
    thread_state = threading.local()

    def compress_candidate(
        source_file: str | Path,
        output_file: str | Path,
        *,
        zstd_args: list[str],
        cancel_event=None,
    ) -> None:
        hp._raise_if_cancelled(cancel_event)
        thread_state.cancel_event = cancel_event
        output = Path(output_file)
        os.makedirs(hp._python_io_path(output.parent), exist_ok=True)
        hp._remove(output)
        try:
            run_quiet(
                [
                    ZSTD_EXE,
                    *zstd_args,
                    "-T1",
                    "-f",
                    os.fspath(source_file),
                    "-o",
                    os.fspath(output),
                ],
                check=True,
                capture=True,
                cancel_event=cancel_event,
            )
        except subprocess.CalledProcessError as exc:
            hp._remove(output)
            raise RuntimeError(
                f"zstd full-file compression failed: {hp._called_process_detail(exc)}"
            ) from exc

    def verify_candidate(path: str | Path) -> None:
        candidate = Path(path)
        cancel_event = getattr(thread_state, "cancel_event", None)
        hp._raise_if_cancelled(cancel_event)
        try:
            run_quiet(
                [
                    ZSTD_EXE,
                    "-t",
                    os.fspath(candidate),
                    "--long=31",
                    "-T1",
                ],
                check=True,
                capture=True,
                cancel_event=cancel_event,
            )
        except subprocess.CalledProcessError as exc:
            hp._remove(candidate)
            raise RuntimeError(
                f"zstd full-file verification failed: {hp._called_process_detail(exc)}"
            ) from exc

    def replace_with_deferred_verification(source, destination) -> None:
        # These are the two temporary names produced immediately after ordinary
        # Zstd compression. If the hybrid comparison discards ``full.zst``, this
        # hook is never reached, which removes the wasted zstd -t invocation.
        # Staged *.payload.zst files have already passed this verification and
        # are therefore not tested again during final payload promotion.
        name = Path(source).name
        if name in {"full.zst", "payload.zst"}:
            verify_candidate(source)
        original_replace(source, destination)

    hp._compress_full = compress_candidate
    hp._replace_file = replace_with_deferred_verification
    _ENABLED = True
