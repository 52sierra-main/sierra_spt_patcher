from __future__ import annotations
from pathlib import Path
import subprocess
from .io import resource_path


_SEVEN_ZA = Path(resource_path("bin", "7za.exe"))




def pack_storage(input_dir: Path, out_file: Path, password: str | None = None) -> bool:
out_file.parent.mkdir(parents=True, exist_ok=True)
cmd = [str(_SEVEN_ZA), "a", "-y", str(out_file), "."]
if password:
cmd.extend([f"-p{password}", "-mhe=on"]) # encrypt headers too
res = subprocess.run(cmd, cwd=input_dir, capture_output=True, text=True)
return res.returncode == 0




def unpack_storage(archive_file: Path, target_dir: Path, password: str | None = None) -> bool:
target_dir.mkdir(parents=True, exist_ok=True)
cmd = [str(_SEVEN_ZA), "x", "-y", str(archive_file), f"-o{target_dir}"]
if password:
cmd.extend([f"-p{password}"])
res = subprocess.run(cmd, capture_output=True, text=True)
return res.returncode == 0
