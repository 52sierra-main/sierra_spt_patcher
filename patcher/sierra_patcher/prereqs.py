from __future__ import annotations

import re
import subprocess
import winreg
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable


@dataclass(frozen=True)
class DependencyRequirement:
    key: str
    label: str
    runtime_check: str
    download_url: str
    note: str = ""


@dataclass(frozen=True)
class DependencyStatus:
    requirement: DependencyRequirement
    installed: bool


_NETFX_472 = DependencyRequirement(
    key="netfx472",
    label=".NET Framework 4.7.2 or newer",
    runtime_check="Windows registry: .NET Framework v4 Full Release >= 461808",
    download_url="https://dotnet.microsoft.com/en-us/download/dotnet-framework/net472",
)

_DESKTOP = {
    5: DependencyRequirement(
        key="desktop5",
        label=".NET Desktop Runtime 5 x64",
        runtime_check="Microsoft.WindowsDesktop.App 5.",
        download_url="https://dotnet.microsoft.com/en-us/download/dotnet/5.0",
    ),
    6: DependencyRequirement(
        key="desktop6",
        label=".NET Desktop Runtime 6 x64",
        runtime_check="Microsoft.WindowsDesktop.App 6.",
        download_url="https://dotnet.microsoft.com/en-us/download/dotnet/6.0",
    ),
    8: DependencyRequirement(
        key="desktop8",
        label=".NET Desktop Runtime 8 x64",
        runtime_check="Microsoft.WindowsDesktop.App 8.",
        download_url="https://dotnet.microsoft.com/en-us/download/dotnet/8.0",
    ),
    9: DependencyRequirement(
        key="desktop9",
        label=".NET Desktop Runtime 9 x64",
        runtime_check="Microsoft.WindowsDesktop.App 9.",
        download_url="https://dotnet.microsoft.com/en-us/download/dotnet/9.0",
    ),
    10: DependencyRequirement(
        key="desktop10",
        label=".NET Desktop Runtime 10 x64",
        runtime_check="Microsoft.WindowsDesktop.App 10.",
        download_url="https://dotnet.microsoft.com/en-us/download/dotnet/10.0",
        note="SPT 4.1 requirement is provisional until stable release docs are available.",
    ),
}

_ASPNET = {
    9: DependencyRequirement(
        key="aspnet9",
        label="ASP.NET Core Runtime 9 x64",
        runtime_check="Microsoft.AspNetCore.App 9.",
        download_url="https://dotnet.microsoft.com/en-us/download/dotnet/9.0",
    ),
    10: DependencyRequirement(
        key="aspnet10",
        label="ASP.NET Core Runtime 10 x64",
        runtime_check="Microsoft.AspNetCore.App 10.",
        download_url="https://dotnet.microsoft.com/en-us/download/dotnet/10.0",
        note="SPT 4.1 requirement is provisional until stable release docs are available.",
    ),
}

_KNOWN_REQUIREMENTS = {
    "netfx472": _NETFX_472,
    **{req.key: req for req in _DESKTOP.values()},
    **{req.key: req for req in _ASPNET.values()},
}


def _parse_version(text: str | None) -> tuple[int, int, int] | None:
    if not text:
        return None
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not match:
        return None
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3) or 0),
    )


def _dedupe(requirements: Iterable[DependencyRequirement]) -> list[DependencyRequirement]:
    seen: set[str] = set()
    out: list[DependencyRequirement] = []
    for req in requirements:
        if req.key in seen:
            continue
        seen.add(req.key)
        out.append(req)
    return out


def infer_requirements_from_spt_version(version_text: str | None) -> list[DependencyRequirement]:
    version = _parse_version(version_text)
    if version is None:
        return []

    major, minor, patch = version
    reqs: list[DependencyRequirement] = [_NETFX_472]

    if major < 3 or (major == 2 and (minor, patch) <= (2, 1)):
        reqs.append(_DESKTOP[5])
    elif major == 2 or (major == 3 and minor <= 7):
        reqs.append(_DESKTOP[6])
    elif major == 3 and 8 <= minor <= 11:
        reqs.append(_DESKTOP[8])
    elif major == 4 and minor == 0:
        reqs.extend((_DESKTOP[9], _ASPNET[9]))
    elif major == 4 and minor >= 1:
        reqs.extend((_DESKTOP[10], _ASPNET[10]))

    return _dedupe(reqs)


def _requirements_from_tokens(tokens: Iterable[str]) -> list[DependencyRequirement]:
    reqs: list[DependencyRequirement] = []
    for raw in tokens:
        token = str(raw).strip().lower()
        if not token:
            continue
        token = token.replace(".", "").replace("-", "").replace("_", "")
        aliases = {
            "netfx472": "netfx472",
            "framework472": "netfx472",
            "dotnet5": "desktop5",
            "desktop5": "desktop5",
            "windowsdesktop5": "desktop5",
            "dotnet6": "desktop6",
            "desktop6": "desktop6",
            "windowsdesktop6": "desktop6",
            "dotnet8": "desktop8",
            "desktop8": "desktop8",
            "windowsdesktop8": "desktop8",
            "dotnet9": "desktop9",
            "desktop9": "desktop9",
            "windowsdesktop9": "desktop9",
            "aspnet9": "aspnet9",
            "aspnetcore9": "aspnet9",
            "dotnet10": "desktop10",
            "desktop10": "desktop10",
            "windowsdesktop10": "desktop10",
            "aspnet10": "aspnet10",
            "aspnetcore10": "aspnet10",
        }
        req = _KNOWN_REQUIREMENTS.get(aliases.get(token, token))
        if req:
            reqs.append(req)
    return _dedupe(reqs)


def requirements_for_metadata(meta) -> list[DependencyRequirement]:
    declared = getattr(meta, "dependencies", None)
    if isinstance(declared, list):
        from_declared = _requirements_from_tokens(
            item.get("key", "") if isinstance(item, dict) else item
            for item in declared
        )
        if from_declared:
            return from_declared
    elif isinstance(declared, str):
        from_declared = _requirements_from_tokens(re.split(r"[,;\s]+", declared))
        if from_declared:
            return from_declared

    return infer_requirements_from_spt_version(getattr(meta, "title", None))


def has_netfx472() -> bool:
    key = r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            release, _ = winreg.QueryValueEx(k, "Release")
            return int(release) >= 461808
    except Exception:
        return False


@lru_cache(maxsize=1)
def runtimes() -> tuple[str, ...]:
    try:
        out = subprocess.check_output(
            ["dotnet", "--list-runtimes"],
            text=True,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,
        )
        return tuple(out.splitlines())
    except Exception:
        return ()


def _has_runtime(check_string: str) -> bool:
    return any(check_string in line for line in runtimes())


def dependency_status(requirements: Iterable[DependencyRequirement]) -> list[DependencyStatus]:
    statuses: list[DependencyStatus] = []
    for req in _dedupe(requirements):
        if req.key == "netfx472":
            installed = has_netfx472()
        else:
            installed = _has_runtime(req.runtime_check)
        statuses.append(DependencyStatus(req, installed))
    return statuses


def missing_requirements_for_metadata(meta) -> list[DependencyRequirement]:
    return [status.requirement for status in dependency_status(requirements_for_metadata(meta)) if not status.installed]


def format_missing_requirements(requirements: Iterable[DependencyRequirement]) -> str:
    lines: list[str] = []
    for req in requirements:
        lines.append(f"{req.label}\n{req.download_url}")
        if req.note:
            lines.append(req.note)
    return "\n\n".join(lines)


def ensure_prereqs(meta=None, interactive: bool = True) -> list[DependencyRequirement]:
    """Compatibility wrapper: report missing requirements, never download or install."""

    if meta is None:
        requirements = _dedupe([_NETFX_472, _DESKTOP[5], _DESKTOP[6], _DESKTOP[8]])
    else:
        requirements = requirements_for_metadata(meta)

    missing = [status.requirement for status in dependency_status(requirements) if not status.installed]
    if not missing:
        print("All required .NET dependencies are present.")
        return []

    print("Missing .NET dependencies:")
    print(format_missing_requirements(missing))
    print("Automatic dependency installation has been removed. Please install from the official Microsoft links above.")
    return missing
