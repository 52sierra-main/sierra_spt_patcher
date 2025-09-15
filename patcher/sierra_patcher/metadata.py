from pathlib import Path
from .registry import exe_version

INFO_FIELDS = ("version", "title", "description", "dependencies")

class Meta:
    def __init__(self, version: str, title: str, description: str, dependencies: str | None = None):
        self.version = version
        self.title = title
        self.description = description
        self.dependencies = dependencies

    @staticmethod
    def read(info_dir: str | Path) -> "Meta":
        info_file = next(Path(info_dir).glob("*.info"), None)
        if not info_file:
            raise FileNotFoundError("Metadata .info file not found")
        lines = info_file.read_text(encoding="utf-8").splitlines()
        # pad to 4 lines
        while len(lines) < 4: lines.append("")
        return Meta(lines[0].strip(), lines[1].strip(), lines[2].strip(), lines[3].strip() or None)

    @staticmethod
    def write(info_path: str | Path, version: str, title: str, date_str: str) -> None:
        p = Path(info_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        content = f"{version}\n{title}\n{date_str}\n"
        p.write_text(content, encoding="utf-8")

# convenience for generator

def stamp_from_game_exe(info_path: str | Path, source_dir: str | Path, target_title: str, date_str: str) -> None:
    v = exe_version(str(Path(source_dir) / "EscapeFromTarkov.exe")) or "0.0.0.0"
    Meta.write(info_path, v, target_title, date_str)
