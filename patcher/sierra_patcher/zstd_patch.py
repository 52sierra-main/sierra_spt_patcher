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

    os.makedirs(os.path.dirname(patch_file), exist_ok=True)

    if not os.path.exists(src):
        # not in source → collect as "additional"
        copy_package_file(dest_file, missing_root, rel)
        return "additional"

    if filecmp.cmp(src, dest_file, shallow=False):
        return "identical"

    args = _normalize_zstd_args(zstd_args)

    # create patch
    run_quiet(
        [ZSTD_EXE, "--patch-from", src, dest_file, "-o", patch_file, *args, "-T1"],
        check=True,
        capture=True,
        cancel_event=cancel_event,
    )

    # Quick verification: apply to a temp file outside source/target trees.
    verify_dir = tempfile.mkdtemp(prefix="sierra_verify_", dir=str(Path(out_root).parent))
    out_tmp = str(Path(verify_dir) / Path(dest_file).name)
    try:
        run_quiet(
            [
                ZSTD_EXE,
                "-d",
                "--patch-from",
                src,
                patch_file,
                "-o",
                out_tmp,
                *_decode_zstd_args(args),
                "-T1",
            ],
            check=True,
            capture=True,
            cancel_event=cancel_event,
        )

        if not filecmp.cmp(dest_file, out_tmp, shallow=False):
            raise RuntimeError(f"verification failed for {rel}")

        patch_size = os.path.getsize(patch_file)
        target_size = os.path.getsize(dest_file)
        if patch_size > target_size:
            copy_package_file(dest_file, missing_root, rel)
            os.remove(patch_file)
            _log(
                f"kept full file for {rel} "
                f"(delta {format_size(patch_size)} > target {format_size(target_size)})"
            )
            return "full"

        return "delta"
    finally:
        shutil.rmtree(verify_dir, ignore_errors=True)


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

                result = fut.result()
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
        _log(f"error patching {rel}: {e.stderr.strip() if e.stderr else e}")
        return False


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

    try:
        # Per-process threads=1 to avoid CPU oversubscription when parallelized
        run_quiet(
            [ZSTD_EXE, "-t", str(patch_path), "-T1"],
            check=True,
            capture=True,
            cancel_event=cancel_event,
        )
        return True, patch_path
    except subprocess.CalledProcessError:
        return False, patch_path


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
                    bad.append(p.name)

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
