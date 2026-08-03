from __future__ import annotations

import os
import re
import winreg
import win32api
from pathlib import Path


APPID = "3932890"

_STEAM_UNINSTALL_KEYS = [
    rf"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App {APPID}",
    rf"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Steam App {APPID}",
]

_BSG_UNINSTALL_KEYS = [
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\EscapeFromTarkov",
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\EscapeFromTarkov",
]

_STEAM_INSTALL_KEYS = [
    r"SOFTWARE\WOW6432Node\Valve\Steam",
    r"SOFTWARE\Valve\Steam",
]


def _read_reg_value(root, path: str, name: str):
    try:
        with winreg.OpenKey(root, path, access=winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, name)
            return val
    except Exception:
        return None


def _valid_tarkov_dir(path: Path) -> bool:
    return (path / "EscapeFromTarkov.exe").is_file()


def _parse_vdf_key_values(text: str, key: str) -> list[str]:
    # Matches: "key"    "value"
    pattern = re.compile(rf'"{re.escape(key)}"\s+"([^"]+)"', re.IGNORECASE)
    return pattern.findall(text)


def _get_steam_root() -> Path | None:
    for key in _STEAM_INSTALL_KEYS:
        for value_name in ("InstallPath", "SteamPath"):
            val = _read_reg_value(winreg.HKEY_LOCAL_MACHINE, key, value_name)
            if val:
                p = Path(val)
                if p.exists():
                    return p

    # Common fallback
    p = Path(r"C:\Program Files (x86)\Steam")
    if p.exists():
        return p

    return None


def _read_steam_libraries(steam_root: Path) -> list[Path]:
    libraries: list[Path] = [steam_root]

    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    if not vdf.is_file():
        return libraries

    try:
        text = vdf.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return libraries

    for raw_path in _parse_vdf_key_values(text, "path"):
        path = Path(raw_path.replace("\\\\", "\\"))
        if path.exists() and path not in libraries:
            libraries.append(path)

    return libraries


def _query_steam_manifest() -> dict | None:
    steam_root = _get_steam_root()
    if not steam_root:
        return None

    for library in _read_steam_libraries(steam_root):
        manifest = library / "steamapps" / f"appmanifest_{APPID}.acf"
        if not manifest.is_file():
            continue

        try:
            text = manifest.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        installdirs = _parse_vdf_key_values(text, "installdir")
        if not installdirs:
            continue

        base = library / "steamapps" / "common" / installdirs[0]

        candidates = [
            base / "build",
            base,
        ]

        for candidate in candidates:
            if _valid_tarkov_dir(candidate):
                return {
                    "install_path": candidate,
                    "source": "steam_appmanifest",
                }

    return None


def _query_steam_uninstall() -> dict | None:
    for key in _STEAM_UNINSTALL_KEYS:
        loc = _read_reg_value(winreg.HKEY_LOCAL_MACHINE, key, "InstallLocation")
        if not loc:
            continue

        base = Path(loc)

        candidates = [
            base / "build",
            base,
        ]

        for candidate in candidates:
            if _valid_tarkov_dir(candidate):
                return {
                    "install_path": candidate,
                    "source": "steam_uninstall_registry",
                }

    return None


def _query_bsg_uninstall() -> dict | None:
    for key in _BSG_UNINSTALL_KEYS:
        loc = _read_reg_value(winreg.HKEY_LOCAL_MACHINE, key, "InstallLocation")
        if not loc:
            continue

        path = Path(loc)

        candidates = [
            path,
            path / "build",
        ]

        for candidate in candidates:
            if _valid_tarkov_dir(candidate):
                return {
                    "install_path": candidate,
                    "source": "bsg_uninstall_registry",
                }

    return None


def query_install() -> dict | None:
    """
    Locate Tarkov installation.

    Priority:
    1. Steam appmanifest_3932890.acf
    2. Steam uninstall registry, if InstallLocation is populated
    3. Old BSG launcher registry
    """

    for finder in (
        _query_steam_manifest,
        _query_steam_uninstall,
        _query_bsg_uninstall,
    ):
        info = finder()
        if info:
            return info

    return None


def exe_version(path: str | os.PathLike) -> str | None:
    """Read file version from EscapeFromTarkov.exe."""
    try:
        info = win32api.GetFileVersionInfo(str(path), "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None