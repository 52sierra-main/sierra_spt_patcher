import winreg
import win32api
from pathlib import Path

# Known standalone EFT uninstall key (old launcher)
_EFT_UNINSTALL_KEY = (
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
    r"\EscapeFromTarkov"
)

# Roots to scan when the direct key doesn't work (covers Steam etc.)
_UNINSTALL_ROOTS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
]


def _try_uninstall_key(root, path) -> dict | None:
    """Try to read InstallLocation from a specific uninstall key."""
    try:
        with winreg.OpenKey(root, path, access=winreg.KEY_READ) as k:
            try:
                loc, _ = winreg.QueryValueEx(k, "InstallLocation")
            except FileNotFoundError:
                return None
            if not loc:
                return None

            install_path = Path(loc)
            if not install_path.is_dir():
                return None

            # Require the actual game exe to be present
            if not (install_path / "EscapeFromTarkov.exe").exists():
                return None

            return {
                "install_path": install_path,
                # you can add more fields later if you want
            }
    except OSError:
        return None


def _scan_all_uninstall() -> dict | None:
    """Fallback: scan all uninstall entries for an InstallLocation that has EscapeFromTarkov.exe."""
    for root, base in _UNINSTALL_ROOTS:
        try:
            with winreg.OpenKey(root, base, access=winreg.KEY_READ) as hkey:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(hkey, i)
                        i += 1
                    except OSError:
                        break

                    full_subkey = base + "\\" + sub
                    info = _try_uninstall_key(root, full_subkey)
                    if info is not None:
                        return info
        except OSError:
            # that uninstall root might not exist, just skip
            continue
    return None


def query_install():
    """
    Locate Tarkov installation.

    1) Try the classic EscapeFromTarkov uninstall key (old launcher).
    2) If that fails, scan all uninstall entries in the standard roots and
       pick any InstallLocation that contains EscapeFromTarkov.exe.

    Returns:
        dict {"install_path": Path(...)} or None if not found.
    """
    # Fast path: original EFT key
    info = _try_uninstall_key(winreg.HKEY_LOCAL_MACHINE, _EFT_UNINSTALL_KEY)
    if info is not None:
        return info

    # Fallback: Steam / other installers
    return _scan_all_uninstall()


def exe_version(path: str) -> str | None:
    try:
        info = win32api.GetFileVersionInfo(path, "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None
