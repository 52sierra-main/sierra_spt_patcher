import os, argparse
meta = Meta.read(STORAGE_DIR)
print("Patcher metadata:")
print(" Version ", meta.version)
print(" Release ", meta.title)
print(" Description ", meta.description)


inst = query_install()
if not inst:
raise SystemExit("Tarkov installation not found in registry.")
print("Tarkov install:")
print(" Path ", inst.install_path)
print(" Version ", inst.version)
print(" Publisher", inst.publisher)


# Hard guard (can be relaxed with --force)
if not args.force:
exe = Path(inst.install_path, "EscapeFromTarkov.exe")
if exe_version(str(exe)) != meta.version:
raise SystemExit("Client version mismatch vs metadata. Use --force to override.")
if inst.publisher != "Battlestate Games":
raise SystemExit("Publisher mismatch. Aborting.")


dest = args.dir or choose_directory("Select the copy‑pasted Tarkov client folder")


check_resources()
threads = args.threads or optimal_threads()


print("Applying patches...")
ok = apply_all_patches(dest, workers=threads)
print("Finalizing...")
finalize(dest, _DEF_DELETE_LIST)
apply_storage(STORAGE_DIR, dest)


if args.prereqs:
ensure_prereqs(interactive=not args.yes)


print("Done. Have fun!")




def build_parser() -> argparse.ArgumentParser:
p = argparse.ArgumentParser(prog="sierra-patcher", description="Sierra's unified patch generator + installer")
sub = p.add_subparsers(dest="cmd", required=True)


g = sub.add_parser("generate", help="Create a patch package from dest vs source")
g.add_argument("--source", type=str, help="Clean game folder")
g.add_argument("--dest", type=str, help="SPT target folder")
g.add_argument("--threads", type=int, help="Worker threads")
g.add_argument("--title", type=str, help="Target release title (e.g., SPT 3.10)")
g.add_argument("--date", type=str, help="Date string to stamp")
g.set_defaults(func=_cmd_generate)


i = sub.add_parser("install", help="Apply an existing patch package to a chosen folder")
i.add_argument("--dir", type=str, help="Destination game folder to patch")
i.add_argument("--threads", type=int, help="Worker threads")
i.add_argument("--force", action="store_true", help="Bypass metadata checks")
i.add_argument("--prereqs", action="store_true", help="Ensure .NET prerequisites")
i.add_argument("-y", "--yes", action="store_true", help="Assume yes for prompts")
i.set_defaults(func=_cmd_install)


return p




def main(argv: list[str] | None = None) -> None:
parser = build_parser()
args = parser.parse_args(argv)
args.func(args)
