from __future__ import annotations
import os, sys, shutil
from pathlib import Path
from typing import Iterable


# Unified resource resolver that works both in source and PyInstaller builds
# Immediate fix: remove brittle get_base_dir/get_bin_dir variations.
def resource_path(*parts: str) -> str:
base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
return os.path.join(base, *([".."] + list(parts))) # up from core/ to repo root




def ensure_dir(p: Path) -> None:
p.mkdir(parents=True, exist_ok=True)




def safe_replace(src_tmp: Path, dst: Path) -> None:
"""Atomic-ish replace on same volume (Windows: os.replace is atomic).
Caller ensures src_tmp exists and is complete/verified.
"""
dst.parent.mkdir(parents=True, exist_ok=True)
os.replace(str(src_tmp), str(dst))




def rm_empty_dirs(root: Path) -> list[Path]:
"""Recursively remove empty folders, return removed list."""
removed: list[Path] = []
if not root.exists():
return removed
# Walk bottom-up so children are removed before parents
for dirpath, dirnames, filenames in os.walk(root, topdown=False):
p = Path(dirpath)
# ignore if any file remains
if not any(Path(dirpath).iterdir()):
try:
p.rmdir()
removed.append(p)
except OSError:
pass
return removed




def copytree_dup(src: Path, dst: Path) -> None:
"""Duplicate a directory tree (used by your generator for inherit targets)."""
if dst.exists():
shutil.rmtree(dst)
shutil.copytree(src, dst)
