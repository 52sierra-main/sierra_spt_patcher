import os, shutil, filecmp, subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from .paths import ZSTD_EXE, PATCH_DIR

# ----- GENERATOR -----

def process_file(source_root: str, dest_root: str, dest_file: str, out_root: str, missing_root: str) -> None:
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
    subprocess.run([ZSTD_EXE, "--patch-from", src, dest_file, "-o", patch_file, "--long=31", "-T1"], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    # quick verification: apply to a temp copy and compare
    src_tmp, out_tmp = src + ".tmp_src", dest_file + ".tmp_out"
    try:
        shutil.copy(src, src_tmp)
        subprocess.run([ZSTD_EXE, "-d", "--patch-from", src_tmp, patch_file, "-o", out_tmp, "--long=31", "-T1"],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if not filecmp.cmp(dest_file, out_tmp, shallow=False):
            raise RuntimeError(f"verification failed for {rel}")
    finally:
        for p in (src_tmp, out_tmp):
            if os.path.exists(p):
                os.remove(p)


def generate_patches(source_root: str, dest_root: str, out_root: str, missing_root: str, workers: int = 8) -> None:
    files = []
    for r, _, fs in os.walk(dest_root):
        for f in fs:
            files.append(os.path.join(r, f))

    with tqdm(total=len(files), desc="Generating patches", unit="file") as bar:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(process_file, source_root, dest_root, f, out_root, missing_root) for f in files]
            for _ in as_completed(futs):
                bar.update(1)

# ----- INSTALLER -----

def _apply_single(patch_file: Path, dest_dir: Path) -> bool:
    rel = patch_file.relative_to(PATCH_DIR).with_suffix("")
    old_file = dest_dir / rel
    if not old_file.exists():
        tqdm.write(f"missing target: {rel}")
        return False
    tmp = old_file.with_suffix(old_file.suffix + ".new")
    try:
        subprocess.run([ZSTD_EXE, "-d", "--patch-from", str(old_file), str(patch_file), "-o", str(tmp), "-T1", "--long=31"],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if not tmp.exists() or tmp.stat().st_size == 0:
            if tmp.exists(): tmp.unlink()
            return False
        os.replace(tmp, old_file)
        return True
    except subprocess.CalledProcessError as e:
        if tmp.exists(): tmp.unlink()
        tqdm.write(f"error patching {rel}: {e.stderr.strip() if e.stderr else e}")
        return False


def apply_all_patches(dest_dir: str, workers: int = 8) -> bool:
    zstd_files = list(Path(PATCH_DIR).rglob("*.zst"))
    if not zstd_files:
        print("No .zst patches found.")
        return False
    ok = 0; fail = 0
    with tqdm(total=len(zstd_files), desc="Applying patches", unit="file") as bar:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_apply_single, p, Path(dest_dir)): p for p in zstd_files}
            for fut in as_completed(futs):
                ok += 1 if fut.result() else 0
                fail += 0 if fut.result() else 1
                bar.update(1)
    print(f"done. success={ok}, failed={fail}, total={len(zstd_files)}")
    return fail == 0


def verify_patch_files() -> bool:
    from .paths import ZSTD_EXE, PATCH_DIR
    bad = []
    for p in Path(PATCH_DIR).rglob("*.zst"):
        try:
            subprocess.run([ZSTD_EXE, "-t", str(p)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except subprocess.CalledProcessError:
            bad.append(p.name)
    if bad:
        print(f"invalid patches: {len(bad)}")
        for b in bad[:10]: print("  -", b)
        return False
    print("all patches OK")
    return True
