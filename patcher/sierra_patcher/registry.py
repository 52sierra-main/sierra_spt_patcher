import winreg, win32api
from pathlib import Path


_UNINSTALL_KEY = (r"SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall"
r"\\EscapeFromTarkov")


class TarkovInstall:
def __init__(self, install_path: Path, version: str, publisher: str):
self.install_path = install_path
self.version = version
self.publisher = publisher


def __repr__(self) -> str:
return f"TarkovInstall(path={self.install_path}, version={self.version}, publisher={self.publisher})"


def query_install() -> TarkovInstall | None:
try:
with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _UNINSTALL_KEY, access=winreg.KEY_READ) as k:
def q(name):
try: val, _ = winreg.QueryValueEx(k, name); return val
except FileNotFoundError: return None
loc = q("InstallLocation")
if not loc:
return None
return TarkovInstall(Path(loc), q("DisplayVersion"), q("Publisher"))
except Exception:
return None


# File version (EscapeFromTarkov.exe)


def exe_version(path: str) -> str | None:
try:
info = win32api.GetFileVersionInfo(path, '\\')
ms, ls = info['FileVersionMS'], info['FileVersionLS']
return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
except Exception:
return None
