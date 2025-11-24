from pathlib import Path
from .registry import exe_version
import json

INFO_FIELDS = ("version", "title", "description", "dependencies")


class Meta:
    def __init__(
        self,
        version: str,
        title: str,
        description: str,
        dependencies: str | None = None,
        integrity_folders: dict[str, int] | None = None,
    ):
        self.version = version
        self.title = title
        self.description = description
        self.dependencies = dependencies
        self.integrity_folders: dict[str, int] = integrity_folders or {}

    @staticmethod
    def read(info_dir: str | Path) -> "Meta":
        """Read metadata.info (JSON if possible, fall back to legacy 3-line text)."""
        info_file = next(Path(info_dir).glob("*.info"), None)
        if not info_file:
            raise FileNotFoundError("Metadata .info file not found")

        raw = info_file.read_text(encoding="utf-8")
        text = raw.lstrip()

        # New JSON format
        if text.startswith("{"):
            data = json.loads(raw)
            return Meta(
                version=data.get("version", ""),
                title=data.get("title", ""),
                description=data.get("description", ""),
                dependencies=data.get("dependencies"),
                integrity_folders=data.get("integrity_folders", {}) or {},
            )

        # Legacy 3-line format: version, title, description, [dependencies?]
        lines = raw.splitlines()
        while len(lines) < 4:
            lines.append("")
        return Meta(
            lines[0].strip(),
            lines[1].strip(),
            lines[2].strip(),
            lines[3].strip() or None,
            integrity_folders={},
        )

    @staticmethod
    def write(
        info_path: str | Path,
        version: str,
        title: str,
        date_str: str,
        dependencies: str | None = None,
        integrity_folders: dict[str, int] | None = None,
    ) -> None:
        """Write JSON metadata (new format)."""
        p = Path(info_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": version,
            "title": title,
            "description": date_str,
            "dependencies": dependencies,
            "integrity_folders": integrity_folders or {},
        }
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")


# convenience for generator
def stamp_from_game_exe(
    info_path: str | Path,
    source_dir: str | Path,
    target_title: str,
    date_str: str,
    integrity_folders: dict[str, int] | None = None,
) -> None:
    v = exe_version(str(Path(source_dir) / "EscapeFromTarkov.exe")) or "0.0.0.0"
    Meta.write(info_path, v, target_title, date_str, integrity_folders=integrity_folders)
