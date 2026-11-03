r"""The kernel smoke: the notebook ENGINE pins run in a fresh subprocess
and report 'KERNEL-SMOKE-OK' on success.

Round 8 moved the kernel out of Dragonfly into the standalone notebook
application process, and onto ONE worker ``QThread`` (the process
separation that closes the rounds-5-7 crash class by construction). The
engine pins survived the move -- same run contract, same nbformat-shaped
outputs, same in-place restart semantics -- but they are now driven
through the kernel's ASYNC surface: ``run_cell(code)`` only queues the
command to the worker thread, and this harness pumps the main thread's
event loop until the queued ``run_finished(count, outputs)`` arrives.
What is pinned:

  * construction starts NOTHING (``is_started`` False until the first
    run -- the lazy-start lifecycle);
  * ``'1 + 1'`` -> execute_result ``'2'`` with execution_count 1;
  * stdout and stderr arrive as SEPARATE stream outputs, in write order;
  * the stdin stub refuses ``input()`` -- the cell catches the
    RuntimeError and prints the message;
  * an inline matplotlib figure is a display_data whose image/png payload
    starts with 'iVBOR', with NO execute_result twin and NO error output
    (the cell succeeds -- the round-3 pin);
  * the seed pushes the given bridge client as ``dfnb`` and NOTHING of
    the Dragonfly process's ambient namespace: ``orsObj`` is a NameError
    in a cell (the notebook application process is ORS-free by design);
  * the GUI thread stays FREE while a cell runs: a plain ``QTimer``
    keeps ticking through a sleeping cell (the property the round-6
    scoped pump had to work around -- there is no pump any more);
  * a busy cell is interruptible from the GUI thread with exactly the
    Stop-button action -- ``kernel.request_stop()``, a flag set -- the
    worker's ``sys.settrace`` heartbeat arms the KeyboardInterrupt at the
    cell's next Python line, the ``heartbeats`` counter advanced while
    the cell ran, ``is_running`` returns False after, and the namespace
    survives the interrupt;
  * a QUEUED stop -- requested before the worker even picks the run up --
    survives into the run and still ends it as an interrupted cell (an
    error output, never an engine failure): when the stop is already set
    the tracer's first line event can be inside ipykernel's own
    ``run_cell`` machinery, the armed KeyboardInterrupt escapes
    ``shell.run_cell`` raw, and ``_execute_on_worker`` converts the
    escape into the interrupted-cell outcome; the shell stays healthy
    for the next cell;
  * ``restart()`` wipes the namespace IN PLACE and re-seeds dfnb: a
    variable defined before the restart raises NameError afterwards and
    the execution counter restarts at 1 (error outputs carry no
    execution_count field -- the round-2 honest pins);
  * round 16, custom environments: a kernel constructed with an
    ``environment`` spawns the venv's OWN interpreter (the runner) on
    its lazy start, takes the environment's in-use lock, and runs its
    cells THERE: the marker package imports from the environment's
    site-packages, sys.executable is the venv's interpreter (the
    process boundary that replaced the in-process activation), a dfnb
    call relays through the parent to the stub, the GUI thread stays
    free, Stop interrupts a busy cell with a KeyboardInterrupt (the
    child's namespace survives), restart wipes the runner's namespace,
    and ``shutdown()`` + ``set_environment`` lands the next run on the
    SECOND environment (its lock re-acquired, its marker bound), and
    ``stop()`` (the window-close teardown) ends the live environment
    child with its CLEAN quit -- exit 0, the in-use lock freed, never
    the TerminateProcess fallback (the round-18 review pin: the quit
    line used to be written after ``self._child`` was nulled, so it
    raised into a bare except and every close hard-killed the child).

TWO modes -- the first argument decides which build under test:

  * ``dfpy_ors.py verification/_kernel_smoke_helper.py <project-dir>`` --
    the PROJECT-SOURCES mode: the project dir goes on sys.path and the
    plugin modules are imported directly (``DragonflyNotebook...``, the
    seam the tier-2 suite uses) so the pins run against the sources under
    review BEFORE any install (the round-6 pattern).
  * ``dfpy_ors.py verification/_kernel_smoke_helper.py`` (no argument) --
    the INSTALLED mode: the plugin is imported the way the app's plugin
    manager sees it (``pythonUserExtensions.Plugins.DragonflyNotebook``),
    probed at the installed data roots exactly as before.
    ``DFNB_INSTALL_ROOT`` overrides the probe with one explicit data root
    -- the staging seam the tier-3 suite uses for pre-install
    verification (a staged copy of the reviewed sources laid out like an
    install, exercised without touching the real %LOCALAPPDATA% tree).

Invoked by the tier-3 installed suite (test_dragonflynotebook_installed.py)
in the installed mode through the dfpy_ors launcher with a subprocess
timeout -- the kernel runs on its own worker thread inside this helper
process, so a hung cell cannot hang the GATE process; the tier-3 wrapper
bounds the subprocess with a hard timeout and treats ANY failure, timeout
or unstartable helper as a FAILED suite -- a hard gate, never a skip. This
file therefore only needs to be honest: exit 0 + the marker when the engine
works, a traceback and a non-zero exit when it does not.

Run standalone (from the project root):

  # the project sources (pre-install):
  uv run --no-project --python "C:\Program Files\Dragonfly\Python_env\python.exe" \
      verification/dfpy_ors.py verification/_kernel_smoke_helper.py <project-dir>
  # the installed build (what the tier-3 suite runs):
  uv run --no-project --python "C:\Program Files\Dragonfly\Python_env\python.exe" \
      verification/dfpy_ors.py verification/_kernel_smoke_helper.py
"""

import importlib
import os
import shutil
import sys
import tempfile
import time
import types

# Same multiple-OpenMP-runtime workaround as the core: MUST precede numpy,
# which the kernel transitively imports.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "True")

# ---- the mode: project sources (argv[1]) or the installed build -----------
_PROJECT_DIR = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else None
if _PROJECT_DIR is not None and not os.path.isdir(
        os.path.join(_PROJECT_DIR, "DragonflyNotebook")):
    sys.exit("argv[1] is not a DragonflyNotebook project dir (no "
             "DragonflyNotebook package inside it): {!r}".format(_PROJECT_DIR))

_LOCAL = os.environ.get("LOCALAPPDATA", "")


def _find_user_root():
    """The first Dragonfly data root whose Plugins folder holds the plugin
    (the installer's probe order: Comet, ORS, then %LOCALAPPDATA% itself).
    ``DFNB_INSTALL_ROOT`` overrides the probe with one explicit root -- the
    tier-3 suite's staging seam."""
    override = os.environ.get("DFNB_INSTALL_ROOT", "")
    if override:
        if os.path.isdir(os.path.join(override, "pythonUserExtensions",
                                      "Plugins", "DragonflyNotebook")):
            return override
        return None
    for parent in (os.path.join(_LOCAL, "Comet"),
                   os.path.join(_LOCAL, "ORS"),
                   _LOCAL):
        if not parent or not os.path.isdir(parent):
            continue
        for name in sorted(os.listdir(parent)):
            if not name.startswith("Dragonfly"):
                continue
            candidate = os.path.join(parent, name)
            if os.path.isdir(os.path.join(candidate, "pythonUserExtensions",
                                          "Plugins", "DragonflyNotebook")):
                return candidate
    return None


if _PROJECT_DIR is not None:
    sys.path.insert(0, _PROJECT_DIR)
else:
    user_root = _find_user_root()
    assert user_root is not None, (
        "no installed DragonflyNotebook found under %LOCALAPPDATA% -- run "
        "Install.ps1 first (or set DFNB_INSTALL_ROOT to a staged install "
        "root)")
    if user_root not in sys.path:
        sys.path.insert(0, user_root)

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

# Bound on purpose (the PyQt ownership lesson the tier-2 loopback taught:
# an unbound parentless QCoreApplication wrapper is destroyed by the next
# GC, taking timer delivery and queued signal delivery with it).
app = QApplication.instance() or QApplication(["dfnb_kernel_smoke"])

if _PROJECT_DIR is not None:
    import DragonflyNotebook.notebook_kernel as kernel_mod  # noqa: E402
    print("KERNEL-SMOKE mode: PROJECT SOURCES ({})".format(_PROJECT_DIR),
          flush=True)
else:
    import pythonUserExtensions.Plugins.DragonflyNotebook.notebook_kernel \
        as kernel_mod  # noqa: E402
    print("KERNEL-SMOKE mode: installed plugin ({})".format(
        kernel_mod.__file__), flush=True)


# ---- the async harness -----------------------------------------------------

_PUMP_MS = 25


def _wait_until(predicate, timeout_s, what):
    """Pumps the main thread's event loop until ``predicate()`` holds (the
    worker emits its signals queued onto this thread)."""
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() > deadline:
            return False
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, _PUMP_MS)
    return True


class _RunCollector:
    """Collects the kernel's next finished run (or engine failure) through
    the queued signals."""

    def __init__(self, kernel):
        self.kernel = kernel
        self.count = None
        self.outputs = None
        self.failure = None
        kernel.run_finished.connect(self._on_finished)
        kernel.run_failed.connect(self._on_failed)

    def _on_finished(self, count, outputs):
        if self.count is None:
            self.count = count
            self.outputs = list(outputs or [])

    def _on_failed(self, message):
        if self.failure is None:
            self.failure = message

    def run(self, code, timeout_s=120.0):
        """Starts one cell and pumps until it finishes. Returns
        ``(execution_count, outputs)``; an engine-level ``run_failed``
        raises AssertionError (user-code errors arrive as error OUTPUTS,
        never through run_failed)."""
        self.count = None
        self.outputs = None
        self.failure = None
        self.kernel.run_cell(code)
        if not _wait_until(
                lambda: self.count is not None or self.failure is not None,
                timeout_s,
                'the cell never finished within {} s (heartbeats={}, '
                'is_started={})'.format(timeout_s, self.kernel.heartbeats,
                                        self.kernel.is_started)):
            raise AssertionError(
                'the cell never finished within {} s (heartbeats={}, '
                'is_started={})'.format(timeout_s, self.kernel.heartbeats,
                                        self.kernel.is_started))
        if self.failure is not None:
            raise AssertionError('run_failed: {}'.format(self.failure))
        return self.count, self.outputs


# ---- the pins --------------------------------------------------------------

# The pushed dfnb: a stub is enough for the ENGINE pins -- the real bridge
# client crossing is pinned by the tier-2 loopback and the tier-3 spawn test.
# list_objects returns what the real client method returns (the unwrapped
# LIST of live-object dicts, not the handler's {'objects': ...} payload).
_DFNB_STUB = types.SimpleNamespace(
    list_objects=lambda *args, **kwargs: [
        {'guid': 'g-1', 'title': 't-1', 'class_name': 'Channel'}])

kernel = kernel_mod.NotebookKernel(dfnb=_DFNB_STUB)
collector = _RunCollector(kernel)

# ---- round 16: the environment pins ride a SECOND kernel -------------------
# The pins above stay on the embedded default; this kernel is constructed
# with an explicit ``environment``, exactly the way the form constructs one
# after a dropdown switch. Two minimal but RUNNABLE venv trees carry a
# marker package each: ``pyvenv.cfg`` with ``home`` pointing at the
# embedded distribution, a REAL copy of the stock interpreter in
# ``Scripts`` (``embedded_python``: the manifest-clean exe the real
# creation uses, so the fake tree behaves like a venv the ``venv`` module
# built), and the marker in ``site-packages`` -- the shape
# ``list_environments`` discovers AND the one the subprocess kernel can
# actually EXECUTE, because the cells below run on those interpreters
# for real. The paths are explicit, so no DFNB_ENVS_ROOT seam is needed
# here, and the real venv CREATION / pip install subprocess runs are
# pinned by tier 1 (the argv shapes) and tier 3 (the real runs through
# the form). This helper pins the KERNEL side: the subprocess spawn, the
# in-use lock, the dfnb relay, the stop, the restart, and the switch.
# The import goes through the kernel's own package so ``envs_mod`` IS the
# module whose in-use registry the worker writes to (its _in_use_fds).
envs_mod = importlib.import_module(
    kernel_mod.__name__.rsplit('.', 1)[0] + '.notebook_envs')


def _make_fake_env(parent, name, tag):
    """A minimal but RUNNABLE venv tree: ``pyvenv.cfg`` with ``home``
    pointing at the embedded distribution (the venv's stdlib), a REAL
    copy of the stock interpreter in ``Scripts``, and the marker package
    in ``site-packages`` (the venv's own import path: nothing of the
    embedded site-packages reaches the child, which is the point of the
    subprocess model)."""
    venv_dir = os.path.join(parent, name)
    pkg_dir = os.path.join(envs_mod.venv_site_packages(venv_dir),
                           'dfnb_smoke_pkg')
    os.makedirs(pkg_dir)
    with open(os.path.join(venv_dir, 'pyvenv.cfg'), 'w',
              encoding='utf-8') as handle:
        handle.write('home = {}\n'
                     'include-system-site-packages = false\n'.format(
                         os.path.dirname(envs_mod.embedded_python())))
    with open(os.path.join(pkg_dir, '__init__.py'), 'w',
              encoding='utf-8') as handle:
        handle.write('WHICH = {!r}\n'.format(tag))
    exe = envs_mod.venv_python(venv_dir)
    os.makedirs(os.path.dirname(exe), exist_ok=True)
    shutil.copyfile(envs_mod.embedded_python(), exe)
    return venv_dir


_env_work = tempfile.mkdtemp(prefix='dfnb_smoke_envs_')
_ENV_A = _make_fake_env(_env_work, 'envA', 'A')
_ENV_B = _make_fake_env(_env_work, 'envB', 'B')
env_kernel = kernel_mod.NotebookKernel(dfnb=_DFNB_STUB, environment=_ENV_A)
env_collector = _RunCollector(env_kernel)

try:
    assert kernel.is_started is False and kernel.is_running is False, \
        "construction must start NOTHING (the lazy-start lifecycle)"
    print("PASS construction starts nothing (lazy start)", flush=True)

    count, outputs = collector.run("1 + 1")
    results = [o for o in outputs if o["output_type"] == "execute_result"]
    assert results, "no execute_result among the outputs: {}".format(outputs)
    assert results[0]["data"].get("text/plain") == "2", \
        "1 + 1 produced {!r}".format(results[0]["data"])
    assert count == 1, \
        "the first executed cell is execution_count 1, got {}".format(count)
    assert kernel.is_started is True, "the first run started the kernel"
    print("PASS '1 + 1' -> execute_result '2', execution_count 1 "
          "(lazy start on the first run)", flush=True)

    # Round 14, the window-spam fix pinned on the REAL start path: the
    # kernel start that just ran rerouted jupyter_client's interface
    # ladder (localinterfaces._load_ips) away from its `ipconfig`
    # subprocess rung -- on Windows that rung shells out to ipconfig
    # BEFORE the library's own pure-socket loader, and a subprocess of a
    # console-less parent (the DETACHED notebook app; the CREATE_NO_
    # WINDOW gate children) allocates a NEW VISIBLE console window (the
    # live `ipconfig` flash at the first cell's run, captured in the
    # round-13 watcher log). The in-process kernel never opens a socket
    # (the connection info is vestigial), so the socket-based loader
    # answers instead: same values, zero child processes. _ensure_
    # started calls the suppression helper BEFORE the manager exists,
    # so this check sees the reroute the actual start left behind. (It
    # runs here, in the SMOKE CHILD, on purpose: a jupyter_client import
    # inside the tier-3 suite process breaks its offscreen window
    # activation -- bisected in round 14 -- so the suite pins this PASS
    # line through the smoke's output instead.)
    from jupyter_client import localinterfaces
    assert localinterfaces._load_ips_ipconfig is \
        localinterfaces._load_ips_gethostbyname, \
        ("the kernel start must reroute the localinterfaces Windows rung "
         "to the library's own socket-based loader (the `ipconfig` "
         "subprocess -- the round-13 window spam)")
    print("PASS the kernel start rerouted localinterfaces away from "
          "`ipconfig` (the socket-based loader answers -- no console "
          "window)", flush=True)

    # stdout and stderr are SEPARATE stream outputs, in write order.
    count, outputs = collector.run('print("out")\nimport sys\n'
                                   'sys.stderr.write("warn\\n")')
    streams = [o for o in outputs if o["output_type"] == "stream"]
    assert [(o["name"], o["text"]) for o in streams] == \
        [("stdout", "out\n"), ("stderr", "warn\n")], \
        "stdout and stderr must be separate streams, in order: {}".format(outputs)
    print("PASS stdout/stderr as separate stream outputs", flush=True)

    # the stdin stub refuses input() with its message; the cell catches the
    # RuntimeError and prints it instead of blocking the worker thread.
    count, outputs = collector.run(
        'try:\n    input()\nexcept RuntimeError as exc:\n'
        '    print("stdin refused:", exc)')
    refusals = [o for o in outputs if o["output_type"] == "stream"
                and "stdin refused" in o.get("text", "")]
    assert refusals, "input() was not refused with a printed message: {}".format(outputs)
    print("PASS the stdin stub refuses input()", flush=True)

    # an inline figure: display_data with a base64 PNG payload, and NO
    # execute_result twin for the cell.
    count, outputs = collector.run(
        'import matplotlib.pyplot as plt\nplt.figure()\n'
        'plt.plot([1, 2], [3, 4])\nplt.show()')
    figures = [o for o in outputs
               if o["output_type"] == "display_data" and "image/png" in o["data"]]
    assert figures, "plt.show() produced no display_data image/png: {}".format(outputs)
    assert figures[0]["data"]["image/png"].startswith("iVBOR"), \
        "the figure payload is not base64 PNG: {!r}".format(
            figures[0]["data"]["image/png"][:20])
    assert not any(o["output_type"] == "execute_result" for o in outputs), \
        "a displayed figure must not gain an execute_result twin: {}".format(outputs)
    assert not any(o["output_type"] == "error" for o in outputs), \
        "the figure cell errored after capturing: {}".format(outputs)
    print("PASS inline figure (base64 length {}, cell succeeded)".format(
        len(figures[0]["data"]["image/png"])), flush=True)

    # the seed pushed the given bridge client as dfnb -- and NOTHING of the
    # Dragonfly process's ambient namespace: the notebook application process
    # is ORS-free by design, so ``orsObj`` must be a NameError in a cell.
    count, outputs = collector.run(
        "dfnb is not None and dfnb.list_objects is not None")
    results = [o for o in outputs if o["output_type"] == "execute_result"]
    assert results and results[0]["data"].get("text/plain") == "True", \
        "the pushed dfnb module is missing: {}".format(outputs)
    count, outputs = collector.run("orsObj")
    errors = [o for o in outputs if o["output_type"] == "error"]
    assert errors and errors[0]["ename"] == "NameError", \
        ("the Dragonfly ambient names must NOT leak into the notebook "
         "application's namespace (the process is ORS-free), got "
         "{}".format(outputs))
    print("PASS seed pushed dfnb; the ambient ORS names do not leak "
          "(orsObj -> NameError)", flush=True)

    # the GUI thread stays FREE while a cell runs: a plain QTimer keeps
    # ticking through a sleeping cell. (Round 8 made this true by
    # construction -- the cell runs on the worker thread -- and the pin
    # holds it: under the rounds-5-7 architecture the GUI thread was inside
    # shell.run_cell and no plain timer could deliver.)
    ticks = []
    timer = QTimer()
    timer.setInterval(50)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    try:
        count, outputs = collector.run('import time\ntime.sleep(1.0)\n'
                                       'print("slept")')
    finally:
        timer.stop()
    assert len(ticks) >= 10, \
        ("the GUI thread must keep servicing timers while a cell sleeps "
         "(the cell runs on the worker thread), got {} tick(s)"
         .format(len(ticks)))
    slept = [o for o in outputs if o["output_type"] == "stream"
             and "slept" in o.get("text", "")]
    assert slept, "the sleeping cell's print did not arrive: {}".format(outputs)
    print("PASS the GUI thread stayed free through a sleeping cell "
          "({} QTimer ticks at 50 ms)".format(len(ticks)), flush=True)

    # the interrupt pin: a ~3M-iteration busy cell on the worker thread,
    # stopped from THIS (GUI) thread with exactly the Stop-button action --
    # kernel.request_stop(), a flag set. The worker's heartbeat tracer arms
    # the KeyboardInterrupt at the cell's next Python line.
    count, outputs = collector.run('ki_probe = "alive"')
    assert outputs == [], "a silent assignment must have no outputs: {}".format(outputs)
    heartbeats_before = kernel.heartbeats
    collector.count = None
    collector.outputs = None
    collector.failure = None
    kernel.run_cell("total = 0\nfor i in range(3000000):\n    total += i")
    assert _wait_until(lambda: kernel.heartbeats > heartbeats_before, 30.0,
                       "the tracer ticked"), \
        ("the heartbeat tracer must tick on the worker thread while the "
         "busy cell runs (heartbeats {} -> {})".format(
             heartbeats_before, kernel.heartbeats))
    kernel.request_stop()
    assert _wait_until(
        lambda: collector.count is not None or collector.failure is not None,
        60.0, "the interrupted cell never finished"), \
        ("the interrupted cell never finished (heartbeats={}, "
         "is_running={})".format(kernel.heartbeats, kernel.is_running))
    assert collector.failure is None, \
        "the interrupt surfaced as an engine failure: {!r}".format(collector.failure)
    errors = [o for o in (collector.outputs or [])
              if o["output_type"] == "error"]
    assert errors and errors[0]["ename"] == "KeyboardInterrupt", \
        ("the stopped cell must error with KeyboardInterrupt, got "
         "{}".format(collector.outputs))
    assert kernel.is_running is False, \
        "the kernel must be idle again after the interrupt"
    print("PASS interrupt: request_stop() from the GUI thread stopped the "
          "busy cell ({} heartbeat tick(s) during it) with a "
          "KeyboardInterrupt".format(kernel.heartbeats - heartbeats_before),
          flush=True)

    # the namespace survived the interrupt
    count, outputs = collector.run("ki_probe")
    results = [o for o in outputs if o["output_type"] == "execute_result"]
    assert results and results[0]["data"].get("text/plain") == "'alive'", \
        ("the namespace must survive the interrupt: ki_probe -> "
         "{}".format(outputs))
    print("PASS namespace survived the interrupt ('ki_probe' -> 'alive')",
          flush=True)

    # the QUEUED-stop pin: a stop requested while the run is still queued
    # (before the worker even picks it up) must SURVIVE into the run -- the
    # flag is deliberately not cleared at run start -- and stop the cell.
    # This is also the case where the tracer's first line event is INSIDE
    # ipykernel's own run_cell machinery (the compiled cell body runs from
    # a temp file, but the prologue around it is library Python), so the
    # armed KeyboardInterrupt escapes shell.run_cell raw -- IPython only
    # captures interrupts raised inside the cell's exec -- and
    # _execute_on_worker converts that escape into the interrupted-cell
    # outcome (an error output, never an engine failure). The tier-3 spawn
    # test pins the same scenario through the real application; this is
    # the engine-level pin.
    collector.count = None
    collector.outputs = None
    collector.failure = None
    kernel.run_cell('import time\ntime.sleep(5)\nprint("never")')
    kernel.request_stop()
    assert _wait_until(
        lambda: collector.count is not None or collector.failure is not None,
        60.0, "the queued-stop cell never finished"), \
        ("the queued-stop cell never finished (heartbeats={}, "
         "is_started={})".format(kernel.heartbeats, kernel.is_started))
    assert collector.failure is None, \
        ("a queued stop must surface as an interrupted cell, not an engine "
         "failure: {!r}".format(collector.failure))
    queued_errors = [o for o in (collector.outputs or [])
                     if o["output_type"] == "error"]
    assert queued_errors and queued_errors[0]["ename"] == "KeyboardInterrupt", \
        ("the queued-stop cell must end with a KeyboardInterrupt error "
         "output, got {}".format(collector.outputs))
    assert kernel.is_running is False, \
        "the kernel must be idle again after the queued stop"
    count, outputs = collector.run("6 * 7")
    results = [o for o in outputs if o["output_type"] == "execute_result"]
    assert results and results[0]["data"].get("text/plain") == "42", \
        ("the shell must stay healthy after a stop escaped through "
         "IPython's machinery: 6 * 7 -> {}".format(outputs))
    print("PASS queued stop (requested before the run started) interrupted "
          "the cell with a KeyboardInterrupt and the shell stayed healthy",
          flush=True)

    # restart(): the namespace is wiped IN PLACE and dfnb is re-seeded.
    collector.run('restart_probe = "abc"')
    restart_box = {}
    kernel.restart_finished.connect(
        lambda ok, message: restart_box.update(ok=ok, message=message))
    kernel.restart()
    assert _wait_until(lambda: 'ok' in restart_box, 60.0,
                       "the restart never finished"), \
        "the restart never finished ({!r})".format(restart_box)
    assert restart_box['ok'] is True, \
        "the restart failed: {!r}".format(restart_box)
    count, outputs = collector.run("dfnb is not None")
    results = [o for o in outputs if o["output_type"] == "execute_result"]
    assert results and results[0]["data"].get("text/plain") == "True", \
        "dfnb must be re-seeded after the restart: {}".format(outputs)
    assert count == 1, \
        "the restart resets the execution counter, got {}".format(count)
    count, outputs = collector.run("restart_probe")
    errors = [o for o in outputs if o["output_type"] == "error"]
    assert errors and errors[0]["ename"] == "NameError", \
        "a pre-restart variable must be gone after the restart: {}".format(outputs)
    # the honest execution_count pin for this cell (observed on this engine,
    # not assumed): the restart reset the counter, so the dfnb cell ran as 1
    # and this NameError cell as 2 -- and an error output carries NO
    # execution_count field (only execute_result outputs do).
    assert count == 2, \
        "the NameError cell after the restart is execution_count 2, got {}".format(count)
    assert "execution_count" not in errors[0], \
        "error outputs carry no execution_count field: {}".format(errors[0])
    print("PASS restart (namespace wiped, dfnb re-seeded, counter reset)",
          flush=True)

    # Round 16, environments -- the SECOND kernel: construction with an
    # environment starts nothing (the lazy-start lifecycle holds); the
    # first run spawns the venv's OWN interpreter with the runner, takes
    # the in-use lock, and executes the cell THERE (the marker package
    # binds from the environment's site-packages and sys.executable IS
    # the venv's interpreter -- the process boundary that replaced the
    # in-process activation); the dfnb stub answers a cell's call through
    # the parent's relay; ``shutdown()`` stops the kernel WITHOUT the
    # worker thread, so the lock frees and the next run lazy-starts
    # fresh on the environment ``set_environment`` staged.
    assert env_kernel.is_started is False and \
        envs_mod.is_environment_in_use(_ENV_A) is False, \
        ("construction with an environment must neither start the kernel "
         "nor take its lock (the lazy-start lifecycle)")
    count, outputs = env_collector.run(
        'import dfnb_smoke_pkg, sys\n'
        'print("ENVW", dfnb_smoke_pkg.WHICH, dfnb_smoke_pkg.__file__)\n'
        'print("EXE", sys.executable)')
    env_streams = [o for o in outputs if o["output_type"] == "stream"]
    assert env_streams and env_streams[0]["text"].startswith("ENVW A ") and \
        _ENV_A in env_streams[0]["text"], \
        ("the marker package did not import from the first environment's "
         "site-packages: {}".format(outputs))
    # the child's two prints share ONE throttled stream line (the writer
    # flushes at most once per 0.1 s), so the EXE line is PARSED out of
    # the stream text instead of looked up as its own output
    exe_value = None
    for stream in env_streams:
        for line in stream["text"].splitlines():
            if line.startswith("EXE "):
                exe_value = line
    assert exe_value == "EXE {}".format(envs_mod.venv_python(_ENV_A)), \
        ("the cell did not run on the venv's own interpreter: "
         "{}".format(outputs))
    assert env_kernel.active_environment == _ENV_A
    assert envs_mod.is_environment_in_use(_ENV_A) is True, \
        "the started kernel must hold its environment's in-use lock"
    print("PASS environment spawn (marker from the venv's own "
          "site-packages, sys.executable is the venv's interpreter, "
          "in-use lock held)", flush=True)

    # the dfnb relay: a cell's call crosses child -> parent -> stub and
    # the answer comes back as the cell's execute_result (the stub
    # returns the real client method's unwrapped list of object dicts)
    count, outputs = env_collector.run("dfnb.list_objects()")
    results = [o for o in outputs if o["output_type"] == "execute_result"]
    assert results and results[0]["data"].get("text/plain") == \
        "[{'guid': 'g-1', 'title': 't-1', 'class_name': 'Channel'}]", \
        "the relayed dfnb call did not answer: {}".format(outputs)
    print("PASS dfnb relay (list_objects crossed child -> parent -> stub)",
          flush=True)

    # the GUI thread stays free while an ENVIRONMENT cell runs: the
    # worker only waits for the child's lines; the cell itself is
    # another process entirely
    ticks = []
    timer = QTimer()
    timer.setInterval(50)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    try:
        count, outputs = env_collector.run('import time\ntime.sleep(1.0)\n'
                                           'print("slept-env")')
    finally:
        timer.stop()
    assert len(ticks) >= 10, \
        ("the GUI thread must keep servicing timers while an environment "
         "cell sleeps, got {} tick(s)".format(len(ticks)))
    slept = [o for o in outputs if o["output_type"] == "stream"
             and "slept-env" in o.get("text", "")]
    assert slept, "the sleeping environment cell's print did not arrive"
    print("PASS the GUI thread stayed free through an environment cell "
          "({} QTimer ticks at 50 ms)".format(len(ticks)), flush=True)

    # Stop: a busy environment cell interrupted from THIS (GUI) thread
    # with exactly the Stop-button action -- kernel.request_stop() -- ends
    # as a KeyboardInterrupt error output, never an engine failure, and
    # the child's namespace survives the interrupt. (If the stop lands
    # before the worker even starts the run it is forwarded at run start
    # and the cell never runs; either path ends as a KeyboardInterrupt.)
    env_collector.run('env_ki_probe = "alive"')
    env_collector.count = None
    env_collector.outputs = None
    env_collector.failure = None
    env_kernel.run_cell('total = 0\nfor i in range(30000000):\n'
                        '    total += i')
    env_kernel.request_stop()
    assert _wait_until(
        lambda: env_collector.count is not None or
        env_collector.failure is not None,
        60.0, "the stopped environment cell never finished"), \
        "the stopped environment cell never finished"
    assert env_collector.failure is None, \
        "the stop surfaced as an engine failure: {!r}".format(
            env_collector.failure)
    errors = [o for o in (env_collector.outputs or [])
              if o["output_type"] == "error"]
    assert errors and errors[0]["ename"] == "KeyboardInterrupt", \
        ("the stopped environment cell must end with a "
         "KeyboardInterrupt, got {}".format(env_collector.outputs))
    assert env_kernel.is_running is False, \
        "the environment kernel must be idle again after the stop"
    count, outputs = env_collector.run("env_ki_probe")
    results = [o for o in outputs if o["output_type"] == "execute_result"]
    assert results and results[0]["data"].get("text/plain") == "'alive'", \
        ("the child's namespace must survive the interrupt: "
         "{}".format(outputs))
    print("PASS environment stop (request_stop() from the GUI thread "
          "interrupted the busy cell; the namespace survived)",
          flush=True)

    # restart: the runner wipes its own namespace and counter and
    # re-seeds dfnb (the child process itself stays; the pins that
    # matter are the wipe, the re-seed and the counter reset)
    env_collector.run('env_restart_probe = "abc"')
    restart_box = {}
    env_kernel.restart_finished.connect(
        lambda ok, message: restart_box.update(ok=ok, message=message))
    env_kernel.restart()
    assert _wait_until(lambda: 'ok' in restart_box, 60.0,
                      "the environment restart never finished"), \
        "the environment restart never finished ({!r})".format(restart_box)
    assert restart_box['ok'] is True, \
        "the environment restart failed: {!r}".format(restart_box)
    count, outputs = env_collector.run("dfnb is not None")
    results = [o for o in outputs if o["output_type"] == "execute_result"]
    assert results and results[0]["data"].get("text/plain") == "True", \
        ("dfnb must be seeded after the environment restart: "
         "{}".format(outputs))
    assert count == 1, \
        ("the environment restart resets the execution counter, got "
         "{}".format(count))
    count, outputs = env_collector.run("env_restart_probe")
    errors = [o for o in outputs if o["output_type"] == "error"]
    assert errors and errors[0]["ename"] == "NameError", \
        ("a pre-restart variable must be gone after the environment "
         "restart: {}".format(outputs))
    print("PASS environment restart (runner namespace wiped, dfnb "
          "re-seeded, counter reset)", flush=True)

    # the switch seam: set_environment stages the SECOND venv, shutdown
    # stops the child and frees the first lock, and the next run
    # lazy-starts fresh on the second environment's interpreter
    stopped_box = {}
    env_kernel.kernel_stopped.connect(
        lambda: stopped_box.update(stopped=True))
    env_kernel.set_environment(_ENV_B)
    env_kernel.shutdown()
    assert _wait_until(lambda: stopped_box.get("stopped"), 60.0,
                      "the shutdown never landed"), \
        "shutdown() never stopped the kernel"
    assert env_kernel.is_started is False, \
        "shutdown() must leave the kernel stopped (the worker survives)"
    assert envs_mod.is_environment_in_use(_ENV_A) is False, \
        "the shutdown must release the first environment's lock"
    count, outputs = env_collector.run(
        'import dfnb_smoke_pkg, sys\n'
        'print("ENVW", dfnb_smoke_pkg.WHICH, dfnb_smoke_pkg.__file__)\n'
        'print("EXE", sys.executable)')
    env_streams = [o for o in outputs if o["output_type"] == "stream"]
    assert env_streams and env_streams[0]["text"].startswith("ENVW B ") and \
        _ENV_B in env_streams[0]["text"], \
        ("the second environment's marker must bind after the switch "
         "(a fresh child on the second venv): {}".format(outputs))
    exe_value = None
    for stream in env_streams:
        for line in stream["text"].splitlines():
            if line.startswith("EXE "):
                exe_value = line
    assert exe_value == "EXE {}".format(envs_mod.venv_python(_ENV_B)), \
        "the switched cell did not run on the second venv's interpreter"
    assert env_kernel.active_environment == _ENV_B
    assert envs_mod.is_environment_in_use(_ENV_B) is True, \
        "the fresh start must hold the SECOND environment's lock"
    print("PASS environment switch (shutdown freed the lock; the next run "
          "spawned the SECOND venv's interpreter and took its lock)",
          flush=True)

    # round 17, the pump's stray-line tolerance: a cell writing straight
    # to the REAL stdout (a shell-out that bypassed the ! helper, or any
    # grandchild inheriting the handle) must surface as plain stream
    # output instead of vanishing into the JSON channel, and the
    # protocol must survive it and finish the cell normally
    count, outputs = env_collector.run(
        'import os\n'
        'os.write(1, b"RAWLINE the grandchild spoke\\n")\n'
        'os.write(1, b"RAWLINE two\\n")\n'
        '"done"')
    raw_lines = [o for o in outputs
                 if o["output_type"] == "stream"
                 and "RAWLINE" in o["text"]]
    assert raw_lines and \
        "the grandchild spoke" in raw_lines[0]["text"], \
        ("a raw write on the child's stdout must surface as stream "
         "output: {}".format(outputs))
    results = [o for o in outputs if o["output_type"] == "execute_result"]
    assert results, \
        ("the JSON protocol must survive raw stdout writes: "
         "{}".format(outputs))
    print("PASS environment stray stdout (raw fd-1 writes relayed as "
          "stream text; the JSON protocol survived and the cell finished)",
          flush=True)

    # round 17, the runtime DLL delivery on a REAL child: the kernel's
    # start copied the SCAN-DISCOVERED misparked set into this venv's
    # Scripts (ensure_runtime_dlls -- not name-keyed: the import tables
    # of the base tree's extension modules decide, and on this machine
    # that set heals sqlite3 AND tkinter alike) and the runner armed
    # that directory for the dependent load, so the stdlib extension
    # family imports inside the environment kernel (in a bare venv on
    # this distribution sqlite3 and tkinter are DLL-load ImportErrors)
    runtime_cell = (
        'import bz2, ctypes, hashlib, lzma, select\n'
        'import sqlite3, ssl, unicodedata, zlib\n'
        'print("SMOKE-RT", sqlite3.connect(":memory:")\n'
        '      .execute("select 41 + 1").fetchone()[0],\n'
        '      ssl.OPENSSL_VERSION.split()[1],\n'
        '      ctypes.sizeof(ctypes.c_void_p))')
    if os.path.isfile(os.path.join(
            os.path.dirname(envs_mod.venv_python(_ENV_B)),
            'tcl86t.dll')):
        runtime_cell += ('\nimport tkinter\n'
                         'print("SMOKE-TK", tkinter.TkVersion)')
    count, outputs = env_collector.run(runtime_cell)
    streams = [o for o in outputs if o["output_type"] == "stream"]
    errors = [o for o in outputs if o["output_type"] == "error"]
    all_stream_text = "".join(o["text"] for o in streams)
    assert streams and "SMOKE-RT 42" in all_stream_text \
        and not errors, \
        ("the environment child must import the stdlib extension family "
         "and answer a sqlite query: {}".format(outputs))
    if "SMOKE-TK" in runtime_cell:
        assert "SMOKE-TK" in all_stream_text, \
            ("tkinter (the set's second machine-misparked member, "
             "tcl86t.dll) must import too: {}".format(outputs))
    print("PASS environment runtime DLLs (the scan-discovered set + the "
          "runner's directory arm made the stdlib extension family, "
          "sqlite3 and tkinter included, import in the venv child)",
          flush=True)

    # round 18 review, the stop() quit pin: stop() (the window-close
    # teardown) must deliver its documented best-effort QUIT to the
    # live environment child, not silently fall through to the hard
    # kill -- the old teardown nulled self._child BEFORE writing the
    # quit line, so _child_write raised 'the environment kernel is not
    # running' into the bare except, the two bounded waits ran out on
    # the idle child (which sits in its command loop and never exits
    # on its own) and TerminateProcess landed (exit 1). The clean quit
    # (the runner's quit op, or its stdin-EOF _parent_gone exit) leaves
    # exit code 0; a kill leaves 1 -- the returncode is the pin.
    stop_child = env_kernel._child
    assert stop_child is not None and stop_child.poll() is None, \
        "the environment child must be alive right before the stop pin"
    stop_t0 = time.monotonic()
    env_kernel.stop()
    stop_elapsed = time.monotonic() - stop_t0
    assert env_kernel.is_started is False, \
        "stop() must leave the kernel stopped"
    assert stop_child.poll() is not None, \
        "the environment child must be gone after stop()"
    assert stop_child.returncode == 0, \
        ("stop() must end the environment child with its CLEAN exit "
         "(the quit op or the stdin-EOF fallback), never a "
         "TerminateProcess kill: exit {}".format(stop_child.returncode))
    assert envs_mod.is_environment_in_use(_ENV_B) is False, \
        "stop() must free the environment's in-use lock"
    assert stop_elapsed < 6.0, \
        ("stop() must not wait out the full bounded-wait ladder on a "
         "healthy child ({:.1f} s)".format(stop_elapsed))
    print("PASS environment stop teardown (stop() delivered the clean "
          "quit: exit 0, the in-use lock freed, {} ms)"
          .format(int(stop_elapsed * 1000)), flush=True)
except BaseException:
    # The finally below stops the kernels, and that teardown can crash
    # the process on this machine (the documented ORS teardown class) --
    # which would swallow THIS traceback. Print the real failure first:
    # a silent exit with no marker already means a failed gate, but a
    # readable one costs nothing.
    import traceback as _tb
    _tb.print_exc()
    raise
finally:
    for one in (env_kernel, kernel):
        try:
            one.stop()
        except Exception:
            pass

print("KERNEL-SMOKE-OK (the engine pins on the worker-thread kernel: lazy "
      "start, 1+1, streams, stdin refusal, inline figure, dfnb seeded + "
      "orsObj a NameError, GUI-timer freedom through a sleeping cell, "
      "request_stop interrupt + heartbeat ticks + namespace survival, "
      "queued stop, restart, localinterfaces rerouted off `ipconfig`, "
      "environment subprocess spawn + dfnb relay + stop + restart + "
      "shutdown switch + stray-stdout tolerance + the runtime DLL set + "
      "the clean-quit stop teardown)",
      flush=True)
