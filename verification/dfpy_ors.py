"""Launcher that runs a script with Dragonfly's bundled Python AND Dragonfly's
Python code on the path (ORSModel etc.), plus the DLL directories Dragonfly
uses — for probing how much of the ORS framework works outside the GUI app.

Same DLL caveat as dfpy.py: directories must be registered in-process.
"""
import ctypes
import os
import sys

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

_DRAGONFLY = r"C:\Program Files\Dragonfly"

for _d in (
    _DRAGONFLY + r"\libs",
    _DRAGONFLY + r"\Python_env\Library\bin",
):
    if os.path.isdir(_d):
        os.add_dll_directory(_d)
        os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")

# ORS python code + its env, as Dragonfly itself sets them (env.txt / setEVarsForPythonIDE.bat)
ors_python = _DRAGONFLY + r"\python"
os.environ["PYTHONPATH"] = ors_python
os.environ["QT_PLUGIN_PATH"] = _DRAGONFLY + r"\plugins\platforms"
os.environ.setdefault("CRYPTOGRAPHY_OPENSSL_NO_LEGACY", "1")
for _extra in (
    ors_python,
    _DRAGONFLY,
    _DRAGONFLY + r"\plugins",
):
    if _extra not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _extra + os.pathsep + os.environ.get("PATH", "")
# PYTHONPATH is only read at interpreter startup, so the ORS tree also goes on sys.path
if ors_python not in sys.path:
    sys.path.insert(0, ors_python)

if len(sys.argv) < 2:
    sys.exit("usage: dfpy_ors.py <script.py> [args...]")

import runpy

sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
