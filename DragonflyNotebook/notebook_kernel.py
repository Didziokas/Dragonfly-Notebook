r"""The notebook application's IPython kernel engine (round 8).

Since round 8 the kernel runs in the STANDALONE notebook application
process, on ONE dedicated worker ``QThread`` -- not on the GUI thread, and
not inside Dragonfly at all. That thread model is what finally closes the
crash class rounds 5-7 chased: a cell can no longer block or re-enter
Dragonfly's GUI thread because it never touches it; the GUI thread stays
free (its event loop keeps running -- repainting, timers, user input) while
a cell runs, and ORS object operations stay on Dragonfly's own GUI thread
behind the bridge. The round-6 scoped pump, the round-7 progress dialog
and the lib's run-depth guard are all GONE: nothing here pumps events,
because the GUI thread is never blocked in the first place.

Engine: the same ``QtInProcessKernelManager`` setup the shipped Python
console uses, constructed LAZILY on the worker thread on the first cell
run (importing this module must stay cheap and must never start a
kernel). In the application process the shell is the process's own
``InteractiveShell.instance()`` -- there is no Dragonfly console to share
it with, and the ambient ORS auto-imports of the Dragonfly process do NOT
apply here: the seed is ``'%matplotlib inline'`` plus the bridge client
pushed as ``dfnb``.

Threading model:

* the form (GUI thread) calls :meth:`NotebookKernel.run_cell`,
  :meth:`request_stop`, :meth:`restart` -- all of which only emit command
  signals or set a flag;
* the worker thread owns the shell and does ALL the execution: lazy
  start, ``shell.run_cell``, restart (``shell.reset`` + re-seed), the
  stream writers, the display-publisher wrapper, the result formatting;
* outputs marshal back to the GUI thread as QUEUED Qt signals
  (``stream_arrived`` / ``display_arrived`` / ``result_arrived`` /
  ``error_arrived`` / ``run_finished``) -- a cell's prints grow on screen
  while it runs, and the authoritative output block is re-rendered from
  the returned outputs once the run finishes.

Stop and the heartbeat: because the cell runs on the worker thread, a
Python interrupt cannot be raised from the form's Stop slot -- so a
``sys.settrace`` line hook (``Heartbeat``) rides the cell's Python lines
on the worker thread and, at most once per ``HEARTBEAT_INTERVAL_S``,
checks ``stop_requested`` and arms a one-shot ``KeyboardInterrupt`` via
``PyThreadState_SetAsyncExc`` -- armed ONLY on the worker thread, never
inside a GUI-thread Qt slot (the arming raises at the next Python line of
the traced thread, which is the cell's own thread). The interrupt lands
at the next Python line. Costs, measured in rounds 5-6: ~7x on a tight
PURE-Python loop (3M iterations: 0.81 s bare -> 5.98 s traced),
negligible for numpy/ORS/plotting cells (C code emits no line events); a
cell stuck inside a single C extension call is interrupted when the call
returns (or escapes via Restart kernel). A dfnb bridge call blocks in
small ``wait`` slices on purpose, so the async interrupt lands between
slices and the client cancels the in-flight request before re-raising.

Restart wipes the worker's shell in place (``shell.reset(new_session=
True)`` -- an InProcess kernel created after a shutdown would silently
KEEP the namespace) and re-seeds.

Custom environments (round 16, the SUBPROCESS model): a kernel with an
environment selected runs its CELLS in a subprocess on the venv's OWN
interpreter (``dfnb_env_kernel``, the runner; ``notebook_envs`` owns the
whole feature), while this process keeps the embedded in-process shell
for the default and the GUI on the embedded Python no matter what is
selected. The two never mix imports: the in-process path overlay that
preceded this model put two site-packages into ONE interpreter, an
embedded extension loaded against the venv's fresh numpy and failed
its ABI checks, and the breakage SURVIVED switching back because a
loaded extension cannot be un-bound -- exactly what the process
boundary prevents. While a kernel runs on an environment the worker
holds its in-use lock (any second notebook window is refused by it,
and round 18's delete flow stops THIS window's kernel through
:meth:`shutdown` before removing the tree); :meth:`shutdown` is the
environment-switch seam: the child, the lock and the kernel go away,
the worker thread stays, and the next run lazy-starts on whatever
environment is selected by then. The child speaks JSON lines over its
stdin/stdout (streams, displays, results, errors, run completions), its
``dfnb`` calls relay back through the application's bridge client
(``DfnbClient.call`` is thread-safe from any thread), and Stop is a
control line the child's stdin watcher turns into the same
``PyThreadState_SetAsyncExc`` interrupt the embedded heartbeat uses.

Output contract: ``run_finished(execution_count, outputs)`` where
``outputs`` are nbformat-shaped dicts the document stores verbatim via
``NotebookDocument.record_execution``::

    {'output_type': 'stream',        'name': 'stdout'|'stderr', 'text': str}
    {'output_type': 'display_data',  'data': {mime: payload}, 'metadata': {}}
    {'output_type': 'execute_result','data': {mime: payload}, 'metadata': {},
                                     'execution_count': int}
    {'output_type': 'error',         'ename', 'evalue', 'traceback': [lines]}

Proven mechanics carried over from rounds 3-7 (the engine pins survive the
kernel move): the kernel starts WITHOUT ``kernel.gui`` (the seed's
``'%matplotlib inline'`` renders figures into base64 ``image/png``);
wrapping ``shell.display_pub.publish`` captures every ``display()`` (the
``image/png`` payload arrives already base64-encoded; ipykernel 6.29.5
dropped ``source`` from ``publish`` so it is swallowed, never forwarded);
the cell's result repr comes from ``shell.display_formatter.format``;
swapped ``sys.stdout``/``sys.stderr`` capture ``print``/writes; tracebacks
reach neither stream -- the engine formats errors itself;
``store_history=True`` makes ``result.execution_count`` count only
executed cells (the seed runs with ``store_history=False``).
"""

import os

# Same multiple-OpenMP-runtime workaround as every module in this project:
# the kernel transitively imports numpy, and this file is the engine's first
# line.
os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'True')

import base64  # noqa: E402
import ctypes  # noqa: E402
import json  # noqa: E402
import queue  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402

from PyQt6.QtCore import QObject, QThread, pyqtSignal  # noqa: E402

from . import dfnb_env_kernel as env_kernel  # noqa: E402
from .notebook_envs import (acquire_in_use,  # noqa: E402
                            ensure_runtime_dlls, popen as env_popen,
                            release_in_use, venv_python,
                            venv_site_packages)

#: The runner program the environment child executes (a plain file in
#: this package: the venv's interpreter runs it directly, so the child
#: never imports this package's modules).
_ENV_RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'dfnb_env_kernel.py')

_STREAM_FLUSH_SECONDS = 0.1   # at most one live flush per tenth of a second
_STREAM_FLUSH_CHARS = 8192    # ... unless this many characters piled up

# The heartbeat cadence: while a cell runs on the worker thread, the
# sys.settrace line hook checks the stop flag at most once every 50 ms (see
# Heartbeat). Measured cost of the tracer on a tight PURE-PYTHON loop:
# ~7x (a 3M-iteration probe loop: 0.81 s bare -> 5.98 s traced at this
# cadence); numpy/plotting cells are unaffected (C code emits no line
# events).
HEARTBEAT_INTERVAL_S = 0.05

# How long the environment child has to announce itself (the ``ready``
# line) before the lazy start fails: a venv whose interpreter is broken
# must surface as a clear error, never a first-cell hang.
_CHILD_START_TIMEOUT_S = 30.0

_ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*m')

_STDIN_MESSAGE = ('input() is not supported in notebook cells -- assign the '
                  'value in the editor or read it from a file; the notebook '
                  'kernel has no interactive stdin.')


def _suppress_ipconfig_spam():
    """Round 13's window-spam fix, part 2: ``jupyter_client`` resolves the
    connection info's ``ip`` default through
    ``jupyter_client.localinterfaces._load_ips``, whose ladder on Windows
    runs ``ipconfig`` as a SUBPROCESS before falling back to its own
    pure-socket loader -- and a subprocess of a console-less parent (the
    DETACHED notebook app; the CREATE_NO_WINDOW gate children) allocates
    a NEW VISIBLE console window: the live ``ipconfig`` flash at the first
    cell's run. The in-process kernel never opens a network socket (no
    zmq; the connection file is vestigial), so reroute the ladder's first
    rung to the library's OWN socket-based loader: same values, zero child
    processes. Called before the first kernel start; the ``hasattr``
    guard keeps it a silent no-op if a future jupyter_client drops the
    private name (the installed-suite pin fails LOUDLY in that case)."""
    from jupyter_client import localinterfaces
    if hasattr(localinterfaces, '_load_ips_ipconfig'):
        localinterfaces._load_ips_ipconfig = \
            localinterfaces._load_ips_gethostbyname


class _NoStdin:
    """The ``sys.stdin`` stub during a cell run: ``input()`` and reads raise
    a clear RuntimeError instead of silently blocking (the kernel has no
    interactive stdin -- blocking would hang the worker thread)."""

    def read(self, size=-1):
        raise RuntimeError(_STDIN_MESSAGE)

    def readline(self, size=-1):
        raise RuntimeError(_STDIN_MESSAGE)

    def readlines(self, hint=-1):
        raise RuntimeError(_STDIN_MESSAGE)

    def __iter__(self):
        raise RuntimeError(_STDIN_MESSAGE)

    def write(self, *args, **kwargs):
        raise RuntimeError(_STDIN_MESSAGE)

    def flush(self):
        pass

    def close(self):
        pass

    def isatty(self):
        return False

    encoding = 'utf-8'


class _CellStreamWriter:
    """Replaces ``sys.stdout``/``sys.stderr`` while a cell runs.

    Every write is buffered; a flush appends the buffered chunk to the
    cell's outputs (merged into the previous same-name stream output,
    nbformat style) and emits the kernel's ``stream_arrived`` signal --
    from the worker thread this is a QUEUED delivery to the GUI thread, so
    the form's output browser grows live while the cell runs. The flush
    rides on the throttle (at most one per ``_STREAM_FLUSH_SECONDS`` or
    ``_STREAM_FLUSH_CHARS``, whichever comes first), never per write.
    """

    def __init__(self, kernel, name):
        self._kernel = kernel
        self.name = name
        self._pending = []
        self._pending_chars = 0
        self._last_flush = time.monotonic()

    def write(self, text):
        if not text:
            return
        self._pending.append(text)
        self._pending_chars += len(text)
        now = time.monotonic()
        if (self._pending_chars >= _STREAM_FLUSH_CHARS
                or (now - self._last_flush) >= _STREAM_FLUSH_SECONDS):
            self.flush()

    def flush(self):
        text = ''.join(self._pending)
        if not text:
            return
        self._pending = []
        self._pending_chars = 0
        self._last_flush = time.monotonic()
        self._kernel._append_stream_output(self.name, text)


class Heartbeat:
    """The ``sys.settrace`` line hook that keeps a running cell
    interruptible.

    It fires on every Python line the cell executes on the WORKER thread
    (C extension calls emit no line events, which is exactly why
    numpy/plotting cells pay ~no tax and also why a cell stuck inside one
    is only interrupted when the call returns). On each 'line' event, at
    most once per ``HEARTBEAT_INTERVAL_S``, it checks
    ``kernel.stop_requested``: if set, it clears the flag and arms a
    one-shot ``KeyboardInterrupt`` on the running (worker) thread via
    ``PyThreadState_SetAsyncExc`` -- the exception lands at the next
    Python line. Round 8: there is NO pump in the hook any more -- the GUI
    thread is free (the cell no longer runs on it), so the old
    pump-then-check order is gone; the tracer is check-and-arm only. The
    arming happens ONLY on the worker thread -- never inside a GUI-thread
    Qt slot (the Stop button merely sets the flag).

    The tracer returns itself for every event, so it also becomes the local
    tracer of every frame the cell enters. It is installed immediately
    before ``shell.run_cell`` and removed (``sys.settrace(None)``) in the
    ``finally`` of the run -- NEVER outside a run: tracing the whole
    worker thread would tax every later line with the same pure-Python
    cost the cells pay.
    """

    def __init__(self, kernel):
        self._kernel = kernel

    def __call__(self, frame, event, arg):
        if event == 'line':
            now = time.monotonic()
            if (now - self._kernel._last_heartbeat) >= HEARTBEAT_INTERVAL_S:
                self._kernel._last_heartbeat = now
                self._kernel.heartbeats += 1
                if self._kernel.stop_requested:
                    self._kernel.stop_requested = False
                    ctypes.pythonapi.PyThreadState_SetAsyncExc(
                        threading.get_ident(),
                        ctypes.py_object(KeyboardInterrupt))
        return self


class _KernelWorker(QObject):
    """The worker-thread half of the kernel: the slots below run on the
    worker ``QThread`` (the kernel's command signals arrive here queued)
    and own the shell for their whole duration."""

    def __init__(self, kernel):
        super().__init__()
        self._kernel = kernel

    def _on_start_requested(self):
        try:
            self._kernel._ensure_started()
        except Exception as exc:
            self._kernel.run_failed.emit(
                'the kernel could not be started: {}'.format(exc))

    def _on_run_requested(self, code):
        kernel = self._kernel
        if kernel._running:
            kernel.run_failed.emit('a cell is already running in this '
                                   'notebook')
            return
        try:
            kernel._ensure_started()
        except Exception as exc:
            kernel.run_failed.emit(
                'the kernel could not be started: {}'.format(exc))
            return
        kernel._execute_on_worker(code)

    def _on_restart_requested(self):
        kernel = self._kernel
        if kernel._running:
            kernel.run_failed.emit('a cell is still running -- wait for it '
                                   'to finish before restarting the kernel')
            return
        if kernel._child is not None:
            # the child path: the runner wipes its own namespace and
            # resets its counter (the 'restarted' line answers)
            kernel._child_restarted.clear()
            try:
                kernel._child_write({'op': 'restart'})
            except Exception as exc:
                kernel.restart_finished.emit(
                    False, 'the environment kernel could not be restarted: '
                    '{}'.format(exc))
                return
            if kernel._child_restarted.wait(30.0):
                kernel.restart_finished.emit(True, '')
            else:
                kernel.restart_finished.emit(
                    False, 'the environment kernel did not restart within '
                    '30 s')
            return
        try:
            kernel._ensure_started()
            shell = kernel._manager.kernel.shell
            shell.reset(new_session=True)
            kernel._seed()
            kernel.restart_finished.emit(True, '')
        except Exception as exc:
            kernel.restart_finished.emit(
                False, 'the kernel could not be restarted: {}'.format(exc))

    def _on_shutdown_requested(self):
        """Best-effort teardown on the worker thread (channels first, then
        the kernel). Teardown problems are swallowed on purpose: a dirty
        shutdown must never take the application down with it. Round 16:
        the environment child (if any) quits and its in-use lock frees
        here, and ``kernel_stopped`` closes the seam -- the form
        re-enables the pip button and the next run lazy-starts on the
        environment selected by then. The branch decision reads the
        ``_child_mode`` flag, not ``_child`` alone: the child's death
        races this request (the pump's EOF teardown nulls ``_child``
        first), and a misread would run the embedded branch below against
        a child-mode kernel, stopping the form's shared bridge client."""
        kernel = self._kernel
        if kernel._child is not None or kernel._child_mode:
            try:
                kernel._child_write({'op': 'quit'})
            except Exception:
                pass
            kernel._teardown_child()
            kernel.kernel_stopped.emit()
            return
        env_dir = kernel._env_lock_dir
        kernel._env_lock_dir = None
        kernel._active_environment = None
        if env_dir is not None:
            try:
                release_in_use(env_dir)
            except Exception:
                pass
        if kernel._client is not None:
            try:
                kernel._client.stop_channels()
            except Exception:
                pass
            kernel._client = None
        manager = kernel._manager
        kernel._manager = None
        if manager is not None:
            try:
                manager.shutdown_kernel()
            except Exception:
                pass
        kernel.kernel_stopped.emit()


class NotebookKernel(QObject):
    """One notebook's kernel: the shell lives on ONE worker thread.

    Lifecycle: the form creates one at construction (cheap, starts
    NOTHING); the kernel starts lazily on the first :meth:`run_cell`;
    :meth:`restart` wipes the namespace in place and re-seeds;
    :meth:`stop` tears the worker thread down. Everything the form calls
    is thread-safe signal emission or a flag set; everything that touches
    the shell runs on the worker.
    """

    # ---- signals (GUI-thread receivers; emitted from the worker thread,
    #      which makes every delivery a queued one) ----
    kernel_started = pyqtSignal()
    kernel_stopped = pyqtSignal()
    stream_arrived = pyqtSignal(str, str)
    display_arrived = pyqtSignal(dict, dict)
    result_arrived = pyqtSignal(dict, dict)
    error_arrived = pyqtSignal(str, str, list)
    run_finished = pyqtSignal(int, list)
    run_failed = pyqtSignal(str)
    restart_finished = pyqtSignal(bool, str)

    # ---- commands (GUI -> worker, queued) ----
    _run_requested = pyqtSignal(str)
    _start_requested = pyqtSignal()
    _restart_requested = pyqtSignal()
    _shutdown_requested = pyqtSignal()

    def __init__(self, dfnb=None, environment=None, parent=None):
        super().__init__(parent)
        self._dfnb = dfnb  # the bridge client pushed into the seed
        self._manager = None
        self._client = None
        # Round 16: the venv directory the NEXT lazy start activates
        # (None = the embedded Python), the one the STARTED kernel runs
        # on, and the environment whose in-use lock the worker holds.
        self._environment = environment
        self._active_environment = None
        self._env_lock_dir = None
        # Round 16: True while the STARTED kernel is an environment
        # child (set at the spawn, cleared by the next embedded start).
        # The shutdown seam's branch decision reads THIS, not ``_child``
        # alone: the child's death races the shutdown request (the
        # pump's EOF teardown nulls ``_child`` first), and a misread
        # would run the embedded branch against a child-mode kernel --
        # stopping the form's shared bridge client and stranding the
        # reconnect timer (the staged round-16 tracebacks).
        self._child_mode = False
        # Round 16, the subprocess model: the environment child (None =
        # the embedded in-process kernel, or a stopped kernel). One
        # Popen plus three daemon threads (the stdout pump that turns
        # the child's JSON lines into the signals below, the stderr
        # drain kept for the crash report, and the dfnb relay that
        # performs the child's bridge calls on the application's
        # client), one lock around every stdin write (the worker's run
        # and stop lines, the relay's responses), and the events and
        # boxes the worker waits on per run.
        self._child = None
        self._child_pump = None
        self._child_stderr_drain = None
        self._child_relay = None
        self._child_relay_queue = None
        self._child_stdin_lock = threading.Lock()
        self._child_ready = threading.Event()
        self._child_restarted = threading.Event()
        self._child_run_done = threading.Event()
        self._child_run_result = None
        self._child_failure = None
        self._child_stderr_tail = []
        self._running = False
        self._current_outputs = None
        self._displayed_this_cell = False
        # The stop/heartbeat state: ``stop_requested`` is the ONLY thing
        # the form's Stop button touches (arming the interrupt from the
        # click slot would raise it inside Qt code -- fatal); the heartbeat
        # tracer reads it ON the worker thread. ``heartbeats`` counts tracer
        # ticks (the smoke's heartbeat pin reads it); ``_last_heartbeat``
        # throttles the tracer.
        self.stop_requested = False
        self.heartbeats = 0
        self._last_heartbeat = 0.0

        self._worker = _KernelWorker(self)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._run_requested.connect(self._worker._on_run_requested)
        self._start_requested.connect(self._worker._on_start_requested)
        self._restart_requested.connect(self._worker._on_restart_requested)
        self._shutdown_requested.connect(self._worker._on_shutdown_requested)
        self._thread.start()

    # ---- state -----------------------------------------------------------

    @property
    def is_started(self):
        return self._manager is not None or self._child is not None

    @property
    def is_running(self):
        return self._running

    @property
    def active_environment(self):
        """The venv directory the STARTED kernel runs on (None when the
        kernel is not started, or runs on the embedded Python). The
        form's delete flow reads this: a kernel running on the deleted
        environment is stopped first (round 18), and the pip tooltip
        names the hold the running kernel keeps."""
        return self._active_environment

    # ---- the form's API (GUI thread) ---------------------------------------

    def set_environment(self, venv_dir):
        """Selects the environment the NEXT lazy start runs on (a venv
        directory, or None for the embedded Python). GUI-thread safe:
        it only stores the value; the worker reads it in
        :meth:`_ensure_started`, and any queued shutdown runs before
        the next run request. For a kernel that is already started the
        form pairs this with :meth:`shutdown` (the switch seam)."""
        self._environment = venv_dir

    def run_cell(self, code):
        """Runs one cell on the worker thread (asynchronous: the command is
        a queued signal). The kernel starts lazily on the first run. The
        outputs arrive through the signals; ``run_finished(count, outputs)``
        carries the authoritative final block."""
        self._run_requested.emit(code)

    def request_stop(self):
        """The form's Stop affordance. The embedded path sets ONLY the
        flag the heartbeat tracer checks on the worker thread (the
        interrupt is armed by the tracer -- never from a GUI slot). The
        child path forwards the stop line to the environment child,
        whose stdin watcher arms the interrupt in the executing thread
        (the pipe write is tiny and the child consumes it immediately,
        so the GUI thread never blocks on it). The flag is not cleared
        at run start: a stop requested while the run is still queued
        survives into the run -- the embedded tracer fires it at the
        cell's first Python line, the child path forwards it as the
        stop line when the run starts -- and the only ways the flag is
        set outside a run are the form's Stop slot (which only fires
        for the RUNNING cell) and ``stop()`` (teardown, no further
        run). The child path's one blind spot: a stop clicked in the
        milliseconds between the child finishing its cell and this
        process reading the ``cell_done`` line latches the child's
        pending-stop flag, so the NEXT run interrupts before its code
        runs -- the latch is load-bearing for the queued-stop contract
        and the pump-latency window is accepted and documented on both
        sides of the pipe."""
        self.stop_requested = True
        if self._child is not None and self._running:
            try:
                self._child_write({'op': 'stop'})
            except Exception:
                pass  # the child is gone; its pump reports the death

    def restart(self):
        """Wipes the worker's shell in place and re-seeds. Refused while a
        cell runs (the worker reports it through ``run_failed``)."""
        self._restart_requested.emit()

    def shutdown(self):
        """Stops the kernel WITHOUT tearing the worker thread down
        (round 16, the environment-switch seam): the shell, the
        namespace and the active environment's in-use lock go away on
        the worker, ``kernel_stopped`` announces the switch, and the
        next :meth:`run_cell` lazy-starts a FRESH kernel on whatever
        environment is selected by then. A request that lands while a
        cell runs queues behind the run on the worker (the run's slot
        does not re-enter the event loop) and executes when it ends.
        Unlike :meth:`stop` this is a normal mid-session operation, not
        teardown."""
        self._shutdown_requested.emit()

    def stop(self):
        """Tears the worker thread down. Best-effort shutdown of the
        in-process kernel happens on the worker (queued); a stuck cell
        cannot be waited for forever, so the wait is bounded -- the
        process is on its way out anyway. The environment in-use lock
        does not need a wait here: the OS releases it when the process
        exits even if the queued release never runs. The environment
        CHILD cannot be left to the queued shutdown either: the worker
        may be wedged inside a run and a child process must never
        outlive the application, so its teardown runs directly and
        bounded here."""
        self.stop_requested = True
        self._shutdown_requested.emit()
        if self._child is not None:
            try:
                self._teardown_child(send_quit=True)
            except Exception:
                pass
        self._thread.quit()
        self._thread.wait(3000)

    # ---- worker-thread internals --------------------------------------------

    def _ensure_started(self):
        """Starts the kernel (idempotent). Runs on the worker thread.
        Round 16: an environment selected means the SUBPROCESS kernel
        (the runner on the venv's own interpreter); None means the
        embedded in-process shell, which the qtconsole in-process
        manager constructs HERE so the shell's whole life stays on one
        thread."""
        if self._environment is not None:
            self._ensure_child_started()
            return
        if self._manager is not None:
            return
        self._start_embedded()

    def _start_embedded(self):
        """Starts the embedded in-process kernel and seeds it (the
        manager and the shell live on the worker thread from the first
        line to teardown)."""
        from qtconsole.inprocess import QtInProcessKernelManager

        # Before the manager exists: the client property the code below
        # touches resolves the connection info's ip default through
        # localinterfaces._load_ips -- the ipconfig shell-out (see
        # _suppress_ipconfig_spam).
        _suppress_ipconfig_spam()

        manager = QtInProcessKernelManager()
        manager.start_kernel(show_banner=False)
        # The inline path: do NOT set kernel.gui -- '%matplotlib inline' in
        # the seed renders figures into base64 image/png payloads captured
        # through the wrapped display publisher.
        self._manager = manager
        self._child_mode = False
        self._client = manager.client()
        self._client.start_channels()
        self._wrap_display_publisher(manager.kernel.shell)
        self._seed()
        # Announced from the worker -- queued delivery to the GUI thread.
        self.kernel_started.emit()

    def _seed(self):
        """The seed: ``'%matplotlib inline'`` (figures capture as base64
        payloads) plus the bridge client pushed as ``dfnb``. The ambient
        ORS auto-imports of the Dragonfly process do NOT apply here --
        this process is ORS-free by design. Seed output is swallowed --
        starting a kernel must not paint junk into the first cell's output
        area."""
        import contextlib
        import io

        shell = self._manager.kernel.shell
        swallow = io.StringIO()
        with contextlib.redirect_stdout(swallow), \
                contextlib.redirect_stderr(swallow):
            shell.run_cell('%matplotlib inline',
                           store_history=False, silent=True)
        if self._dfnb is not None:
            shell.push({'dfnb': self._dfnb})

    def _execute_on_worker(self, code):
        """One cell run, synchronously ON the worker thread. Streams and
        displays emit as they happen (queued to the GUI thread); the final
        block goes out with ``run_finished``. The environment child has
        its own execution path (the events arrive as JSON lines from the
        child's pump threads) -- this method is the EMBEDDED engine."""
        if self._child is not None:
            self._execute_on_child(code)
            return
        shell = self._manager.kernel.shell

        self._current_outputs = []
        self._displayed_this_cell = False
        self._running = True

        saved_stdout, saved_stderr, saved_stdin = sys.stdout, sys.stderr, \
            sys.stdin
        stdout_writer = _CellStreamWriter(self, 'stdout')
        stderr_writer = _CellStreamWriter(self, 'stderr')
        sys.stdout = stdout_writer
        sys.stderr = stderr_writer
        sys.stdin = _NoStdin()
        # The stop flag is deliberately NOT cleared at run start. Runs are
        # asynchronous now, so a Stop click can land while the run request
        # is still queued or while the kernel is still lazy-starting -- that
        # request must SURVIVE into the run (the tracer arms the interrupt
        # at the cell's first Python line), not be wiped by the start. The
        # only ways the flag is set outside a run are the form's Stop slot
        # (which only fires for the RUNNING cell) and ``stop()`` (teardown,
        # no further run) -- the stale-flag case a start-clear used to be
        # trusted for cannot occur. The flag is cleared when the heartbeat
        # arms the interrupt and at every run end (the finally below).
        interrupted = False
        try:
            # The heartbeat lives EXACTLY for the duration of the cell run:
            # installed immediately before, removed in the finally, never
            # outside a run (tracing the whole worker thread would tax
            # everything it later runs).
            heartbeat = Heartbeat(self)
            sys.settrace(heartbeat)
            try:
                result = shell.run_cell(code, store_history=True)
            finally:
                sys.settrace(None)
        except KeyboardInterrupt:
            # A stop can be armed before the cell's code runs: a stop
            # requested while the run was still queued or while the kernel
            # was still lazy-starting leaves the flag set when the tracer
            # fires its FIRST line event -- and that event can be inside
            # ipykernel's own run_cell machinery, not the cell's code (the
            # compiled cell body runs from a temp file, but the prologue
            # around it is library Python). The interrupt then fires
            # inside IPython's machinery, where run_cell does NOT capture
            # it -- its handlers catch Exception, not BaseException -- and
            # it escapes here raw. Convert the escape into the
            # interrupted-cell outcome, the same shape a mid-run stop
            # produces (an error block, not an engine failure): partial
            # outputs are kept, one error output is synthesized and
            # ``run_finished`` closes the run. Building it AFTER the
            # finally below keeps the writers' final flushes in the
            # outputs. The shell survives: IPython tolerates a
            # KeyboardInterrupt at any point of run_cell (SIGINT does
            # exactly this on the main thread).
            interrupted = True
        except BaseException as exc:
            # an engine-level failure (a shell that blew up around the
            # cell): report it, never crash the worker
            self._running = False
            self._current_outputs = None
            self.run_failed.emit('the cell could not be executed: '
                                 '{}'.format(exc))
            return
        finally:
            sys.stdout, sys.stderr, sys.stdin = saved_stdout, saved_stderr, \
                saved_stdin
            # The final flushes still emit: the throttle may have left a
            # tail buffered when the cell finished quickly.
            stdout_writer.flush()
            stderr_writer.flush()
            self.stop_requested = False
        if interrupted:
            self._running = False
            outputs = self._current_outputs
            self._current_outputs = None
            if outputs is None:
                outputs = []
            outputs.append({'output_type': 'error',
                            'ename': 'KeyboardInterrupt',
                            'evalue': '',
                            'traceback': ['KeyboardInterrupt']})
            self.error_arrived.emit('KeyboardInterrupt', '',
                                    ['KeyboardInterrupt'])
            self.run_finished.emit(int(shell.execution_count or 0), outputs)
            return
        outputs = self._current_outputs
        self._current_outputs = None
        self._running = False

        count = result.execution_count

        if result.result is not None and not self._displayed_this_cell:
            data, metadata = self._format_result(shell, result.result)
            if data:
                outputs.append({'output_type': 'execute_result',
                                'data': data,
                                'metadata': metadata,
                                'execution_count': count})
                self.result_arrived.emit(data, metadata)

        if not result.success:
            ename, evalue, tb_lines = self._format_error(result)
            outputs.append({'output_type': 'error',
                            'ename': ename,
                            'evalue': evalue,
                            'traceback': tb_lines})
            self.error_arrived.emit(ename, evalue, tb_lines)

        self.run_finished.emit(int(count or 0), outputs)

    def _wrap_display_publisher(self, shell):
        kernel_self = self
        original_publish = shell.display_pub.publish

        def publish(data, metadata=None, source=None, **kwargs):
            kernel_self._on_display_published(data, metadata)
            # ipykernel 6.29.5 dropped `source` from publish (zmqshell.py:
            # publish(data, metadata=None, transient=None, update=False));
            # IPython 8 keeps the parameter only as deprecated-and-unused,
            # so it is swallowed here and never forwarded.
            return original_publish(data, metadata=metadata, **kwargs)

        shell.display_pub.publish = publish

    def _on_display_published(self, data, metadata):
        self._displayed_this_cell = True
        normalized = {}
        for mime, payload in dict(data or {}).items():
            if isinstance(payload, bytes):
                # nbformat stores image/png base64-encoded; the inline
                # backend normally delivers the str already encoded, but
                # bytes pass through too -- encode them here.
                payload = base64.b64encode(payload).decode('ascii')
            normalized[mime] = payload
        outputs = self._current_outputs
        if outputs is None:
            return  # a display outside a cell run (e.g. from a magic) -- ignore
        entry_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        outputs.append({'output_type': 'display_data',
                        'data': normalized,
                        'metadata': entry_metadata})
        self.display_arrived.emit(normalized, entry_metadata)

    def _append_stream_output(self, name, text):
        outputs = self._current_outputs
        if outputs is None:
            return
        previous = outputs[-1] if outputs else None
        if (previous is not None
                and previous.get('output_type') == 'stream'
                and previous.get('name') == name):
            previous['text'] = previous['text'] + text
        else:
            outputs.append({'output_type': 'stream', 'name': name,
                            'text': text})
        self.stream_arrived.emit(name, text)

    @staticmethod
    def _format_result(shell, value):
        try:
            data, metadata = shell.display_formatter.format(value)
        except Exception:
            data, metadata = {'text/plain': repr(value)}, {}
        return data, metadata

    @staticmethod
    def _format_error(result):
        """Structured error fields straight from the run result: tracebacks
        reach neither swapped stream, so the engine formats them itself
        (plain text, no ANSI -- IPython is bypassed)."""
        exc = result.error_in_exec or result.error_before_exec
        if isinstance(exc, BaseException):
            ename = type(exc).__name__
            evalue = str(exc)
            lines = traceback.format_exception(type(exc), exc,
                                               exc.__traceback__)
        else:
            ename, evalue = 'Error', repr(exc)
            lines = [repr(exc)]
        flattened = []
        for chunk in lines:
            flattened.extend(
                _ANSI_ESCAPE.sub('', chunk).rstrip('\n').split('\n'))
        return ename, evalue, flattened

    # ---- the environment child (round 16, the subprocess kernel) ----------

    def _ensure_child_started(self):
        """Spawns the environment kernel: the venv's OWN interpreter
        running this package's runner, with its in-use lock taken FIRST
        (a held environment refuses to start: another notebook window
        runs on it) and released again when the spawn itself fails. The
        child must announce itself (the ``ready`` line) within
        ``_CHILD_START_TIMEOUT_S``: a venv whose interpreter is broken
        surfaces as a clear start error, never a first-cell hang. Runs
        on the worker thread; the GUI thread stays free."""
        if self._child is not None:
            return
        env_dir = self._environment
        acquire_in_use(env_dir)
        try:
            interpreter = venv_python(env_dir)
            if not os.path.isfile(interpreter) or \
                    not os.path.isdir(venv_site_packages(env_dir)):
                raise ValueError(
                    "The environment '{}' is missing its interpreter or "
                    'site-packages (it may have been deleted). Pick '
                    'another environment, or create it again.'.format(
                        os.path.basename(env_dir)))
            # round 17: the scan-discovered runtime DLLs into the venv
            # (idempotent; the misparked set this distribution parks
            # outside the base tree -- on this machine that heals
            # sqlite3 AND tkinter) so the child's base extension
            # modules load -- the runner arms this directory for the
            # dependent load at its startup (the two pieces are both
            # required, probe-proven); environments created before the
            # round heal here on their next start
            ensure_runtime_dlls(env_dir)
            self._child = env_popen(
                [interpreter, _ENV_RUNNER],
                stdin=subprocess.PIPE, merge_stderr=False)
        except Exception:
            self._child = None
            try:
                release_in_use(env_dir)
            except Exception:
                pass
            raise
        self._child_mode = True
        self._active_environment = env_dir
        self._env_lock_dir = env_dir
        self._child_ready.clear()
        self._child_restarted.clear()
        self._child_failure = None
        self._child_run_result = None
        self._child_stderr_tail = []
        self._child_pump = threading.Thread(
            target=self._child_pump_loop, daemon=True)
        self._child_pump.start()
        self._child_stderr_drain = threading.Thread(
            target=self._child_stderr_loop, args=(self._child,),
            daemon=True)
        self._child_stderr_drain.start()
        self._child_relay_queue = queue.Queue()
        self._child_relay = threading.Thread(
            target=self._child_relay_loop, daemon=True)
        self._child_relay.start()
        if not self._child_ready.wait(_CHILD_START_TIMEOUT_S):
            self._teardown_child()
            raise RuntimeError(
                'the environment kernel did not start within {} s (the '
                'environment may be broken; try the embedded '
                'Python)'.format(_CHILD_START_TIMEOUT_S))
        self.kernel_started.emit()

    def _teardown_child(self, send_quit=False):
        """Ends the environment child: a best-effort quit line, a
        bounded wait, an EOF fallback (stdin closed -- the runner's own
        ``_parent_gone`` exit, ``os._exit(0)``), a kill if the child
        ignores both, the pipes closed, the relay queue released (the
        daemon threads wind down on their own), the in-use lock freed,
        and a run still in flight failed so the waiting worker unwedges.
        Never raises: every teardown path is best-effort by design.
        Round 18 review P1: the quit line rides the CAPTURED child
        stdin, written BEFORE ``self._child`` is nulled -- it used to go
        through ``_child_write`` AFTER the nulling, which raised 'the
        environment kernel is not running' into the bare except, so
        ``stop()``'s quit was never sent and every window close with a
        live environment child waited out the bounded waits and
        hard-killed the process (exit 1) instead of the documented clean
        quit. Does NOT emit ``kernel_stopped`` -- the callers own that
        announcement."""
        child = self._child
        if child is None:
            return
        if send_quit:
            try:
                if child.stdin is not None:
                    data = json.dumps({'op': 'quit'}) + '\n'
                    with self._child_stdin_lock:
                        child.stdin.write(data)
                        child.stdin.flush()
            except Exception:
                pass
        self._child = None
        try:
            try:
                child.wait(2)
            except Exception:
                pass
            if child.poll() is None:
                # the child ignored quit: closing stdin is the runner's
                # own clean exit (its stdin watcher sees EOF, unwedges
                # any run in flight and os._exit(0)s) -- preferred over
                # a hard kill, which lands only if BOTH signals fail
                try:
                    if child.stdin is not None:
                        child.stdin.close()
                except Exception:
                    pass
                try:
                    child.wait(2)
                except Exception:
                    pass
            if child.poll() is None:
                try:
                    child.kill()
                except Exception:
                    pass
                try:
                    child.wait(2)
                except Exception:
                    pass
        except Exception:
            pass
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
        relay_queue = self._child_relay_queue
        if relay_queue is not None:
            try:
                relay_queue.put_nowait(None)
            except Exception:
                pass
        self._child_pump = None
        self._child_stderr_drain = None
        self._child_relay = None
        env_dir = self._env_lock_dir
        self._env_lock_dir = None
        self._active_environment = None
        if env_dir is not None:
            try:
                release_in_use(env_dir)
            except Exception:
                pass
        if self._running:
            exit_code = child.returncode
            tail = ' | '.join(self._child_stderr_tail[-3:])
            reason = ('the environment kernel exited unexpectedly while '
                      'the cell ran (exit {})'.format(
                          exit_code if exit_code is not None else 'unknown'))
            if tail:
                reason += ': ' + tail
            self._child_failure = reason
            self._child_run_done.set()

    def _child_write(self, obj):
        """One control or response line to the child's stdin: small
        lines, one lock, an explicit flush (the child's watcher thread
        consumes them immediately, so a write never blocks its caller
        meaningfully)."""
        child = self._child
        if child is None or child.stdin is None:
            raise RuntimeError('the environment kernel is not running')
        data = json.dumps(obj) + '\n'
        with self._child_stdin_lock:
            child.stdin.write(data)
            child.stdin.flush()

    def _child_pump_loop(self):
        """The child's stdout reader: every JSON line becomes the
        kernel's existing output path (``_append_stream_output`` and
        friends -- the same appends and queued signal emissions the
        embedded engine uses), and a dfnb request goes to the relay
        queue. EOF means the child is gone; ``_child_died`` owns the
        teardown and the failure report."""
        child = self._child
        try:
            for line in child.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    # not JSON on this pipe: most often a cell's own
                    # grandchild writing straight to the inherited
                    # stdout (a shell-out that bypassed the ``!``
                    # helper). Relay it as plain stream text so the
                    # user sees it instead of the pipe silently eating
                    # it; the protocol resumes at the next JSON line.
                    self._append_stream_output('stdout', line + '\n')
                    continue
                try:
                    self._child_handle(message)
                except Exception:
                    pass  # one bad event must never kill the pump
        except Exception:
            pass
        self._child_died()

    def _child_handle(self, message):
        """One decoded child event, threaded exactly like the embedded
        engine's equivalents (append into ``_current_outputs``, emit the
        queued signal, so the form's output browser grows live while
        the cell works)."""
        op = message.get('op')
        if op == 'ready':
            self._child_ready.set()
        elif op == 'restarted':
            self._child_restarted.set()
        elif op == 'stream':
            name = message.get('name')
            self._append_stream_output(
                name if name in ('stdout', 'stderr') else 'stdout',
                str(message.get('text') or ''))
        elif op == 'display':
            self._displayed_this_cell = True
            normalized = {}
            for mime, payload in dict(message.get('data') or {}).items():
                if isinstance(payload, bytes):
                    payload = base64.b64encode(payload).decode('ascii')
                normalized[str(mime)] = payload
            metadata = message.get('metadata')
            metadata = dict(metadata) if isinstance(metadata, dict) else {}
            outputs = self._current_outputs
            if outputs is None:
                return  # a display outside a run: ignored, as embedded
            outputs.append({'output_type': 'display_data',
                            'data': normalized, 'metadata': metadata})
            self.display_arrived.emit(normalized, metadata)
        elif op == 'result':
            data = dict(message.get('data') or {})
            metadata = message.get('metadata')
            metadata = dict(metadata) if isinstance(metadata, dict) else {}
            outputs = self._current_outputs
            if outputs is None:
                return
            count = int(message.get('count') or 0)
            outputs.append({'output_type': 'execute_result', 'data': data,
                            'metadata': metadata,
                            'execution_count': count})
            self.result_arrived.emit(data, metadata)
        elif op == 'error':
            ename = str(message.get('ename') or 'Error')
            evalue = str(message.get('evalue') or '')
            traceback_lines = [str(line) for line in
                               (message.get('traceback') or [])]
            outputs = self._current_outputs
            if outputs is None:
                return
            outputs.append({'output_type': 'error', 'ename': ename,
                            'evalue': evalue, 'traceback': traceback_lines})
            self.error_arrived.emit(ename, evalue, traceback_lines)
        elif op == 'cell_done':
            outputs = self._current_outputs or []
            self._current_outputs = None
            self._running = False
            self._child_run_result = (int(message.get('count') or 0),
                                      outputs)
            self._child_run_done.set()
        elif op == 'dfnb_request':
            relay_queue = self._child_relay_queue
            if relay_queue is not None:
                relay_queue.put(message)

    def _child_stderr_loop(self, child):
        """Drains the child's stderr (the runner's own crash text; the
        JSON channel is stdout) into a bounded tail kept for the death
        report."""
        try:
            for line in child.stderr:
                tail = self._child_stderr_tail
                tail.append(line.rstrip('\r\n'))
                if len(tail) > 50:
                    del tail[:-50]
        except Exception:
            pass

    def _child_died(self):
        """EOF on the child's stdout: the child is gone (an exit or a
        crash). The pump threads have stopped reading; the teardown owns
        everything (including the failure report when a run was in
        flight) and the ``kernel_stopped`` announcement makes the form
        re-evaluate the environment controls."""
        child = self._child
        if child is None:
            return  # an ordered teardown already owned the cleanup
        try:
            child.wait(2)  # let the exit code land before the report
        except Exception:
            pass
        self._teardown_child()
        self.kernel_stopped.emit()

    def _child_relay_loop(self):
        """The child's dfnb calls, one at a time (cells run
        sequentially, so at most one call is ever in flight): each
        request is performed on the application's bridge client -- its
        ``call`` marshals to the client's owner thread, so the ORS work
        still happens exactly where it always did -- and the answer (or
        the error envelope) goes back as one response line. A Stop
        during an in-flight call interrupts only the CHILD side (the
        cell dies; the response is discarded by its sequence number):
        this thread has no interrupt to take, so the bridge transaction
        itself runs to completion -- a long dfnb import keeps Dragonfly
        busy for its natural duration even after Stop returned, a
        contract change from the embedded kernel, whose worker thread
        takes the client's interrupt path and sends a cancel frame."""
        while True:
            message = self._child_relay_queue.get()
            if message is None:
                return
            try:
                self._child_relay_one(message)
            except Exception:
                pass  # a response that never left surfaces through the
                # child's own channels or its death

    def _child_relay_one(self, message):
        """One dfnb request: decode the arguments (ndarrays and
        DataFrames materialize in the EMBEDDED numpy / pandas -- the
        bytes cross, the objects never do), run the whitelisted method
        on the application's client, and encode the result back."""
        seq = message.get('seq')
        method = str(message.get('method') or '')
        try:
            if method not in env_kernel.DFNB_METHODS:
                raise RuntimeError(
                    'dfnb has no method {!r}.'.format(method))
            if self._dfnb is None:
                raise RuntimeError('the Dragonfly bridge client is not '
                                   'available in this notebook.')
            args = [env_kernel.decode_value(arg)
                    for arg in (message.get('args') or [])]
            kwargs = {str(key): env_kernel.decode_value(item)
                      for key, item in
                      (message.get('kwargs') or {}).items()}
            result = getattr(self._dfnb, method)(*args, **kwargs)
            self._child_write({'op': 'dfnb_response', 'seq': seq,
                               'ok': True,
                               'result': env_kernel.encode_value(result)})
        except BaseException as exc:  # the child decides how it surfaces
            try:
                lines = [line.rstrip('\n') for line in
                         traceback.format_exception(type(exc), exc,
                                                   exc.__traceback__)]
                self._child_write({
                    'op': 'dfnb_response', 'seq': seq, 'ok': False,
                    'error': {'type': type(exc).__name__,
                              'message': str(exc),
                              'traceback': lines[-8:]}})
            except Exception:
                pass

    def _execute_on_child(self, code):
        """One cell on the environment child, synchronously on the
        worker thread: the run line goes out, the pump threads feed the
        signals while the cell works, and the worker wakes at
        ``cell_done`` (or at the child's death) to close the run. A
        stop that was requested while this run was still queued is
        forwarded as the stop line at run start -- the child interrupts
        the cell before its code runs, the queued-stop contract of the
        embedded engine."""
        self._current_outputs = []
        self._displayed_this_cell = False
        self._running = True
        self._child_failure = None
        self._child_run_result = None
        self._child_run_done.clear()
        try:
            self._child_write({'op': 'run', 'code': code})
        except Exception as exc:
            self._running = False
            self._current_outputs = None
            self.run_failed.emit(
                'the cell could not be started on the environment '
                'kernel: {}'.format(exc))
            return
        if self.stop_requested:
            self.stop_requested = False
            try:
                self._child_write({'op': 'stop'})
            except Exception:
                pass
        self._child_run_done.wait()
        self.stop_requested = False
        failure = self._child_failure
        self._child_failure = None
        result = self._child_run_result
        self._child_run_result = None
        if failure is not None:
            self._running = False
            self._current_outputs = None
            self.run_failed.emit(failure)
            return
        count, outputs = result
        self.run_finished.emit(count, outputs)
