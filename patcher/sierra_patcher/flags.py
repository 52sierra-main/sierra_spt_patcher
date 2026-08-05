from __future__ import annotations

import os
import sys
from pathlib import Path


def _truthy_env(name: str) -> bool:
    value = (os.environ.get(name) or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def is_dev_mode() -> bool:
    """Return True when developer-only features should be enabled.

    Supported opt-ins:
    - SIERRA_DEV=1 (or true/yes/on)
    - dev.enable beside a frozen executable
    - dev.enable beside the Python package
    - dev.enable in the project root (parent of sierra_patcher/)
    - dev.enable in the current working directory

    Checking several well-defined locations avoids virtual-environment and
    launch-directory surprises while keeping dev mode an explicit opt-in.
    """

    if _truthy_env("SIERRA_DEV"):
        return True

    package_dir = Path(__file__).resolve().parent
    candidates = {
        package_dir / "dev.enable",
        package_dir.parent / "dev.enable",
        Path.cwd() / "dev.enable",
    }

    if getattr(sys, "frozen", False):
        candidates.add(Path(sys.executable).resolve().parent / "dev.enable")

    return any(path.is_file() for path in candidates)
