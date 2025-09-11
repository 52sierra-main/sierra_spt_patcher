from __future__ import annotations




def apply_storage(target_dir: Path, patch_dir: Path) -> bool:
arc = patch_dir / "storage.sierra"
if not arc.exists():
return True
# If you add a password, pass it here
return unpack_storage(arc, target_dir, password=None)




def finalize(target_dir: Path) -> None:
removed = rm_empty_dirs(target_dir)
if removed:
PRINT(f"Removed {len(removed)} empty folder(s).")




def main(argv: List[str] | None = None) -> int:
ap = argparse.ArgumentParser(description="Sierra's SPT Installer")
ap.add_argument("target", help="Target EFT folder (to be patched)")
ap.add_argument("patchdir", help="Patch folder containing *.zst/metadata.json")
ap.add_argument("--workers", type=int, default=0, help="Parallel workers (0=auto)")
ap.add_argument("--force", action="store_true", help="Skip version check")
args = ap.parse_args(argv)


target = Path(args.target).resolve()
patch_dir = find_patch_dir(Path(args.patchdir).resolve())


# Load metadata (JSON preferred; legacy .info supported)
meta = Metadata.load_any(patch_dir)


# Immediate fix: ensure prereqs in all flows
ensure_prereqs(interactive=True)


# Version check UX (expand when you wire detect_eft_version)
# detected = detect_eft_version(target / "EscapeFromTarkov.exe")
# if meta.game_version and detected and not args.force and meta.game_version != detected:
# PRINT(f"Version mismatch. Expected {meta.game_version}, detected {detected}. Use --force to override.")
# return 3


PRINT("Applying binary patches…")
ok = process_patches(target, patch_dir, workers=args.workers)
if not ok:
PRINT("Some patches failed. Fix issues and retry.")
return 2


PRINT("Applying delete list…")
apply_delete_list(target, patch_dir)


PRINT("Applying storage (extra files)…")
if not apply_storage(target, patch_dir):
PRINT("Failed to extract storage.sierra")
return 4


finalize(target)
PRINT("All done! ✅")
return 0




if __name__ == "__main__":
raise SystemExit(main())
