from __future__ import annotations

import re
import subprocess
import winreg
from dataclasses import dataclass
from typing import Iterable

from .i18n import tr


@dataclass(frozen=True)
class DependencyRequirement:
    key: str
    label: str
    runtime_check: str
    download_url: str
    note: str = ""
    framework_name: str = ""
    minimum_version: str = ""
    sources: tuple[str, ...] = ()


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


def _parse_version(text: str | None) -> tuple[int, int, int] | None:
    if not text:
        return None
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(text))
    if not match:
        return None
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3) or 0),
    )


def _download_url_for_version(version: str) -> str:
    parsed = _parse_version(version)
    if parsed is None:
        return "https://dotnet.microsoft.com/en-us/download/dotnet"
    major, minor, _patch = parsed
    return f"https://dotnet.microsoft.com/en-us/download/dotnet/{major}.{minor}"


def _framework_friendly_name(framework: str) -> str:
    names = {
        "Microsoft.NETCore.App": ".NET Runtime",
        "Microsoft.AspNetCore.App": "ASP.NET Core Runtime",
        "Microsoft.WindowsDesktop.App": ".NET Desktop Runtime",
    }
    return names.get(framework, framework)


def _framework_label(framework: str, version: str) -> str:
    parsed = _parse_version(version)
    train = f"{parsed[0]}.{parsed[1]}" if parsed else version
    return f"{_framework_friendly_name(framework)} {train} x64"


def _framework_requirement(
    framework: str,
    version: str,
    *,
    note: str = "",
    sources: Iterable[str] = (),
) -> DependencyRequirement:
    parsed = _parse_version(version)
    if parsed is None:
        raise ValueError(f"Invalid .NET runtime version: {version!r}")
    major, minor, patch = parsed
    train = f"{major}.{minor}"
    return DependencyRequirement(
        key=f"runtime:{framework}:{train}",
        label=_framework_label(framework, version),
        runtime_check=f"{framework} >= {major}.{minor}.{patch} within {train}.x",
        download_url=_download_url_for_version(version),
        note=note,
        framework_name=framework,
        minimum_version=f"{major}.{minor}.{patch}",
        sources=tuple(str(source) for source in sources if str(source).strip()),
    )


_DESKTOP = {
    major: _framework_requirement("Microsoft.WindowsDesktop.App", f"{major}.0.0")
    for major in (5, 6, 8, 9, 10)
}

_ASPNET = {
    major: _framework_requirement("Microsoft.AspNetCore.App", f"{major}.0.0")
    for major in (9, 10)
}

_KNOWN_REQUIREMENTS = {
    "netfx472": _NETFX_472,
    **{f"desktop{major}": req for major, req in _DESKTOP.items()},
    **{f"aspnet{major}": req for major, req in _ASPNET.items()},
}


def _dedupe(requirements: Iterable[DependencyRequirement]) -> list[DependencyRequirement]:
    selected: dict[str, DependencyRequirement] = {}
    order: list[str] = []
    for req in requirements:
        existing = selected.get(req.key)
        if existing is None:
            selected[req.key] = req
            order.append(req.key)
            continue

        old_version = _parse_version(existing.minimum_version)
        new_version = _parse_version(req.minimum_version)
        if old_version is not None and new_version is not None and new_version > old_version:
            selected[req.key] = req
    return [selected[key] for key in order]


def infer_requirements_from_spt_version(version_text: str | None) -> list[DependencyRequirement]:
    """Legacy fallback for packages that predate runtimeconfig-derived metadata."""

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


def _requirements_from_runtime_metadata(items) -> list[DependencyRequirement]:
    if not isinstance(items, list) or not items:
        return []

    reqs: list[DependencyRequirement] = []
    for item in items:
        if not isinstance(item, dict):
            raise RuntimeError("runtime_requirements contains an invalid entry")
        framework = str(item.get("framework", "")).strip()
        version = str(item.get("version", "")).strip()
        if not framework or _parse_version(version) is None:
            raise RuntimeError("runtime_requirements contains an invalid framework/version")
        sources = item.get("sources")
        source_names = (
            tuple(str(source) for source in sources[:3])
            if isinstance(sources, list)
            else ()
        )
        reqs.append(
            _framework_requirement(
                framework,
                version,
                sources=source_names,
            )
        )
    return _dedupe(reqs)


def requirements_for_metadata(meta) -> list[DependencyRequirement]:
    # Current packages carry exact framework family + servicing baseline derived
    # from the target SPT runtimeconfig files. Prefer those over all heuristics.
    runtime_requirements = _requirements_from_runtime_metadata(
        getattr(meta, "runtime_requirements", None)
    )
    if runtime_requirements:
        return runtime_requirements

    # Explicit legacy dependency declarations remain supported.
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

    # Old Sierra packages have neither field, so preserve their title-based map.
    return infer_requirements_from_spt_version(getattr(meta, "title", None))


def has_netfx472() -> bool:
    key = r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            release, _ = winreg.QueryValueEx(k, "Release")
            return int(release) >= 461808
    except Exception:
        return False


def runtimes() -> tuple[str, ...]:
    """Return a fresh runtime inventory for every prerequisite check."""

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


def _installed_runtime_versions(lines: Iterable[str]) -> dict[str, list[tuple[int, int, int]]]:
    inventory: dict[str, list[tuple[int, int, int]]] = {}
    pattern = re.compile(r"^\s*(\S+)\s+(\d+\.\d+(?:\.\d+)?(?:-[^\s]+)?)\s+\[")
    for line in lines:
        match = pattern.match(line)
        if not match:
            continue
        framework = match.group(1)
        version_text = match.group(2)
        # A stable requirement should not be considered satisfied only by a
        # prerelease runtime. SPT release packages use stable runtime baselines.
        if "-" in version_text:
            continue
        version = _parse_version(version_text)
        if version is not None:
            inventory.setdefault(framework, []).append(version)
    return inventory


def _has_runtime_requirement(
    requirement: DependencyRequirement,
    inventory: dict[str, list[tuple[int, int, int]]],
) -> bool:
    required = _parse_version(requirement.minimum_version)
    if not requirement.framework_name or required is None:
        return any(requirement.runtime_check in line for line in runtimes())

    required_major, required_minor, required_patch = required
    for installed in inventory.get(requirement.framework_name, []):
        major, minor, patch = installed
        if major == required_major and minor == required_minor and patch >= required_patch:
            return True
    return False


def dependency_status(requirements: Iterable[DependencyRequirement]) -> list[DependencyStatus]:
    requirements = _dedupe(requirements)
    runtime_lines = runtimes()
    inventory = _installed_runtime_versions(runtime_lines)

    statuses: list[DependencyStatus] = []
    for req in requirements:
        if req.key == "netfx472":
            installed = has_netfx472()
        elif req.framework_name:
            installed = _has_runtime_requirement(req, inventory)
        else:
            installed = any(req.runtime_check in line for line in runtime_lines)
        statuses.append(DependencyStatus(req, installed))
    return statuses


def missing_requirements_for_metadata(meta) -> list[DependencyRequirement]:
    return [
        status.requirement
        for status in dependency_status(requirements_for_metadata(meta))
        if not status.installed
    ]


def _localized_requirement_label(req: DependencyRequirement) -> str:
    parsed = _parse_version(req.minimum_version)
    if not req.framework_name or parsed is None:
        return tr(req.label)

    train = f"{parsed[0]}.{parsed[1]}"
    return tr(
        "{runtime_name} {train} x64",
        runtime_name=tr(_framework_friendly_name(req.framework_name)),
        train=train,
    )


def _localized_requirement_note(req: DependencyRequirement) -> str:
    parts: list[str] = []
    parsed = _parse_version(req.minimum_version)
    if req.framework_name and parsed is not None:
        major, minor, patch = parsed
        parts.append(
            tr(
                "Requires {framework} {version} or a newer patch within the {train} runtime train.",
                framework=req.framework_name,
                version=f"{major}.{minor}.{patch}",
                train=f"{major}.{minor}",
            )
        )
        if req.sources:
            parts.append(
                tr(
                    "Declared by {sources}.",
                    sources=", ".join(req.sources[:3]),
                )
            )
    if req.note:
        parts.append(tr(req.note))
    return " ".join(part for part in parts if part)


def format_missing_requirements(requirements: Iterable[DependencyRequirement]) -> str:
    lines: list[str] = []
    for req in requirements:
        lines.append(f"{_localized_requirement_label(req)}\n{req.download_url}")
        note = _localized_requirement_note(req)
        if note:
            lines.append(note)
    return "\n\n".join(lines)


def ensure_prereqs(meta=None, interactive: bool = True) -> list[DependencyRequirement]:
    """Compatibility wrapper: report missing requirements, never download or install."""

    del interactive
    if meta is None:
        requirements = _dedupe([_NETFX_472, _DESKTOP[5], _DESKTOP[6], _DESKTOP[8]])
    else:
        requirements = requirements_for_metadata(meta)

    missing = [
        status.requirement
        for status in dependency_status(requirements)
        if not status.installed
    ]
    if not missing:
        print("All required .NET dependencies are present.")
        return []

    print("Missing .NET dependencies:")
    print(format_missing_requirements(missing))
    print(
        "Automatic dependency installation has been removed. "
        "Please install from the official Microsoft links above."
    )
    return missing
