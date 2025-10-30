# sierra_patcher/proc.py
from __future__ import annotations
import os, sys, subprocess, threading, time

# Track live processes so we can kill them on Abort
_live: set[subprocess.Popen] = set()
_live_lock = threading.Lock()

# Windows flags to hide console windows
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0

class Cancelled(Exception):
    """Raised when a child process is cancelled by the user."""
    pass

def _startupinfo_windows():
    if os.name != "nt":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= STARTF_USESHOWWINDOW
    si.wShowWindow = SW_HIDE
    return si

def run_quiet(cmd: list[str],
              cwd: str | None = None,
              env: dict | None = None,
              check: bool = True,
              capture: bool = True,
              cancel_event: threading.Event | None = None,
              poll_interval: float = 0.1) -> subprocess.CompletedProcess:
    """
    Spawn a process with NO console window (Windows), capture output,
    and allow cooperative cancellation via cancel_event.
    """
    kwargs = dict(
        cwd=cwd, env=env, shell=False,
        stdin=subprocess.DEVNULL,
        startupinfo=_startupinfo_windows(),
        creationflags=(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0,
    )
    if capture:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    else:
        kwargs.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    p = subprocess.Popen(cmd, **kwargs)
    with _live_lock:
        _live.add(p)
    try:
        if cancel_event is None:
            out, err = p.communicate()
            if check and p.returncode != 0:
                raise subprocess.CalledProcessError(p.returncode, cmd, out, err)
            return subprocess.CompletedProcess(cmd, p.returncode, out, err)

        # Cooperative cancel loop
        out_chunks: list[str] = []
        err_chunks: list[str] = []
        while True:
            if cancel_event.is_set():
                # terminate quietly
                try:
                    if os.name == "nt":
                        p.terminate()
                    else:
                        p.terminate()
                except Exception:
                    pass
                # Give it a moment, then kill if needed
                try:
                    p.wait(timeout=0.5)
                except Exception:
                    try: p.kill()
                    except Exception: pass
                raise Cancelled()
            try:
                rc = p.wait(timeout=poll_interval)
                # done → drain pipes
                if capture:
                    try:
                        o, e = p.communicate(timeout=0.0)
                    except Exception:
                        o = e = ""
                    out_chunks.append(o or "")
                    err_chunks.append(e or "")
                if check and rc != 0:
                    if cancel_event is not None and cancel_event.is_set():
                        raise Cancelled()
                    raise subprocess.CalledProcessError(rc, cmd, "".join(out_chunks), "".join(err_chunks))
                return subprocess.CompletedProcess(cmd, rc, "".join(out_chunks), "".join(err_chunks))
            except subprocess.TimeoutExpired:
                # poll and non-blocking read (best-effort)
                if capture and p.stdout and not p.stdout.closed:
                    try: out_chunks.append(p.stdout.read())  # non-blocking on Windows pipes is limited; best effort
                    except Exception: pass
                if capture and p.stderr and not p.stderr.closed:
                    try: err_chunks.append(p.stderr.read())
                    except Exception: pass
                continue
    finally:
        with _live_lock:
            _live.discard(p)

def kill_all():
    """Force-kill all tracked child processes (used by Abort)."""
    with _live_lock:
        procs = list(_live)
    for p in procs:
        try:
            if p.poll() is None:
                if os.name == "nt":
                    p.terminate()
                else:
                    p.terminate()
                time.sleep(0.2)
                if p.poll() is None:
                    p.kill()
        except Exception:
            pass
    with _live_lock:
        _live.clear()
