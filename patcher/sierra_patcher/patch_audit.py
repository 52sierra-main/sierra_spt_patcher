from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from .paths import PATCH_out_DIR


def _audit_one(path: Path) -> tuple[bool, str]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        return False, f"unreadable: {path} ({exc})"
    if size <= 0:
        return False, f"empty patch: {path}"
    return True, ""


def audit_patch_files(
    patch_root: str | Path = PATCH_out_DIR,
    *,
    cancel_event=None,
    on_progress: Callable[[str, int, int, str], None] | None = None,
    workers: int | None = None,
    fast_fail: int = 0,
) -> bool:
    """Perform a lightweight audit of generation-verified patch files.

    Every delta is already decoded with its source and byte-compared against the
    target during generation. A plain `zstd -t` cannot validate patch-from
    frames without that source, so this final pass checks package completeness
    instead: readable, non-empty .zst files and no abandoned staging artifacts.
    """

    root = Path(patch_root)
    patches = list(root.rglob("*.zst")) if root.is_dir() else []
    abandoned = []
    if root.is_dir():
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            lower = path.name.lower()
            if lower.endswith((".tmp", ".part", ".new")) or ".assembling-" in lower:
                abandoned.append(str(path))

    total = len(patches)
    if total == 0:
        if on_progress:
            on_progress("audit:patches", 0, 0, "No delta patches generated")
        if abandoned:
            print(f"patch package audit failed: {len(abandoned)} abandoned files")
            for item in abandoned[:10]:
                print(" -", item)
            return False
        print("patch package audit passed (no delta patches generated)")
        return True

    max_workers = max(1, min(int(workers or min(32, max(4, os.cpu_count() or 4))), 64))
    problems: list[str] = list(abandoned)
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_audit_one, path): path for path in patches}
        for future in as_completed(futures):
            if cancel_event and cancel_event.is_set():
                break

            ok, detail = future.result()
            completed += 1
            if not ok:
                problems.append(detail)
                if fast_fail and len(problems) >= fast_fail:
                    if cancel_event:
                        cancel_event.set()
                    for pending in futures:
                        pending.cancel()
                    break

            if on_progress:
                on_progress(
                    "audit:patches",
                    completed,
                    total,
                    f"Audited {completed}/{total}",
                )

    if problems:
        print(f"patch package audit failed: {len(problems)} problem(s)")
        for item in problems[:10]:
            print(" -", item)
        return False

    print(f"patch package audit passed: {total} generation-verified patches")
    return True
