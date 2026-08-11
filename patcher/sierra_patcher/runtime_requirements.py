from __future__ import annotations

import json
import os
from pathlib import Path


RUNTIME_REQUIREMENTS_FILENAME = "runtime_requirements.json"
RUNTIME_REQUIREMENTS_FORMAT_VERSION = 1


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    text = str(value or "").strip()
    core = text.split("-", 1)[0]
    parts = core.split(".")
    if len(parts) < 2 or len(parts) > 3:
        return None
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return None
    if len(numbers) == 2:
        numbers.append(0)
    return numbers[0], numbers[1], numbers[2]


def _runtimeconfig_candidates(target_root: Path) -> list[Path]:
    """Prefer SPT's own runtimeconfig files over unrelated bundled tools/mods."""

    configs = sorted(
        (path for path in target_root.rglob("*.runtimeconfig.json") if path.is_file()),
        key=lambda path: path.as_posix().lower(),
    )
    if not configs:
        return []

    spt_configs = [path for path in configs if path.name.lower().startswith("spt.")]
    if spt_configs:
        return spt_configs

    # Fallback for older layouts/names: limit discovery to files close to the
    # package root so a nested mod/tool runtimeconfig cannot unexpectedly add a
    # prerequisite to the whole Sierra release.
    shallow: list[Path] = []
    for path in configs:
        try:
            relative = path.relative_to(target_root)
        except ValueError:
            continue
        if len(relative.parts) <= 2:
            shallow.append(path)
    return shallow


def _framework_entries(runtimeconfig: Path) -> list[tuple[str, str]]:
    try:
        data = json.loads(runtimeconfig.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise RuntimeError(f"could not read .NET runtime config: {runtimeconfig}") from exc

    options = data.get("runtimeOptions")
    if not isinstance(options, dict):
        return []

    raw_entries: list[dict] = []
    single = options.get("framework")
    if isinstance(single, dict):
        raw_entries.append(single)
    multiple = options.get("frameworks")
    if isinstance(multiple, list):
        raw_entries.extend(item for item in multiple if isinstance(item, dict))

    entries: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_entries:
        name = str(item.get("name", "")).strip()
        version = str(item.get("version", "")).strip()
        if not name or not version:
            continue
        if _version_tuple(version) is None:
            raise RuntimeError(
                f"unsupported .NET framework version {version!r} in {runtimeconfig.name}"
            )
        key = (name, version)
        if key not in seen:
            seen.add(key)
            entries.append(key)
    return entries


def discover_runtime_requirements(target_root: str | Path) -> list[dict]:
    """Discover exact external .NET framework requirements from SPT runtimeconfigs.

    Requirements are deduplicated per framework major/minor train. If multiple
    SPT applications require the same train, the highest requested patch is
    retained because it is the minimum servicing level that satisfies all of
    them conservatively.
    """

    root = Path(target_root).resolve()
    requirements: dict[tuple[str, int, int], dict] = {}

    for config in _runtimeconfig_candidates(root):
        try:
            source_name = config.relative_to(root).as_posix()
        except ValueError:
            source_name = config.name

        for framework, version in _framework_entries(config):
            parsed = _version_tuple(version)
            if parsed is None:
                continue
            major, minor, patch = parsed
            key = (framework, major, minor)
            current = requirements.get(key)
            if current is None:
                requirements[key] = {
                    "framework": framework,
                    "version": version,
                    "sources": [source_name],
                }
                continue

            current_version = _version_tuple(str(current.get("version", ""))) or (0, 0, 0)
            if patch > current_version[2]:
                current["version"] = version
            sources = current.setdefault("sources", [])
            if source_name not in sources:
                sources.append(source_name)

    result = list(requirements.values())
    for item in result:
        item["sources"] = sorted(str(source) for source in item.get("sources", []))
    result.sort(
        key=lambda item: (
            str(item.get("framework", "")).lower(),
            _version_tuple(str(item.get("version", ""))) or (0, 0, 0),
        )
    )
    return result


def write_runtime_requirements_manifest(
    target_root: str | Path,
    storage_root: str | Path,
) -> Path:
    requirements = discover_runtime_requirements(target_root)
    storage = Path(storage_root)
    storage.mkdir(parents=True, exist_ok=True)
    output = storage / RUNTIME_REQUIREMENTS_FILENAME
    temp = output.with_name(output.name + ".tmp")
    payload = {
        "format_version": RUNTIME_REQUIREMENTS_FORMAT_VERSION,
        "requirements": requirements,
    }
    try:
        temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, output)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
    return output


def load_runtime_requirements_manifest(storage_root: str | Path) -> list[dict] | None:
    path = Path(storage_root) / RUNTIME_REQUIREMENTS_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("runtime requirements manifest is not valid JSON") from exc
    if data.get("format_version") != RUNTIME_REQUIREMENTS_FORMAT_VERSION:
        raise RuntimeError(
            f"unsupported runtime requirements format: {data.get('format_version')!r}"
        )
    requirements = data.get("requirements")
    if not isinstance(requirements, list):
        raise RuntimeError("runtime requirements manifest must contain a requirements list")

    normalized: list[dict] = []
    for item in requirements:
        if not isinstance(item, dict):
            raise RuntimeError("runtime requirements manifest contains an invalid entry")
        framework = str(item.get("framework", "")).strip()
        version = str(item.get("version", "")).strip()
        if not framework or _version_tuple(version) is None:
            raise RuntimeError("runtime requirements manifest contains an invalid framework/version")
        sources = item.get("sources", [])
        if not isinstance(sources, list):
            sources = []
        normalized.append(
            {
                "framework": framework,
                "version": version,
                "sources": [str(source) for source in sources if str(source).strip()],
            }
        )
    return normalized
