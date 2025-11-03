import os, shutil, random, string, subprocess, re
from pathlib import Path
from .paths import SEVENZIP
from .proc import run_quiet

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

# --- 7-Zip progress parsing ---
_PERCENT = re.compile(r"(\d{1,3})%")

def _on_7z_output_factory(on_progress):
    buf = {"s": "", "last": -1}
    def _cb(chunk: str):
        if not on_progress or not chunk:
            return
        # normalize carriage returns and bound buffer
        s = chunk.replace("\r", "\n")
        buf["s"] += s
        if len(buf["s"]) > 4096:
            buf["s"] = buf["s"][-2048:]
        m = None
        for m in _PERCENT.finditer(buf["s"]):
            pass
        if not m:
            return
        p = max(0, min(100, int(m.group(1))))
        if p != buf["last"]:
            buf["last"] = p
            on_progress("7z", p, 100, f"7-Zip: {p}%")
    return _cb

# pack additional_files → storage.sierra (AES-256)
def pack_additional(additional_dir: str | Path, storage_dir: str | Path,
                    cancel_event=None, on_progress=None) -> None:
    additional_dir = Path(additional_dir)
    if not additional_dir.is_dir():
        return
    storage_dir = Path(storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    archive = storage_dir / "storage.sierra"
    pw = os.environ.get("AF_PASS") or _gen_pass()
    run_quiet(
        [SEVENZIP, "a", "-t7z", str(archive), str(additional_dir / "*"),
         "-mx9", "-mhe=on", "-mmt=on", "-bsp2", f"-p{pw}"],
        check=True, capture=True, cancel_event=cancel_event,
        on_output=_on_7z_output_factory(on_progress)
    )
    shutil.rmtree(additional_dir)
    _stash_password(pw, storage_dir)

# unpack into dest
def apply_storage(storage_dir: str | Path, dest_dir: str | Path,
                  cancel_event=None, on_progress=None) -> None:
    archive = Path(storage_dir) / "storage.sierra"
    if not archive.exists():
        print("No storage.sierra found – skipping.")
        return
    pw = recover_password(storage_dir)
    run_quiet(
        [SEVENZIP, "x", "-y", f"-o{dest_dir}", f"-p{pw}", str(archive), "-bsp2"],
        check=True, capture=True, cancel_event=cancel_event,
        on_output=_on_7z_output_factory(on_progress)
    )
    print("storage applied.")
