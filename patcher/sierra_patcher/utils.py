import os, re, shutil, sys
from pathlib import Path
from .registry import exe_version  # already in your codebase
import webbrowser
from tkinter import messagebox

def _last_digit_from_version(ver: str | None) -> str:
    """Extract the last numeric digit from a version string; fallback 'x'."""
    if not ver:
        return "x"
    digits = re.findall(r"\d", ver)
    return digits[-1] if digits else "x"

def rename_output_folder(output_dir: str, spt_version: str, live_client_exe: str, log) -> str | None:
    """
    Rename output_dir to '<spt_version>_<lastdigit>_'. Returns new path or None on failure.
    """
    try:
        last = _last_digit_from_version(exe_version(live_client_exe))
        # sanitize spt_version for folder name
        safe_spt = re.sub(r"[^A-Za-z0-9._-]+", "-", (spt_version or "spt")).strip("-")
        base = f"{safe_spt}_{last}_"
        parent = str(Path(output_dir).parent)
        target = os.path.join(parent, base)

        # avoid collisions: add -1, -2, …
        cand = target
        i = 1
        while os.path.exists(cand):
            cand = f"{target.rstrip(os.sep)}-{i}"
            i += 1

        os.replace(output_dir, cand)   # atomic on same volume
        log(f"[generate] renamed output → {cand}")
        return cand
    except Exception as e:
        log(f"[generate] WARN: failed to rename output → {e}")
        return None

def copy_self_to_output(output_dir: str, log):
        """If running as a frozen EXE, copy the current executable into output_dir."""
        try:
            if not getattr(sys, "frozen", False):
                log("[generate] skipping EXE copy (dev/script run)")
                return
            src = sys.executable
            dst = os.path.join(output_dir, os.path.basename(src))
            if os.path.abspath(src) == os.path.abspath(dst):
                log("[generate] EXE already in output")
                return
            shutil.copy2(src, dst)
            log(f"[generate] copied EXE → {dst}")
        except Exception as e:
            # non-fatal: don't fail the whole run if copy fails
            log(f"[generate] WARN: failed to copy EXE → {e}")

def open_url(url: str):
    try:
        webbrowser.open(url, new=2)
    except Exception as e:
        messagebox.showerror("Open link", f"Failed to open link:\n{e}")

def copy_to_clipboard(root, text: str, toast: bool = True):
    try:
        root.clipboard_clear()
        root.clipboard_append(text)
        if toast:
            messagebox.showinfo("Copied", "Copied to clipboard.")
    except Exception:
        pass
    