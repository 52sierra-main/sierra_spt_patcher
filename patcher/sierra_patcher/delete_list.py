import os
from pathlib import Path


# generator: list files in source that are absent in dest → delete_list.txt


def build_delete_list(source_root: str, dest_root: str, out_path: str) -> None:
items = []
for r, _, fs in os.walk(source_root):
for f in fs:
rel = os.path.relpath(os.path.join(r, f), source_root)
if not os.path.exists(os.path.join(dest_root, rel)):
items.append(rel)
Path(out_path).write_text("\n".join(items) + "\n", encoding="utf-8")


# installer: remove listed files and empty dirs


def finalize(dest_dir: str, delete_list_path: str) -> None:
p = Path(delete_list_path)
if not p.exists():
print(f"delete list not found: {p}")
return
for line in p.read_text(encoding="utf-8").splitlines():
if not line.strip():
continue
fp = Path(dest_dir, line.strip())
try:
if fp.exists():
fp.unlink()
print("deleted:", fp)
except Exception as e:
print("failed to delete", fp, e)
# remove empty folders (bottom-up)
for root, dirs, files in os.walk(dest_dir, topdown=False):
for d in dirs:
dp = Path(root, d)
try:
if not any(dp.iterdir()):
dp.rmdir()
print("removed empty:", dp)
except Exception:
pass
