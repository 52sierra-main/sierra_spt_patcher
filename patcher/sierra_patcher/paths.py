# sierra_patcher/paths.py
from __future__ import annotations
import os, sys
from pathlib import Path

# Base dir of package at runtime (works in dev and PyInstaller EXE)
def _app_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # PyInstaller staging dir
    return Path(__file__).resolve().parent

APP_ROOT: Path = _app_root()

# Binaries bundled via spec
BIN_DIR: Path = APP_ROOT / "bin"
ZSTD_DIR: Path = BIN_DIR / "zstd64"

# Standardized executable names (strings, because subprocess likes str)
ZSTD_EXE: str      = str(ZSTD_DIR / "zstd.exe")
SEVENZIP: str  = str(BIN_DIR / "7za.exe")  # 7-Zip standalone CLI

# Output layout (adjust if you changed these elsewhere)
OUTPUT_DIR: str   = str(APP_ROOT / "patch_output")
PATCH_DIR: str    = str(Path(OUTPUT_DIR) / "patchfiles")
MISSING_DIR: str  = str(Path(OUTPUT_DIR) / "additional_files")
STORAGE_DIR: str  = str(Path(OUTPUT_DIR) / "storage")

# Ensure directories exist when imported (safe no-ops if they already exist)
for d in (OUTPUT_DIR, PATCH_DIR, MISSING_DIR, STORAGE_DIR):
    os.makedirs(d, exist_ok=True)

__all__ = [
    "APP_ROOT", "BIN_DIR", "ZSTD_DIR",
    "ZSTD_EXE", "SEVENZIP_EXE",
    "OUTPUT_DIR", "PATCH_DIR", "MISSING_DIR", "STORAGE_DIR",
]
