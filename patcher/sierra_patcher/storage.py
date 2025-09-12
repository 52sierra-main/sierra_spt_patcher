import os, shutil, random, string, subprocess
from pathlib import Path
from .paths import SEVENZIP


_KEY_NAME = ".af.key"


def _gen_pass(n: int = 24) -> str:
alphabet = string.ascii_letters + string.digits
return "".join(random.choice(alphabet) for _ in range(n))


def _stash_password(pw: str, storage_dir: str | Path) -> None:
blob = bytes(b ^ 0x5A for b in pw.encode())
Path(storage_dir, _KEY_NAME).write_bytes(blob)


def recover_password(storage_dir: str | Path) -> str:
blob = Path(storage_dir, _KEY_NAME).read_bytes()
return bytes(b ^ 0x5A for b in blob).decode()


# pack additional_files → storage.sierra (AES-256)


def pack_additional(additional_dir: str | Path, storage_dir: str | Path) -> None:
additional_dir = Path(additional_dir)
if not additional_dir.is_dir():
return
storage_dir = Path(storage_dir)
storage_dir.mkdir(parents=True, exist_ok=True)
archive = storage_dir / "storage.sierra"
pw = os.environ.get("AF_PASS") or _gen_pass()
subprocess.check_call([SEVENZIP, "a", "-t7z", str(archive), str(additional_dir / "*"),
"-mx9", "-mhe=on", "-mnt=on", f"-p{pw}"])
shutil.rmtree(additional_dir)
_stash_password(pw, storage_dir)


# unpack into dest


def apply_storage(storage_dir: str | Path, dest_dir: str | Path) -> None:
archive = Path(storage_dir) / "storage.sierra"
if not archive.exists():
print("No storage.sierra found – skipping.")
return
pw = recover_password(storage_dir)
subprocess.check_call([SEVENZIP, "x", "-y", f"-o{dest_dir}", f"-p{pw}", str(archive)])
print("storage applied.")
