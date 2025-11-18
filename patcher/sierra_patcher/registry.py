import winreg
import win32api
import os
from pathlib import Path


_STEAM_UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 3932890"
_BSG_UNINSTALL_KEY   = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\EscapeFromTarkov"

_UNINSTALL_KEYS = [
    _STEAM_UNINSTALL_KEY,
    _BSG_UNINSTALL_KEY,
]


def _query_single_uninstall(path: str) -> dict | None:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, access=winreg.KEY_READ) as k:
            def q(name):
                try: 
                    val, _ = winreg.QueryValueEx(k, name)
                    return val
                except FileNotFoundError: 
                    return None
            loc = q("InstallLocation")
            if not loc:
                return None
            info = {
                "install_path": Path(loc),
                #display_version": _q("DisplayVersion"),
                #publisher": _q("Publisher"),
                #the above does not appear for some people after the 40087 client update
            }
            return info
    except Exception:
        return None


def query_install():
    """
    Locate Tarkov installation, prioritizing Steam version.

    1) Try Steam uninstall key (Steam App 3932890).
    2) If that fails, try the old BSG launcher uninstall key.
    3) If both fail, return None.

    Returns:
        dict with at least:
            {
                "install_path": Path(...),
                "display_version": str,
                "publisher": str,
            }
        or None if nothing is found.
    """
    for key in _UNINSTALL_KEYS:
        info = _query_single_uninstall(key)
        if info is not None:
            if key == _STEAM_UNINSTALL_KEY:
                info["install_path"] = os.path.join(info["install_path"], "build")
            return info
    return None


def exe_version(path: str) -> str | None:
    """Read file version from EscapeFromTarkov.exe."""
    try:
        info = win32api.GetFileVersionInfo(path, "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None