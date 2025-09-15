import os, sys
from pathlib import Path

__all__ = ["get_base_dir", "get_bin_dir", "SCRIPT_DIR", "BUNDLE_DIR",
           "PATCH_DIR", "OUTPUT_DIR", "MISSING_DIR", "STORAGE_DIR",
           "SEVENZIP", "ZSTD_EXE"]

def get_base_dir() -> str:
    """Directory of the script or PyInstaller executable."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

# In a onefile build, bundled files live next to the executable on recent PyInstaller.
# We keep a separate accessor to avoid mixing concerns.

def get_bin_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(get_base_dir())
    return Path(get_base_dir())

SCRIPT_DIR = get_base_dir()
BUNDLE_DIR = get_bin_dir()

OUTPUT_DIR  = os.path.join(SCRIPT_DIR, "patch_output")
PATCH_DIR   = os.path.join(OUTPUT_DIR, "patchfiles")
MISSING_DIR = os.path.join(OUTPUT_DIR, "additional_files")
STORAGE_DIR = os.path.join(OUTPUT_DIR, "storage")

SEVENZIP = str(BUNDLE_DIR / "bin" / "7za.exe")
ZSTD_EXE = str(BUNDLE_DIR / "bin" / "zstd64" / "zstd.exe")