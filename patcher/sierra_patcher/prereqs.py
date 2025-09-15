import urllib.request, shutil, subprocess, tempfile, winreg, time
from pathlib import Path

_CACHE = Path(tempfile.gettempdir()) / "dotnet_cache"
_CACHE.mkdir(exist_ok=True)

_DEF = {
    "netfx472": ("https://download.visualstudio.microsoft.com/download/pr/1f5af042-d0e4-4002-9c59-9ba66bcf15f6/124d2afe5c8f67dfa910da5f9e3db9c1/ndp472-kb4054531-web.exe",
                  "/quiet /norestart /log ndp472.log"),
    5: ("https://builds.dotnet.microsoft.com/dotnet/WindowsDesktop/5.0.0/windowsdesktop-runtime-5.0.0-win-x64.exe",
        "/install /quiet /norestart"),
    6: ("https://builds.dotnet.microsoft.com/dotnet/WindowsDesktop/6.0.4/windowsdesktop-runtime-6.0.4-win-x64.exe",
        "/install /quiet /norestart"),
    8: ("https://builds.dotnet.microsoft.com/dotnet/WindowsDesktop/8.0.16/windowsdesktop-runtime-8.0.16-win-x64.exe",
        "/install /quiet /norestart"),
}

def _fetch(url: str) -> Path:
    dst = _CACHE / Path(url).name
    if dst.exists():
        return dst
    with urllib.request.urlopen(url) as r, open(dst, "wb") as fp:
        shutil.copyfileobj(r, fp)
    return dst

# dotnet 4.7.2

def has_netfx472() -> bool:
    key = r"SOFTWARE\\Microsoft\\NET Framework Setup\\NDP\\v4\\Full"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            release, _ = winreg.QueryValueEx(k, "Release")
            return release >= 461808
    except FileNotFoundError:
        return False

# desktop runtimes list

def runtimes() -> list[str]:
    try:
        out = subprocess.check_output(["dotnet", "--list-runtimes"], text=True, stderr=subprocess.DEVNULL)
        return out.splitlines()
    except Exception:
        return []

def need_desktop(major: int) -> bool:
    needle = f"Microsoft.WindowsDesktop.App {major}."
    return not any(needle in line for line in runtimes())


def ensure_prereqs(interactive: bool = True) -> None:
    missing = []
    if not has_netfx472():
        missing.append(".NET Framework 4.7.2")
    for m in (5, 6, 8):
        if need_desktop(m):
            missing.append(f".NET {m} Desktop Runtime")

    if not missing:
        print("All .NET prerequisites present.")
        return

    print("Missing prerequisites:\n - " + "\n - ".join(missing))
    if not interactive:
        print("Non-interactive mode: installing silently...")
        do_install = True
    else:
        ans = input("Install them now? (y/n): ").strip().lower()
        do_install = (ans == 'y')

    if not do_install:
        print("Skipping .NET install.")
        return

    # install sequence
    tasks: list[tuple[Path, str]] = []
    if not has_netfx472():
        url, args = _DEF["netfx472"]
        tasks.append((_fetch(url), args))
    for m in (5, 6, 8):
        if need_desktop(m):
            url, args = _DEF[m]
            tasks.append((_fetch(url), args))

    for exe, args in tasks:
        print(f"Installing {exe.name} ...")
        code = subprocess.call([str(exe), *args.split()], shell=False)
        if code == 0:
            print(f"  ✓ {exe.name} installed")
        else:
            print(f"  ⚠ {exe.name} exit code {code} (continuing)")
        time.sleep(1)
