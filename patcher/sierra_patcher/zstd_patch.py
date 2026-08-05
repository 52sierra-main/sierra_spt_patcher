import filecmp
import io
import os
import shutil
import subprocess
import sys
import threading
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from .hygiene import copy_package_file, format_size, is_package_excluded
from .paths import PATCH_out_DIR, PATCH_read_DIR, ZSTD_EXE
from .proc import run_quiet

# Optional progress callback signature:
# on_progress(phase: str, current: int, total: int, message: str)

# Keep external tools comfortably below the classic Win32 MAX_PATH boundary.
# The actual final package path may be longer; Python moves/copies use the
# extended-path prefix on Windows when needed.
_EXTERNAL_PATH_SOFT_LIMIT = 240


# ----- GENERATOR -----
try:
    from tqdm import tqdm as _tqdm  # noqa: F401

    _HAVE_TQDM = True
except Exception:
    tqdm = None  # type: ignore
    _HAVE_TQDM = False


def _log(msg: str) -> None:
    """Safe log function.

    - Prefer tqdm.write if available and working.
    - Fallback to plain print, even if sys.stderr is None.
    """

    if _HAVE_TQDM:
        try:
            tqdm.write(msg)  # type: ignore[attr-defined]
            return
        except Exception:
            pass

    if getattr(sys, "stderr", None) is not None:
        print(msg, file=sys.stderr)
    else:
        print(msg)


def _normalize_zstd_args(zstd_args: list[str] | None) -> list[str]:
    # Default behavior keeps backward-compatible aggressiveness.
    return list(zstd_args) if zstd_args else ["--long=31"]


def _decode_zstd_args(zstd_args: list[str]) -> list[str]:
    args = [arg for arg in zstd_args if arg == "--long" or arg.startswith("--long=")]
    if not any(arg == "--long" or arg.startswith("--long=") for arg in args):
        args.append("--long=31")
    return args


def _external_path_is_long(path: str | Path) -> bool:
    """True when a path is risky to pass directly to a Windows CLI tool."""
    if os.name != "nt":
        return False
    try:
        return len(os.path.abspath(os.fspath(path))) >= _EXTERNAL_PATH_SOFT_LIMIT
    except Exception:
        return len(os.fspath(path)) >= _EXTERNAL_PATH_SOFT_LIMIT


def _python_io_path(path: str | Path) -> str:
    """Return a path suitable for Python file I/O, including long Windows paths."""
    value = os.path.abspath(os.fspath(path))
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _copy_file(src: str | Path, dst: str | Path) -> None:
    dst_value = os.path.abspath(os.fspath(dst))
    parent = os.path.dirname(dst_value)
    if parent:
        os.makedirs(_python_io_path(parent), exist_ok=True)
    shutil.copy2(_python_io_path(src), _python_io_path(dst_value))


def _replace_file(src: str | Path, dst: str | Path) -> None:
    dst_value = os.path.abspath(os.fspath(dst))
    parent = os.path.dirname(dst_value)
    if parent:
        os.makedirs(_python_io_path(parent), exist_ok=True)
    os.replace(_python_io_path(src), _python_io_path(dst_value))


def _stage_external_input(path: str | Path, stage_dir: str | Path, name: str) -> str:
    """Copy only long-path inputs to a short staging path for external tools."""
    value = os.path.abspath(os.fspath(path))
    if not _external_path_is_long(value):
        return value

    staged = os.path.join(os.fspath(stage_dir), name)
    _copy_file(value, staged)
    return staged


def _called_process_detail(exc: subprocess.CalledProcessError) -> str:
    stderr = exc.stderr.strip() if isinstance(exc.stderr, str) and exc.stderr.strip() else ""
    stdout = exc.stdout.strip() if isinstance(exc.stdout, str) and exc.stdout.strip() else ""
    return stderr or stdout or f"exit code {exc.returncode}"


def process_file(
    source_root: str,
    dest_root: str,
    dest_file: str,
    out_root: str,
    missing_root: str,
    zstd_args: list[str] | None = None,
    cancel_event=None,
) -> str:
    if cancel_event and cancel_event.is_set():
        return "cancelled"

    if is_package_excluded(dest_file, dest_root):
        return "excluded"

    rel = os.path.relpath(dest_file, dest_root)
    src = os.path.join(source_root, rel)
    patch_file = os.path.join(out_root, rel + ".zst")

    # Python creates the final package hierarchy. zstd itself writes to a short,
    # flat staging path so long mirrored Tarkov paths do not exceed MAX_PATH.
    os.makedirs(_python_io_path(os.path.dirname(patch_file)), exist_ok=True)

    if not os.path.exists(_python_io_path(src)):
        # not in source → collect as "additional"
        copy_package_file(dest_file, missing_root, rel)
        return "additional"

    if filecmp.cmp(_python_io_path(src), _python_io_path(dest_file), shallow=False):
        return "identical"

    args = _normalize_zstd_args(zstd_args)
    stage_parent = str(Path(out_root).parent)
    os.makedirs(_python_io_path(stage_parent), exist_ok=True)
    stage_dir = tempfile.mkdtemp(prefix="sierra_zstd_", dir=stage_parent)

    patch_tmp = os.path.join(stage_dir, "patch.zst")
    verify_tmp = os.path.join(stage_dir, "verify.out")

    try:
        src_for_zstd = _stage_external_input(src, stage_dir, "source.bin")
        dest_for_zstd = _stage_external_input(dest_file, stage_dir, "target.bin")

        try:
            run_quiet(
                [
                    ZSTD_EXE,
                    "--patch-from",
                    src_for_zstd,
                    dest_for_zstd,
                    "-o",
                    patch_tmp,
                    *args,
                    "-T1",
                ],
                check=True,
                capture=True,
                cancel_event=cancel_event,
            )
        except subprocess.CalledProcessError as e:
            detail = _called_process_detail(e)
            raise RuntimeError(
                f"zstd patch generation failed for {rel}: {detail}"
            ) from e

        # Quick verification while all zstd-visible paths are still short.
        try:
            run_quiet(
                [
                    ZSTD_EXE,
                    "-d",
                    "--patch-from",
                    src_for_zstd,
                    patch_tmp,
                    "-o",
                    verify_tmp,
                    *_decode_zstd_args(args),
                    "-T1",
                ],
                check=True,
                capture=True,
                cancel_event=cancel_event,
            )
        except subprocess.CalledProcessError as e:
            detail = _called_process_detail(e)
            raise RuntimeError(
                f"zstd verification decode failed for {rel}: {detail}"
            ) from e

        if not filecmp.cmp(
            _python_io_path(dest_file),
            _python_io_path(verify_tmp),
            shallow=False,
        ):
            raise RuntimeError(f"verification failed for {rel}")

        patch_size = os.path.getsize(_python_io_path(patch_tmp))
        target_size = os.path.getsize(_python_io_path(dest_file))
        if patch_size > target_size:
            copy_package_file(dest_file, missing_root, rel)
            _log(
                f"kept full file for {rel} "
                f"(delta {format_size(patch_size)} > target {format_size(target_size)})"
            )
            return "full"

        # Promote only a completely generated + verified patch into the package.
        _replace_file(patch_tmp, patch_file)
        return "delta"
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)


def generate_patches(
    source_root: str,
    dest_root: str,
    out_root: str,
    missing_root: str,
    workers: int = 8,
    on_progress=None,
    cancel_event=None,
    use_tqdm: bool = True,
    zstd_args: list[str] | None = None,
) -> int:
    """Generate patches; returns number of processed files (including skipped/added)."""

    files: list[str] = []
    excluded = 0
    for r, _, fs in os.walk(dest_root):
        for f in fs:
            path = os.path.join(r, f)
            if is_package_excluded(path, dest_root):
                excluded += 1
                continue
            files.append(path)

    total = len(files)
    done = 0
    lock = threading.Lock()
    stats = {
        "delta": 0,
        "full": 0,
        "additional": 0,
        "identical": 0,
        "excluded": excluded,
        "cancelled": 0,
    }

    with tqdm(
        total=total,
        desc="Generating patches",
        unit="file",
        file=_tqdm_file(),
        disable=_tqdm_disable() if use_tqdm else True,
    ) as bar:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {
                ex.submit(
                    process_file,
                    source_root,
                    dest_root,
                    f,
                    out_root,
                    missing_root,
                    zstd_args,
                    cancel_event,
                ): f
                for f in files
            }

            for fut in as_completed(futs):
                if cancel_event and cancel_event.is_set():
                    break

                try:
                    result = fut.result()
                except Exception as e:
                    failed_file = futs[fut]
                    try:
                        rel_failed = os.path.relpath(failed_file, dest_root)
                    except Exception:
                        rel_failed = failed_file
                    _log(f"generation failed on: {rel_failed}")
                    # Best effort: prevent queued work from continuing after a
                    # deterministic file failure.
                    for pending in futs:
                        if pending is not fut:
                            pending.cancel()
                    raise RuntimeError(
                        f"patch generation failed for {rel_failed}: {e}"
                    ) from e

                with lock:
                    done += 1
                    if result in stats:
                        stats[result] += 1

                if on_progress:
                    on_progress("generate:patch", done, total, f"patched {done}/{total}")

                bar.update(1)

    _log(
        "generation summary: "
        f"delta={stats['delta']}, full={stats['full']}, additional={stats['additional']}, "
        f"identical={stats['identical']}, hygiene_skipped={stats['excluded']}"
    )
    return total


# ----- INSTALLER -----

def _apply_single(
    patch_file: Path,
    dest_dir: Path,
    patch_root: Path,
    cancel_event=None,
) -> bool:
    rel = patch_file.relative_to(patch_root).with_suffix("")
    old_file = dest_dir / rel

    if not old_file.exists():
        _log(f"missing target: {rel}")
        return False

    # Normal short paths keep the old fast path. Long paths are staged at the
    # destination root so zstd only sees short file names and the final replace
    # remains on the same volume.
    needs_stage = (
        _external_path_is_long(old_file)
        or _external_path_is_long(patch_file)
        or _external_path_is_long(old_file.with_suffix(old_file.suffix + ".new"))
    )

    if not needs_stage:
        tmp = old_file.with_suffix(old_file.suffix + ".new")
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

            if not tmp.exists() or tmp.stat().st_size == 0:
                if tmp.exists():
                    tmp.unlink()
                return False

            os.replace(tmp, old_file)
            return True

        except subprocess.CalledProcessError as e:
            if tmp.exists():
                tmp.unlink()
            _log(f"error patching {rel}: {_called_process_detail(e)}")
            return False

    stage_dir = tempfile.mkdtemp(prefix="sierra_apply_", dir=str(dest_dir))
    staged_output = os.path.join(stage_dir, "patched.out")
    try:
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
        except subprocess.CalledProcessError as e:
            _log(f"error patching {rel}: {_called_process_detail(e)}")
            return False

        if (
            not os.path.exists(_python_io_path(staged_output))
            or os.path.getsize(_python_io_path(staged_output)) == 0
        ):
            return False

        _replace_file(staged_output, old_file)
        return True
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)


def apply_all_patches(
    dest_dir: str,
    workers: int = 8,
    on_progress=None,
    cancel_event=None,
    use_tqdm: bool = True,
    patch_root: str | Path = PATCH_read_DIR,
) -> tuple[int, int, int]:
    """Apply all patches; returns (total, succeeded, failed).

    patch_root defaults to the existing standalone package location, but can
    point at a materialized web package cache.
    """

    patch_root = Path(patch_root)
    zstd_files = list(patch_root.rglob("*.zst"))
    total = len(zstd_files)

    if not zstd_files:
        print("No .zst patches found.")
        return (0, 0, 0)

    ok = 0
    fail = 0
    done = 0
    lock = threading.Lock()

    with tqdm(
        total=total,
        desc="Applying patches",
        unit="file",
        file=_tqdm_file(),
        disable=_tqdm_disable() if use_tqdm else True,
    ) as bar:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {
                ex.submit(_apply_single, p, Path(dest_dir), patch_root, cancel_event): p
                for p in zstd_files
            }
            for fut in as_completed(futs):
                if cancel_event and cancel_event.is_set():
                    break

                res = fut.result()
                with lock:
                    done += 1
                    if res:
                        ok += 1
                    else:
                        fail += 1

                if on_progress:
                    on_progress("install:patch", done, total, f"applied {done}/{total}")

                bar.update(1)

    print(f"done. success={ok}, failed={fail}, total={total}")
    return (total, ok, fail)


# Utility counts (optional helpers for GUI to pre-compute totals)

def count_dest_files(dest_root: str) -> int:
    c = 0
    for r, _, fs in os.walk(dest_root):
        for f in fs:
            path = os.path.join(r, f)
            if not is_package_excluded(path, dest_root):
                c += 1
    return c


def count_patch_files(patch_root: str | Path = PATCH_read_DIR) -> int:
    return sum(1 for _ in Path(patch_root).rglob("*.zst"))


def _verify_single(patch_path: Path, cancel_event=None) -> tuple[bool, Path]:
    """Verify a single .zst patch file with zstd -t. Returns (ok, patch_path)."""

    stage_dir: str | None = None
    zstd_path = str(patch_path)
    try:
        if _external_path_is_long(patch_path):
            stage_parent = str(Path(PATCH_out_DIR).parent)
            os.makedirs(_python_io_path(stage_parent), exist_ok=True)
            stage_dir = tempfile.mkdtemp(prefix="sierra_test_", dir=stage_parent)
            zstd_path = os.path.join(stage_dir, "patch.zst")
            _copy_file(patch_path, zstd_path)

        run_quiet(
            [ZSTD_EXE, "-t", zstd_path, "-T1"],
            check=True,
            capture=True,
            cancel_event=cancel_event,
        )
        return True, patch_path
    except subprocess.CalledProcessError:
        return False, patch_path
    finally:
        if stage_dir:
            shutil.rmtree(stage_dir, ignore_errors=True)


def verify_patch_files(
    cancel_event=None,
    on_progress=None,
    workers: int | None = None,
    fast_fail: int = 0,
) -> bool:
    """Parallel verification of all .zst patches under PATCH_out_DIR.

    - Calls _verify_single(...) in a thread pool.
    - Reports ABSOLUTE progress via on_progress("verify:patches", done, total, msg).
    - Respects cancel_event.
    - If fast_fail>0, cancels after that many failures.
    """

    patches = list(Path(PATCH_out_DIR).rglob("*.zst"))
    total = len(patches)

    if total == 0:
        if on_progress:
            on_progress("verify:patches", 0, 0, "No patches to verify")
        print("all patches OK (none found)")
        return True

    max_workers = workers or min(32, max(4, (os.cpu_count() or 4)))
    bad: list[str] = []
    done = 0
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_verify_single, p, cancel_event): p for p in patches}

        for fut in as_completed(futs):
            if cancel_event and cancel_event.is_set():
                break

            ok, p = fut.result()
            with lock:
                done += 1
                if not ok:
                    bad.append(str(p))

            if fast_fail and len(bad) >= fast_fail and cancel_event:
                cancel_event.set()

            if on_progress:
                on_progress("verify:patches", done, total, f"Validating patches {done}/{total}")

    if cancel_event and cancel_event.is_set() and fast_fail and len(bad) >= fast_fail:
        print(f"stopped after {len(bad)} failures (fast-fail)")
        return False

    if bad:
        print(f"invalid patches: {len(bad)}")
        for b in bad[:10]:
            print(" -", b)
        return False

    print("all patches OK")
    return True


def _tqdm_file():
    """Return a file-like object for tqdm to write to.

    In GUI builds, sys.stderr may be None; fall back to a sink.
    """

    f = getattr(sys, "stderr", None)
    return f if (f is not None and hasattr(f, "write")) else io.StringIO()


def _tqdm_disable() -> bool:
    """Disable tqdm when there is no real stderr (GUI build) or when explicitly requested.

    Env override:
      - SIERRA_TQDM=0 forces enable
      - SIERRA_TQDM=1 forces disable
    """

    env = os.environ.get("SIERRA_TQDM")
    if env == "0":
        return False
    if env == "1":
        return True

    f = getattr(sys, "stderr", None)
    return not (f is not None and hasattr(f, "write"))
