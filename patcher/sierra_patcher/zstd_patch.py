import os, shutil, filecmp, subprocess, threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from .proc import run_quiet
import sys, io
from .paths import ZSTD_EXE, PATCH_out_DIR, PATCH_read_DIR

# Optional progress callback signature:
#   on_progress(phase: str, current: int, total: int, message: str)

# ----- GENERATOR -----

def process_file(source_root: str, dest_root: str, dest_file: str, out_root: str, missing_root: str,cancel_event=None) -> None:
    rel = os.path.relpath(dest_file, dest_root)
    src = os.path.join(source_root, rel)
    patch_file = os.path.join(out_root, rel + ".zst")
    os.makedirs(os.path.dirname(patch_file), exist_ok=True)

    if not os.path.exists(src):
        # not in source → collect as "additional"
        dst_missing = os.path.join(missing_root, rel)
        os.makedirs(os.path.dirname(dst_missing), exist_ok=True)
        shutil.copy(dest_file, dst_missing)
        return

    if filecmp.cmp(src, dest_file, shallow=False):
        return  # identical

    # create patch
    run_quiet([ZSTD_EXE, "--patch-from", src, dest_file, "-o", patch_file, "--long=31", "-T1"], check=True,
                   capture=True, cancel_event=cancel_event)

    # quick verification: apply to a temp copy and compare
    src_tmp, out_tmp = src + ".tmp_src", dest_file + ".tmp_out"
    try:
        shutil.copy(src, src_tmp)
        run_quiet([ZSTD_EXE, "-d", "--patch-from", src_tmp, patch_file, "-o", out_tmp, "--long=31", "-T1"],
                       check=True, capture=True, cancel_event=cancel_event)
        if not filecmp.cmp(dest_file, out_tmp, shallow=False):
            raise RuntimeError(f"verification failed for {rel}")
    finally:
        for p in (src_tmp, out_tmp):
            if os.path.exists(p):
                os.remove(p)


def generate_patches(source_root: str, dest_root: str, out_root: str, missing_root: str,
                      workers: int = 8, on_progress=None, cancel_event=None, use_tqdm=True) -> int:
    """Generate patches; returns number of processed files (including skipped/added)."""
    files = []
    for r, _, fs in os.walk(dest_root):
        for f in fs:
            files.append(os.path.join(r, f))

    total = len(files)
    done = 0
    lock = threading.Lock()

    with tqdm(total=total, desc="Generating patches", unit="file", file=_tqdm_file(), disable=_tqdm_disable()) as bar:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(process_file, source_root, dest_root, f, out_root, missing_root, cancel_event) for f in files]
            for _ in as_completed(futs):
                if cancel_event and cancel_event.is_set():
                    break
                with lock:
                    done += 1
                    if on_progress:
                        on_progress("generate:patch", done, total, f"patched {done}/{total}")
                bar.update(1)
    return total

# ----- INSTALLER -----

def _apply_single(patch_file: Path, dest_dir: Path, cancel_event=None) -> bool:
    rel = patch_file.relative_to(PATCH_read_DIR).with_suffix("")
    old_file = dest_dir / rel
    if not old_file.exists():
        tqdm.write(f"missing target: {rel}")
        return False
    tmp = old_file.with_suffix(old_file.suffix + ".new")
    try:
        run_quiet([ZSTD_EXE, "-d", "--patch-from", str(old_file), str(patch_file), "-o", str(tmp), "-T1", "--long=31"],
                       check=True, capture=True, cancel_event=cancel_event)
        if not tmp.exists() or tmp.stat().st_size == 0:
            if tmp.exists(): tmp.unlink()
            return False
        os.replace(tmp, old_file)
        return True
    except subprocess.CalledProcessError as e:
        if tmp.exists(): tmp.unlink()
        tqdm.write(f"error patching {rel}: {e.stderr.strip() if e.stderr else e}")
        return False


def apply_all_patches(dest_dir: str, workers: int = 8, on_progress=None, cancel_event=None, use_tqdm=True) -> tuple[int, int, int]:
    """Apply all patches; returns (total, succeeded, failed)."""
    zstd_files = list(Path(PATCH_read_DIR).rglob("*.zst"))
    total = len(zstd_files)
    if not zstd_files:
        print("No .zst patches found.")
        return (0, 0, 0)

    ok = 0; fail = 0; done = 0
    lock = threading.Lock()

    with tqdm(total=total, desc="Applying patches", unit="file", file=_tqdm_file(), disable=_tqdm_disable()) as bar:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_apply_single, p, Path(dest_dir), cancel_event): p for p in zstd_files}
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
    for _, _, fs in os.walk(dest_root):
        c += len(fs)
    return c

def count_patch_files() -> int:
    return sum(1 for _ in Path(PATCH_read_DIR).rglob("*.zst"))

def verify_patch_files(cancel_event=None, on_progress=None) -> bool:
    bad = []
    patches = list(Path(PATCH_out_DIR).rglob("*.zst"))
    total = len(patches)
    
    for i, p in enumerate(patches, 1):
        if cancel_event and cancel_event.is_set():
            # user cancelled; report where we got to and exit early
            if on_progress:
                on_progress("verify:patches", i-1, total, "Cancelled")
            return False
        try:
            run_quiet([ZSTD_EXE, "-t", str(p)],
                      check=True, capture=True, cancel_event=cancel_event)
        except subprocess.CalledProcessError:
            bad.append(p.name)
        finally:
            if on_progress:
                on_progress("verify:patches", i, total, f"Validating patches {i}/{total}")
    if bad:
        print(f"invalid patches: {len(bad)}")
        for b in bad[:10]: print("  -", b)
        return False
    print("all patches OK")
    return True

def _tqdm_file():
    """
    Return a file-like object for tqdm to write to.
    In GUI builds, sys.stderr may be None; fall back to a sink.
    """
    f = getattr(sys, "stderr", None)
    return f if (f is not None and hasattr(f, "write")) else io.StringIO()

def _tqdm_disable():
    """
    Disable tqdm when there is no real stderr (GUI build) or when explicitly requested.
    Env override: SIERRA_TQDM=0 forces enable, =1 forces disable.
    """
    env = os.environ.get("SIERRA_TQDM")
    if env == "0":
        return False
    if env == "1":
        return True
    f = getattr(sys, "stderr", None)
    return not (f is not None and hasattr(f, "write"))

