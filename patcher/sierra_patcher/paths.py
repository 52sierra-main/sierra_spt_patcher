# sierra_patcher/paths.py
from __future__ import annotations
import sys
from pathlib import Path

PKG_ROOT: Path = Path(__file__).resolve().parent

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    APP_ROOT: Path = Path(sys._MEIPASS)
else:
    APP_ROOT: Path = PKG_ROOT.parent

ASSET_DIR: Path = PKG_ROOT / "assets"
TITLE: str = str(ASSET_DIR / "title.ico")

BIN_DIR: Path = APP_ROOT / "bin"
ZSTD_DIR: Path = BIN_DIR / "zstd64"
ZSTD_EXE: str = str(ZSTD_DIR / "zstd.exe")


def _working_dir() -> Path:
    """Directory used for package input/output beside the application."""
    if getattr(sys, "frozen", False):
        try:
            return Path(sys.executable).resolve().parent
        except Exception:
            return Path.cwd()
    return PKG_ROOT.parent


WORKING_DIR: Path = _working_dir()
OUTPUT_DIR: str = str(WORKING_DIR / "patch_output")
PATCH_out_DIR: str = str(Path(OUTPUT_DIR) / "patchfiles")
MISSING_out_DIR: str = str(Path(OUTPUT_DIR) / "additional_files")
STORAGE_out_DIR: str = str(Path(OUTPUT_DIR) / "storage")

PATCH_read_DIR: str = str(Path(WORKING_DIR) / "patchfiles")
MISSING_read_DIR: str = str(Path(WORKING_DIR) / "additional_files")
STORAGE_read_DIR: str = str(Path(WORKING_DIR) / "storage")

__all__ = [
    "PKG_ROOT", "APP_ROOT", "WORKING_DIR",
    "ASSET_DIR", "TITLE",
    "BIN_DIR", "ZSTD_DIR", "ZSTD_EXE",
    "OUTPUT_DIR", "PATCH_out_DIR", "MISSING_out_DIR", "STORAGE_out_DIR",
    "PATCH_read_DIR", "MISSING_read_DIR", "STORAGE_read_DIR",
]
