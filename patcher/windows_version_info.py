from __future__ import annotations

import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)


_VERSION_PATTERN = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def _package_version(project_root: str | Path) -> str:
    init_file = Path(project_root) / "sierra_patcher" / "__init__.py"
    text = init_file.read_text(encoding="utf-8")
    match = _VERSION_PATTERN.search(text)
    if match is None:
        raise RuntimeError(f"could not read __version__ from {init_file}")
    return match.group(1).strip()


def _windows_version_tuple(version: str) -> tuple[int, int, int, int]:
    """Convert a normal package version into Windows' four 16-bit integers."""

    core = str(version).strip().split("-", 1)[0].split("+", 1)[0]
    parts = core.split(".")
    if not 1 <= len(parts) <= 4:
        raise ValueError(f"unsupported Windows file version: {version!r}")

    numbers: list[int] = []
    for part in parts:
        if not part.isdigit():
            raise ValueError(f"unsupported Windows file version: {version!r}")
        value = int(part)
        if not 0 <= value <= 65535:
            raise ValueError(f"Windows file version component is out of range: {value}")
        numbers.append(value)

    while len(numbers) < 4:
        numbers.append(0)
    return tuple(numbers)  # type: ignore[return-value]


def build_version_info(project_root: str | Path) -> VSVersionInfo:
    """Build the VERSIONINFO resource embedded into the public Windows EXE."""

    version = _package_version(project_root)
    numeric_version = _windows_version_tuple(version)

    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=numeric_version,
            prodvers=numeric_version,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct("CompanyName", "Sierra"),
                            StringStruct("FileDescription", "Sierra Installer for SPT"),
                            StringStruct("FileVersion", version),
                            StringStruct("InternalName", "sierra-patcher"),
                            StringStruct("LegalCopyright", "Copyright © 2026 Sierra"),
                            StringStruct("OriginalFilename", "sierra-patcher.exe"),
                            StringStruct("ProductName", "Sierra Installer"),
                            StringStruct("ProductVersion", version),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )
