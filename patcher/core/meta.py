from __future__ import annotations
game_version: str | None
release_title: str | None
description: str | None
dependencies: list[str]
patch_format: str
built_with: dict
created_utc: str


@staticmethod
def from_legacy_info(path: Path) -> "Metadata":
"""Bridge from your old 3-line .info text file.
Expected: line0=game_version, line1=release_title, line2=description
Missing lines are treated as None.
"""
game_version = release_title = description = None
try:
lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
game_version = lines[0].strip() if len(lines) > 0 else None
release_title = lines[1].strip() if len(lines) > 1 else None
description = lines[2].strip() if len(lines) > 2 else None
except FileNotFoundError:
pass
return Metadata(
game_version=game_version,
release_title=release_title,
description=description,
dependencies=["dotnet472", "dotnet5desktop", "dotnet6desktop", "dotnet8desktop"],
patch_format="zstd-patch-v1",
built_with={},
created_utc="",
)


@staticmethod
def load_any(base_dir: Path) -> "Metadata":
json_path = base_dir / "metadata.json"
if json_path.exists():
data = json.loads(json_path.read_text(encoding="utf-8", errors="ignore"))
return Metadata(**data)
# Fallback to legacy .info
info_path = base_dir / "patch.info"
return Metadata.from_legacy_info(info_path)


def save_json(self, base_dir: Path) -> None:
(base_dir / "metadata.json").write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
