r"""The notebook application's IPython kernel engine (round 8).

Since round 8 the kernel runs in the STANDALONE notebook application
process, on ONE dedicated worker ``QThread`` — not on the GUI thread, and
not inside Dragonfly at all. That thread model is what finally closes the
crash class rounds 5-7 chased: a cell can no longer block or re-enter
Dragonfly's GUI thread because it never touches it; the GUI thread stays
free (its event loop keeps running — repainting, timers, user input) while
a cell runs, and ORS object operations stay on Dragonfly's own GUI thread
behind the bridge. The round-6 scoped pump, the round-7 progress dialog
and the lib's run-depth guard are all GONE: nothing here pumps events,
because the GUI thread is never blocked in the first place.

Engine: the same ``QtInProcessKernelManager`` setup the shipped Python
console uses, constructed LAZILY on the worker thread on the first cell
run (importing this module must stay cheap and must never start a
kernel). In the application process the shell is the process's own
``InteractiveShell.instance()`` — there is no Dragonfly console to share
it with, and the ambient ORS auto-imports of the Dragonfly process do NOT
apply here: the seed is ``'%matplotlib inline'`` plus the bridge client
pushed as ``dfnb``.

Threading model:

* the form (GUI thread) calls :meth:`NotebookKernel.run_cell`,
  :meth:`request_stop`, :meth:`restart` — all of which only emit command
  signals or set a flag;
* the worker thread owns the shell and does ALL the execution: lazy
  start, ``shell.run_cell``, restart (``shell.reset`` + re-seed), the
  stream writers, the display-publisher wrapper, the result formatting;
* outputs marshal back to the GUI thread as QUEUED Qt signals
  (``stream_arrived`` / ``display_arrived`` / ``result_arrived`` /
  ``error_arrived`` / ``run_finished``) — a cell's prints grow on screen
  while it runs, and the authoritative output block is re-rendered from
  the returned outputs once the run finishes.

Stop and the heartbeat: because the cell runs on the worker thread, a
Python interrupt cannot be raised from the form's Stop slot — so a
``sys.settrace`` line hook (``Heartbeat``) rides the cell's Python lines
on the worker thread and, at most once per ``HEARTBEAT_INTERVAL_S``,
checks ``stop_requested`` and arms a one-shot ``KeyboardInterrupt`` via
``PyThreadState_SetAsyncExc`` — armed ONLY on the worker thread, never
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
True)`` — an InProcess kernel created after a shutdown would silently
KEEP the namespace) and re-seeds.

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
reach neither stream — the engine formats errors itself;
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
import re  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402

from PyQt6.QtCore import QObject, QThread, pyqtSignal  # noqa: E402

_STREAM_FLUSH_SECONDS = 0.1   # at most one live flush per tenth of a second
_STREAM_FLUSH_CHARS = 8192    # ... unless this many characters piled up

# The heartbeat cadence: while a cell runs on the worker thread, the
# sys.settrace line hook checks the stop flag at most once every 50 ms (see
# Heartbeat). Measured cost of the tracer on a tight PURE-PYTHON loop:
# ~7x (a 3M-iteration probe loop: 0.81 s bare -> 5.98 s traced at this
# cadence); numpy/plotting cells are unaffected (C code emits no line
# events).
HEARTBEAT_INTERVAL_S = 0.05

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
                'the IPython kernel could not be started: {}'.format(exc))

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
                'the IPython kernel could not be started: {}'.format(exc))
            return
        kernel._execute_on_worker(code)

    def _on_restart_requested(self):
        kernel = self._kernel
        if kernel._running:
            kernel.run_failed.emit('a cell is still running -- wait for it '
                                   'to finish before restarting the kernel')
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
        shutdown must never take the application down with it."""
        kernel = self._kernel
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

    def __init__(self, dfnb=None, parent=None):
        super().__init__(parent)
        self._dfnb = dfnb  # the bridge client pushed into the seed
        self._manager = None
        self._client = None
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
        return self._manager is not None

    @property
    def is_running(self):
        return self._running

    # ---- the form's API (GUI thread) ---------------------------------------

    def run_cell(self, code):
        """Runs one cell on the worker thread (asynchronous: the command is
        a queued signal). The kernel starts lazily on the first run. The
        outputs arrive through the signals; ``run_finished(count, outputs)``
        carries the authoritative final block."""
        self._run_requested.emit(code)

    def request_stop(self):
        """The form's Stop affordance: sets ONLY the flag the heartbeat
        tracer checks on the worker thread. The interrupt is armed by the
        tracer -- never from a GUI slot. The flag is not cleared at run
        start: a stop requested while the run is still queued (the first
        cell lazy-starting the kernel) survives into the run and
        interrupts it at its first Python line."""
        self.stop_requested = True

    def restart(self):
        """Wipes the worker's shell in place and re-seeds. Refused while a
        cell runs (the worker reports it through ``run_failed``)."""
        self._restart_requested.emit()

    def stop(self):
        """Tears the worker thread down. Best-effort shutdown of the
        in-process kernel happens on the worker (queued); a stuck cell
        cannot be waited for forever, so the wait is bounded -- the
        process is on its way out anyway."""
        self.stop_requested = True
        self._shutdown_requested.emit()
        self._thread.quit()
        self._thread.wait(3000)

    # ---- worker-thread internals --------------------------------------------

    def _ensure_started(self):
        """Starts the in-process kernel and seeds it (idempotent). Runs on
        the worker thread; the qtconsole in-process manager constructs the
        shell HERE, so the shell's whole life stays on one thread."""
        if self._manager is not None:
            return
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
        block goes out with ``run_finished``."""
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
