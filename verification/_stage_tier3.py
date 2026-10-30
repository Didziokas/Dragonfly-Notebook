r"""The tier-3 staging harness (the permanent pre-install staging seam,
introduced in round 8): stages the reviewed sources as a fake install
under a temp root laid out exactly like an install
(<root>/pythonUserExtensions/Plugins/DragonflyNotebook, __pycache__
excluded -- Install.ps1 removes it), then runs the REAL tier-3 suite
with DFNB_INSTALL_ROOT pointed at it -- every pin, the offscreen spawn
test and the kernel smoke, against the bytes under review. The real
%LOCALAPPDATA% trees are never touched (install=false -- the user runs
Install.ps1 after review, then re-runs the suite without the override).
"""

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile

# Round 13: a console-subsystem gate python spawned through a piped chain
# (bash -> uv -> this interpreter) still gets a console window allocated
# on the user's desktop for as long as the gate runs. Hide it -- but ONLY
# when this process is the console's sole occupant (the console was
# allocated for it): a console shared with a shell is a human's terminal
# (their `python gate.py > log.txt` keeps its window), and a process
# spawned with CREATE_NO_WINDOW/DETACHED has no console at all and is
# left alone (the count comes back 0 there).
if sys.platform == "win32":
    try:
        _pids = (ctypes.c_uint32 * 32)()
        _pids[0] = 32
        if ctypes.windll.kernel32.GetConsoleProcessList(_pids, 32) == 1:
            _console = ctypes.windll.kernel32.GetConsoleWindow()
            if _console:
                ctypes.windll.user32.ShowWindow(_console, 0)  # SW_HIDE
    except Exception:
        pass  # a hide failure must never break a gate

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# uv resolved from PATH (the harness runs wherever uv is installed -- a
# published checkout must not carry a per-machine uv path); a missing uv
# fails loudly here instead of inside the subprocess.
_UV = shutil.which("uv")
if _UV is None:
    raise SystemExit("uv was not found on PATH -- install uv or add it "
                     "to PATH before running the staged tier-3 gate")
_PY = r"C:\Program Files\Dragonfly\Python_env\python.exe"
# The gate's own child pythons are console-subsystem binaries; without
# CREATE_NO_WINDOW every gate run puts a printing console window on the
# user's desktop for as long as the suite runs (round 13; the production
# app spawn is already DETACHED_PROCESS). 0 where the flag is missing.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def main():
    root = tempfile.mkdtemp(prefix="dfnb_stage_")
    dest = os.path.join(root, "pythonUserExtensions", "Plugins",
                        "DragonflyNotebook")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copytree(os.path.join(_ROOT, "DragonflyNotebook"), dest,
                    ignore=shutil.ignore_patterns("__pycache__"))
    print("staged:", dest, flush=True)
    env = dict(os.environ)
    env["DFNB_INSTALL_ROOT"] = root
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            [_UV, "run", "--no-project", "--python", _PY,
             "verification/dfpy_ors.py",
             "verification/test_dragonflynotebook_installed.py"],
            cwd=_ROOT, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=1500,
            creationflags=_NO_WINDOW)
        print(proc.stdout, flush=True)
        if proc.stderr:
            print("-- stderr (tail) --", flush=True)
            print(proc.stderr[-6000:], flush=True)
        print("EXIT =", proc.returncode, flush=True)
        return proc.returncode
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
