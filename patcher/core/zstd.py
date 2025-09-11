from __future__ import annotations
import subprocess, os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable
from .io import resource_path


# Immediate fix: centralize tool lookup
_ZSTD_EXE = Path(resource_path("bin", "zstd64", "zstd.exe"))




def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)




def make_patch(src_file: Path, dst_file: Path, patch_out: Path, long_distance: bool = True) -> bool:
patch_out.parent.mkdir(parents=True, exist_ok=True)
cmd = [str(_ZSTD_EXE), "--patch-from", str(src_file), str(dst_file), "-o", str(patch_out)]
if long_distance:
cmd.append("--long=28")
res = _run(cmd)
return res.returncode == 0




def apply_patch(base_file: Path, patch_file: Path, out_tmp: Path, long_distance: bool = True) -> tuple[bool, str]:
out_tmp.parent.mkdir(parents=True, exist_ok=True)
cmd = [str(_ZSTD_EXE), "-d", "--patch-from", str(base_file), str(patch_file), "-o", str(out_tmp)]
if long_distance:
cmd.append("--long=28")
res = _run(cmd)
ok = res.returncode == 0 and out_tmp.exists() and out_tmp.stat().st_size >= 0
return ok, res.stderr if not ok else ""




def parallel_apply(jobs: list[tuple[Path, Path, Path]], workers: int | None = None) -> tuple[bool, list[str]]:
"""Apply multiple patches in parallel. Returns (all_ok, errors)."""
errors: list[str] = []
if workers is None or workers <= 0:
try:
import os
workers = max(1, (os.cpu_count() or 4) - 1)
except Exception:
workers = 4
with ThreadPoolExecutor(max_workers=workers) as ex:
futs = {ex.submit(apply_patch, base, patch, out): (base, patch, out) for base, patch, out in jobs}
for fut in as_completed(futs):
ok, err = fut.result()
if not ok:
base, patch, _ = futs[fut]
errors.append(f"Patch failed: {patch} against {base}: {err}")
return len(errors) == 0, errors
