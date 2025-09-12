import os, shutil, filecmp, subprocess


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
for b in bad[:10]: print(" -", b)
return False
print("all patches OK")
return True
