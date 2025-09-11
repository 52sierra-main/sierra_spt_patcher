from __future__ import annotations
import os
from pathlib import Path
from typing import Optional


# NOTE: these are stubs that you can extend with your existing logic.




def detect_eft_version(exe_path: Path) -> Optional[str]:
# Implement your file version extraction here (ctypes/pefile/win32api)
# Return like "0.14.x.x" or None.
return None




def ensure_prereqs(interactive: bool = True) -> None:
"""Check .NET frameworks and offer installation.
Immediate fix: call this in BOTH auto and manual flows in spt_installer.
"""
# TODO: insert your actual checks & silent installers.
pass
