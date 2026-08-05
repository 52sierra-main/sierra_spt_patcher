# sierra_patcher/paths.py
from __future__ import annotations
import os, sys
from pathlib import Path

# Where the package code lives (…/sierra_patcher)
PKG_ROOT: Path = Path(__file__).resolve().parent

# Where the bundled files live at runtime:
# - Frozen: sys._MEIPASS (top of the extracted bundle)
# - Dev: project root (one level above sierra_patcher/)
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    APP_ROOT: Path = Path(sys._MEIPASS)
else:
    APP_ROOT: Path = PKG_ROOT.parent

# ---- Assets (live under the package) ----
ASSET_DIR: Path = PKG_ROOT / "assets"
TITLE: str      = str(ASSET_DIR / "title.ico")

# ---- Binaries (your spec places them at top-level bin/) ----
BIN_DIR: Path    = APP_ROOT / "bin"
ZSTD_DIR: Path   = BIN_DIR / "zstd64"
ZSTD_EXE: str    = str(ZSTD_DIR / "zstd.exe")
SEVENZIP: str= str(BIN_DIR / "7za.exe")

# ---- Runtime/package working directory ----
def _working_dir() -> Path:
    """Directory used for package input/output beside the application.

    Frozen builds use the directory containing the public EXE.
    Source/dev runs use the project root containing sierra_patcher/ and bin/.

    Do not use sys.executable for source runs: inside a virtual environment
    that points to .venv/Scripts/python.exe and incorrectly places generated
    packages under .venv/Scripts.
    """
    if getattr(sys, "frozen", False):
        try:
            return Path(sys.executable).resolve().parent
        except Exception:
            return Path.cwd()
    return PKG_ROOT.parent

WORKING_DIR: Path = _working_dir()
OUTPUT_DIR: str   = str(WORKING_DIR / "patch_output")
PATCH_out_DIR: str    = str(Path(OUTPUT_DIR) / "patchfiles")
MISSING_out_DIR: str  = str(Path(OUTPUT_DIR) / "additional_files")
STORAGE_out_DIR: str  = str(Path(OUTPUT_DIR) / "storage")

PATCH_read_DIR: str    = str(Path(WORKING_DIR) / "patchfiles")
MISSING_read_DIR: str  = str(Path(WORKING_DIR) / "additional_files")
STORAGE_read_DIR: str  = str(Path(WORKING_DIR) / "storage")

__all__ = [
    "PKG_ROOT", "APP_ROOT", "WORKING_DIR",
    "ASSET_DIR", "TITLE",
    "BIN_DIR", "ZSTD_DIR",
    "ZSTD_EXE", "SEVENZIP",
    "OUTPUT_DIR", "PATCH_out_DIR", "MISSING_out_DIR", "STORAGE_out_DIR",
    "PATCH_read_DIR", "MISSING_read_DIR", "STORAGE_read_DIR",
]
