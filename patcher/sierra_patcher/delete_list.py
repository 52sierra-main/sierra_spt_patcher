import os
from pathlib import Path

from .hygiene import is_package_excluded


# generator: list source files absent in dest -> delete_list.txt
def build_delete_list(source_root: str, dest_root: str, out_path: str) -> None:
    items = []
    for root, _, files in os.walk(source_root):
        for name in files:
            source_file = os.path.join(root, name)
            if is_package_excluded(source_file, source_root):
                continue
            rel = os.path.relpath(source_file, source_root)
            if not os.path.exists(os.path.join(dest_root, rel)):
                items.append(rel)
    Path(out_path).write_text("\n".join(items) + "\n", encoding="utf-8")


# installer: remove listed files and empty dirs
def finalize(dest_dir: str, delete_list_path: str) -> None:
    path = Path(delete_list_path)
    if not path.exists():
        print(f"delete list not found: {path}")
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        file_path = Path(dest_dir, line.strip())
        try:
            if file_path.exists():
                file_path.unlink()
                print("deleted:", file_path)
        except Exception as exc:
            print("failed to delete", file_path, exc)

    for root, dirs, _files in os.walk(dest_dir, topdown=False):
        for directory in dirs:
            dir_path = Path(root, directory)
            try:
                if not any(dir_path.iterdir()):
                    dir_path.rmdir()
                    print("removed empty:", dir_path)
            except Exception:
                pass
