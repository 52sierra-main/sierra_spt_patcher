from __future__ import annotations
from pathlib import Path
from typing import Iterable
import json


try:
import xxhash # prefer very fast non-crypto hash
def _hfile(p: Path) -> str:
h = xxhash.xxh3_128()
with p.open("rb") as f:
for chunk in iter(lambda: f.read(1024 * 1024), b""):
h.update(chunk)
return h.hexdigest()
except Exception:
import hashlib
def _hfile(p: Path) -> str:
h = hashlib.blake2b(digest_size=16)
with p.open("rb") as f:
for chunk in iter(lambda: f.read(1024 * 1024), b""):
h.update(chunk)
return h.hexdigest()




def build_manifest(root: Path, rel_glob: str = "**/*") -> dict:
files: dict[str, dict] = {}
for p in root.glob(rel_glob):
if p.is_file():
rel = str(p.relative_to(root)).replace("\\", "/")
files[rel] = {"size": p.stat().st_size, "hash": _hfile(p)}
return {"root": str(root), "files": files}




def save_manifest(man: dict, out_path: Path) -> None:
out_path.write_text(json.dumps(man, indent=2), encoding="utf-8")




def verify_manifest(root: Path, man: dict) -> list[str]:
errors: list[str] = []
files = man.get("files", {})
for rel, meta in files.items():
p = root / rel
if not p.exists():
errors.append(f"Missing: {rel}")
continue
if p.stat().st_size != meta.get("size"):
errors.append(f"Size mismatch: {rel}")
continue
if _hfile(p) != meta.get("hash"):
errors.append(f"Hash mismatch: {rel}")
return errors
