r"""The custom-environment kernel runner (round 16, the SUBPROCESS model).

A notebook whose selected environment is a custom venv executes its CELLS
in a subprocess on the venv's OWN interpreter, and THIS file is the program
that runs there. The notebook application (GUI, embedded IPython kernel,
bridge client) stays on Dragonfly's embedded Python no matter what is
selected; only the custom-environment cells cross into the child.

Why a subprocess at all. Round 16 first shipped activation INSIDE the
application process: the venv's site-packages went to the front of
``sys.path`` with the embedded distribution's site-packages still on it.
That mixes two package trees in ONE interpreter, and the mix is not
survivable: a package the embedded distribution ships (say scikit-image,
compiled against the embedded numpy) binds fine until a pip install puts
a NEW numpy into the venv, at which point the embedded extension loads
against the venv's numpy and fails its ABI checks; C extensions cannot be
un-bound once loaded, so switching the environment back to the embedded
Python did not heal the process (the broken modules stayed imported:
skimage failed first, then the yfinance import, and the breakage
persisted after the switch back). The venv therefore gets its
OWN interpreter with its OWN ``sys.path``: its site-packages plus the
embedded distribution's STDLIB through ``pyvenv.cfg``'s ``home`` (the
venv Python the ``venv`` module created guarantees exactly that), and
nothing of the embedded site-packages ever reaches it. A pip install
into the environment touches exactly its own tree; the embedded
distribution is never upgraded, never poisoned, and both sides keep
their one ABI-coherent numpy copy per process.

The wire protocol (stdin/stdout, one JSON object per line, UTF-8, and
ASCII-only through ``json.dumps`` so no pipe encoding can ever mangle
it):

parent -> child (control lines)::

    {"op": "run", "code": str}     run one cell (the cell's source)
    {"op": "stop"}                 arm a KeyboardInterrupt in the cell
    {"op": "restart"}              wipe the namespace, reset the counter
    {"op": "quit"}                 exit cleanly
    {"op": "dfnb_response", "seq": int, "ok": bool, "result": ...,
     "error": {"type", "message", "traceback"} | None}
                                   one answered dfnb call

child -> parent (event lines)::

    {"op": "ready"}                       startup handshake
    {"op": "stream", "name", "text"}      stdout / stderr text
    {"op": "display", "data", "metadata"} a display() / matplotlib figure
    {"op": "result", "data", "metadata", "count"}   the last expression
    {"op": "error", "ename", "evalue", "traceback"}
    {"op": "cell_done", "count", "interrupted"}
    {"op": "restarted"}
    {"op": "dfnb_request", "seq", "method", "args", "kwargs"}

The dfnb relay. The venv child must never import the application's
modules (``dfnb_client`` pulls PyQt6; the ORS tree is not on its path),
but its cells still need ``dfnb``. The shim below exposes exactly the
eleven bridge functions as plain functions: each one encodes its
arguments (see the codec), writes one ``dfnb_request`` line, and blocks
until the matching ``dfnb_response`` arrives. The parent side
(``notebook_kernel``) performs the real call on the application's bridge
client (whose ``call`` is safe from any thread: it marshals to the GUI
thread and waits in slices) and writes the response back, so the ORS
work still happens on the Dragonfly side exactly as before.

The codec (:func:`encode_value` / :func:`decode_value`) is the bridge
protocol's value model restated for JSON lines: an ndarray crosses as
``numpy.save`` bytes, base64 in the marker (pickle-free); a DataFrame
crosses column by column (object-dtyped columns as plain lists, the
pickle-free fallback) with its index; bytes, scalars, lists and str-keyed
dicts stay JSON. NOTHING crosses by reference and no pickle is involved,
which is the other half of the isolation: the venv's numpy and the
application's numpy never meet, only their bytes.

Stop. ``stop`` is handled by the stdin-watcher thread, which owns every
stdin read: mid-cell it arms a one-shot ``KeyboardInterrupt`` on the
executing thread through ``PyThreadState_SetAsyncExc`` (the same
mechanism the embedded kernel's heartbeat uses, minus the tracer: a
dedicated reader thread sees the line instead); between cells it sets a
pending flag the next run consumes (a stop that lands while the run is
still queued must still stop the cell). The interrupt reaches the cell
at its next Python line; a cell stuck inside one C extension call is
interrupted when the call returns (the same contract as the embedded
kernel).

Shell escapes, and pip belongs to the button (round 17, restated).
A notebook cell expects IPython's ``!command`` system escape; the
embedded kernel has IPython for it, and this runner restates the ``!``
half: ``!command`` lines rewrite to an internal helper that runs the
command in a no-window child with CAPTURED pipes (a grandchild
inheriting this process's stdout would write into the JSON channel
itself) and devnull stdin (the channel's stdin stays the watcher's
alone), streaming what it prints through the cell's streams. A
``python``/``python3`` first word reroutes to THIS interpreter, because
the venv's Scripts directory is not on PATH in a notebook child, so a
bare ``python`` would find another interpreter or none at all. PIP DOES
NOT RUN FROM CELLS (the round-17 rule: the toolbar's pip button -- one
field taking the FULL pip command -- is the one pip interface), so a
``pip``/``pip3`` first word and a ``python -m pip`` prefix are refused
with a single visible note naming the button and nothing spawns.
``%`` magics are not supported here at all (``%pip`` included); the
explanatory error names the embedded IPython kernel for them. The
rewrite only happens when plain parsing fails, so a ``!`` inside a
string literal stays the string it is.

The stdin watcher POLLS on Windows (round 17, probe-proven): a thread
blocked in a synchronous ReadFile on the stdin pipe DEADLOCKS the first
DLL load of an OpenBLAS-linked extension (numpy, scipy, torch) on
another thread -- ``import numpy`` beside a blocking stdin reader hung
6/6 in the child (stuck inside LoadLibrary of numpy's
_multiarray_umath.pyd, which is where a yfinance cell froze for the
user), while beside a PeekNamedPipe-polling reader it imports cleanly; a
sleeping second thread and a second pipe's reader are both harmless,
and OPENBLAS_NUM_THREADS=1 does not help (the interaction is the
pending read on the console-less child's stdin handle, not OpenBLAS's
pool). The Windows watcher therefore never blocks in a read: it peeks
the pipe and reads only bytes that are already there, trading at most
50 ms of stop latency for a child that can import numpy at all. POSIX
keeps the blocking read: the loader involved is the Windows one.

The child arms its OWN exe directory (round 17, probe-proven too): the
base tree's extension modules load with the altered search path, whose
dependency search starts at each pyd's own directory and never reaches
the venv's ``Scripts``, so the runtime DLLs the parent copies there
stay invisible to that load without an explicit ``os.add_dll_directory``
on the exe directory. The parent's copy is scan-driven, not name-keyed
(round 17's follow-up): it discovers from the base
tree's own import tables whatever this distribution parks outside the
directories a venv child can reach -- on this machine that set heals
``sqlite3`` AND ``tkinter`` alike. The arm grows the child's DLL search
by exactly the one directory whose contents this package deliberately
controls; Dragonfly's library trees still never leak into it (the
host-only block below stays child-free on purpose).

This file is PURE STDLIB at import (numpy, pandas and matplotlib bind
lazily, inside the codec calls and the figure flush, so an environment
without numpy still starts and runs plain cells), it is BOTH the child
program (``venv_python dfnb_env_kernel.py``) and a package module (the
parent imports it for the shared codec and the method whitelist), and it
must stay importable by the framework-free tier-1 suite.
"""

import ast
import base64
import ctypes
import io
import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import time
import traceback
import types

try:
    import msvcrt  # Windows-only; the polling stdin watcher needs it
except ImportError:
    msvcrt = None

# Same multiple-OpenMP-runtime workaround as every module in this project:
# set before the codec's lazy numpy import in the venv child.
os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'True')

# The embedded-interpreter rule (the bridge protocol's): numpy's shared
# libs live in Dragonfly's two directories, and this module's codec binds
# numpy LAZILY, so a host that imports this module on the embedded
# interpreter needs the registration before that import. The environment
# child never runs this block: it executes this file as __main__ on the
# VENV's interpreter, whose numpy resolves its DLLs from its own tree,
# and Dragonfly's library directories must not leak into its DLL search.
if __name__ != '__main__' and os.name == 'nt':
    _dll_dir_handles = []
    for _dir in (r'C:\Program Files\Dragonfly\libs',
                 r'C:\Program Files\Dragonfly\Python_env\Library\bin'):
        if os.path.isdir(_dir):
            try:
                _dll_dir_handles.append(os.add_dll_directory(_dir))
            except OSError:
                pass

# Round 17: the CHILD arms its own exe directory (the venv's Scripts)
# for dependent-DLL loads. A base-tree pyd's altered search path starts
# at the pyd's own directory and never reaches the exe dir (probe-proven
# on a real venv: the copied dll loads by absolute path, the plain
# 'import sqlite3' does not, and with this arm it imports as 3.40.0),
# so without the arm the DLLs the parent copies next to the child's
# python.exe (the scan-discovered misparked set, whatever this
# distribution parks outside the base tree) are invisible to that
# load. Hosts import this module too (the tier-1 suite); for them the
# arm only duplicates a directory their own loader already searches.
_OWN_DLL_DIR = None
if os.name == 'nt':
    _own = os.path.dirname(os.path.abspath(sys.executable))
    try:
        os.add_dll_directory(_own)
        _OWN_DLL_DIR = _own
    except (OSError, ValueError):
        pass

#: The dfnb methods the relay accepts (the eleven bridge functions; the
#: parent refuses any other name, so a cell cannot reach the client's
#: connection machinery).
DFNB_METHODS = (
    'import_volume_as_np', 'import_roi_as_np', 'import_multiroi_as_np',
    'publish_np_as_volume', 'publish_np_as_roi', 'publish_np_as_multiroi',
    'import_measured_properties', 'export_measured_properties',
    'list_measured_properties', 'list_objects', 'delete_object',
)

# the stream-writer throttle, the same values the embedded kernel uses:
# at most one line per tenth of a second unless this much text piled up
_STREAM_FLUSH_SECONDS = 0.1
_STREAM_FLUSH_CHARS = 8192

# the bridge protocol's reserved dict key, kept identical so a value that
# cannot cross one channel cannot cross the other either
_DFN_MARKER = '__dfnb__'

# the exception classes a relayed dfnb error may rebuild (anything else
# crosses as a RuntimeError; their MESSAGES are the user-facing contract)
_ERROR_WHITELIST = {
    'ValueError': ValueError,
    'RuntimeError': RuntimeError,
    'TypeError': TypeError,
    'KeyError': KeyError,
    'OSError': OSError,
}

_STDIN_MESSAGE = ('input() is not supported in notebook cells -- assign the '
                  'value in the editor or read it from a file; the notebook '
                  'kernel has no interactive stdin.')


# ---------------------------------------------------------------------------
# the value codec (shared: the child encodes its call arguments and decodes
# the results; the parent does the reverse)
# ---------------------------------------------------------------------------

def _is_ndarray(value):
    """True for a numpy ndarray without importing numpy: the class lives
    in a ``numpy.*`` module and is named ``ndarray`` (an environment
    without numpy can never hold one, so the check must not need the
    import)."""
    cls = type(value)
    return cls.__name__ == 'ndarray' and \
        cls.__module__.split('.', 1)[0] == 'numpy'


def _is_dataframe(value):
    """True for a pandas DataFrame without importing pandas (the bridge
    protocol's duck check)."""
    cls = type(value)
    return cls.__name__ == 'DataFrame' and \
        cls.__module__.split('.', 1)[0] == 'pandas'


def _numpy_or_refuse():
    try:
        import numpy
    except ImportError:
        raise RuntimeError(
            'this Python environment has no numpy, so dfnb arrays cannot '
            'be transferred into it. Use the pip button on the toolbar '
            'to install numpy (it takes the full pip command, e.g. pip '
            'install numpy), then run the cell again.')
    return numpy


def encode_value(value):
    """One Python value as a JSON-encodable stand-in (the inverse of
    :func:`decode_value`): ndarrays as pickle-free ``numpy.save`` bytes,
    DataFrames column by column (object-dtyped columns as plain lists),
    bytes as base64, plain scalars / lists / str-keyed dicts as
    themselves. Anything else raises: a value that is not data must not
    silently cross the environment boundary."""
    if _is_ndarray(value):
        import numpy
        buffer = io.BytesIO()
        try:
            numpy.save(buffer, value, allow_pickle=False)
        except ValueError as exc:
            raise ValueError(
                'an array of dtype {} cannot cross into the environment '
                'kernel: {}.'.format(value.dtype, exc))
        return {_DFN_MARKER: 'ndarray',
                'npy': base64.b64encode(buffer.getvalue()).decode('ascii'),
                'shape': [int(size) for size in value.shape],
                'dtype': str(value.dtype)}
    if _is_dataframe(value):
        columns = [_encode_column(str(name), value[name])
                   for name in value.columns]
        return {_DFN_MARKER: 'dataframe', 'columns': columns,
                'index': _encode_column(None, value.index)}
    if isinstance(value, (bytes, bytearray)):
        return {_DFN_MARKER: 'bytes',
                'b64': base64.b64encode(bytes(value)).decode('ascii')}
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [encode_value(item) for item in value]
    if isinstance(value, dict):
        if _DFN_MARKER in value:
            raise ValueError(
                'a dict carrying the reserved {!r} key cannot cross into '
                'the environment kernel.'.format(_DFN_MARKER))
        return {str(key): encode_value(item) for key, item in value.items()}
    raise ValueError(
        'a value of type {} cannot cross into the environment kernel; '
        'convert it to a numpy array, a DataFrame or plain Python data '
        'first.'.format(type(value).__name__))


def _encode_column(name, series):
    """One DataFrame column (or the index, ``name`` None): non-object
    dtypes as ``npy`` bytes, object columns as a plain encoded list (the
    pickle-free fallback the bridge protocol uses too)."""
    array = series.to_numpy()
    if array.dtype == object:
        return {'name': name, 'kind': 'json',
                'values': [encode_value(item) for item in series.tolist()]}
    import numpy
    buffer = io.BytesIO()
    numpy.save(buffer, array, allow_pickle=False)
    return {'name': name, 'kind': 'npy',
            'npy': base64.b64encode(buffer.getvalue()).decode('ascii'),
            'dtype': str(array.dtype)}


def decode_value(value):
    """The inverse of :func:`encode_value` (a DataFrame decodes into a
    real DataFrame when this environment has pandas, and into a plain
    dict of columns when it does not: the arrays are still fully usable,
    only the pandas API is missing until pandas is installed)."""
    if isinstance(value, list):
        return [decode_value(item) for item in value]
    if isinstance(value, dict):
        marker = value.get(_DFN_MARKER)
        if marker == 'ndarray':
            numpy = _numpy_or_refuse()
            blob = base64.b64decode(value['npy'])
            return numpy.load(io.BytesIO(blob), allow_pickle=False)
        if marker == 'bytes':
            return base64.b64decode(value['b64'])
        if marker == 'dataframe':
            data = {column['name']: _decode_column(column)
                    for column in value['columns']}
            index = _decode_column(value['index']) \
                if value.get('index') is not None else None
            try:
                import pandas
            except ImportError:
                # the documented fallback: every column as a plain array
                return data
            column_names = [column['name'] for column in value['columns']]
            if index is None:
                frame = pandas.DataFrame(data)
            else:
                frame = pandas.DataFrame(data, index=index)
            return frame[column_names]
        return {key: decode_value(item) for key, item in value.items()}
    return value


def _decode_column(marker):
    """One encoded DataFrame column (or the index) back to values: an
    ``npy`` column as a real ndarray, a ``json`` (object-dtyped) column as
    a plain list."""
    if marker.get('kind') == 'json':
        return [decode_value(item) for item in marker.get('values') or []]
    numpy = _numpy_or_refuse()
    blob = base64.b64decode(marker['npy'])
    return numpy.load(io.BytesIO(blob), allow_pickle=False)


# ---------------------------------------------------------------------------
# the child runner
# ---------------------------------------------------------------------------

_real_stdout = None   # the pipe, captured before any cell swaps streams
_real_stdin = None
_emit_lock = threading.Lock()
_commands = queue.Queue()      # run / restart / quit, fed by the watcher
_responses = queue.Queue()     # dfnb_response lines, fed by the watcher
_main_thread_id = None
_ns = None                    # the cell namespace (one dict, like a shell)
_execution_count = 0
_executing = False             # the run window the watcher arms inside
_stop_pending = False          # a stop that landed between runs
_parent_gone = False          # stdin closed: the application is gone
_displayed_this_run = False    # a display()/figure suppressed the result
_dfnb_seq = 0
_dfnb_seq_lock = threading.Lock()


def _emit(obj):
    """One event line on the real stdout (never the swapped one). Every
    write is ASCII (json.dumps default) and flushed immediately: the child
    of a text-mode pipe is block-buffered, and the parent must not wait
    for a buffer to fill. A dead pipe is the parent's death: swallow the
    write error and let the watcher's exit path finish the process."""
    try:
        with _emit_lock:
            _real_stdout.write(json.dumps(obj) + '\n')
            _real_stdout.flush()
    except Exception:
        global _parent_gone
        _parent_gone = True


def _arm_interrupt(thread_id):
    """One-shot ``KeyboardInterrupt`` on ``thread_id`` at its next Python
    line (the embedded kernel's heartbeat mechanism, triggered by the
    stdin watcher instead of a tracer)."""
    ctypes.pythonapi.PyThreadState_SetAsyncExc(
        thread_id, ctypes.py_object(KeyboardInterrupt))


class _NoStdin:
    """The ``sys.stdin`` stub during a cell: reads raise a clear
    RuntimeError instead of blocking on a pipe nobody answers (the
    embedded kernel refuses input() the same way)."""

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


class _RunStream:
    """The ``sys.stdout`` / ``sys.stderr`` during a cell: buffered writes
    flushed as ``stream`` lines on the throttle the embedded kernel uses
    (one line per tenth of a second, or every 8192 characters), so a
    chatty cell cannot flood the pipe or the GUI thread."""

    def __init__(self, name):
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
        _emit({'op': 'stream', 'name': self.name, 'text': text})

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def isatty(self):
        return False

    encoding = 'utf-8'


def _dfnb_caller(method):
    def caller(*args, **kwargs):
        """The shim for one dfnb method: encode, one request line, block
        until the matching response (stale responses, left behind by a
        call an interrupt abandoned, are discarded by seq)."""
        global _dfnb_seq
        with _dfnb_seq_lock:
            _dfnb_seq += 1
            seq = _dfnb_seq
        _emit({'op': 'dfnb_request', 'seq': seq, 'method': method,
               'args': [encode_value(arg) for arg in args],
               'kwargs': {key: encode_value(item)
                          for key, item in kwargs.items()}})
        while True:
            if _parent_gone:
                raise RuntimeError(
                    'the notebook application is no longer running; the '
                    'environment kernel is shutting down.')
            try:
                item = _responses.get(timeout=0.25)
            except queue.Empty:
                continue  # also the slice where an armed interrupt lands
            if item.get('seq') != seq:
                continue  # a response for an interrupted, abandoned call
            if item.get('ok'):
                return decode_value(item.get('result'))
            error = item.get('error') or {}
            message = str(error.get('message') or '')
            cls = _ERROR_WHITELIST.get(str(error.get('type')))
            if cls is not None:
                raise cls(message)
            raise RuntimeError('{}: {}'.format(error.get('type'),
                                               message))
    return caller


def _display(obj):
    """The ``display()`` a cell may call: one text/plain display line
    (figures do not need it; the post-run flush captures them)."""
    global _displayed_this_run
    _displayed_this_run = True
    _emit({'op': 'display', 'data': {'text/plain': repr(obj)},
           'metadata': {}})


def _seed_ns():
    """The seed: the dfnb shim, ``display``, and the ``!command``
    cell-syntax helper (underscore names, internal like IPython's
    ``_``). There are no ``%`` magics in a plain interpreter (pip runs
    from the toolbar's pip button, not from cells); matplotlib, when
    the environment has it, binds the Agg backend at the first import
    and the post-run figure flush renders its figures as inline PNGs."""
    global _ns
    _ns = {'__builtins__': __builtins__,
           'dfnb': types.SimpleNamespace(
               **{name: _dfnb_caller(name) for name in DFNB_METHODS}),
           'display': _display,
           '_dfnb_system': _dfnb_system}


def _format_exception(exc):
    """The error fields for one failed cell: ename, evalue, and the
    traceback flattened into lines (tracebacks reach neither stream; the
    runner formats them itself, exactly like the embedded kernel)."""
    lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    flattened = []
    for chunk in lines:
        flattened.extend(chunk.rstrip('\n').split('\n'))
    return type(exc).__name__, str(exc), flattened


# the CREATE_NO_WINDOW creation flag (0 elsewhere: POSIX has no console
# to allocate; the standing no-window rule for every child this project
# spawns)
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


#: The no-op sink an orphaned command's forwarder is rebound to when a
#: Stop interrupts the wait (round 17 review: the orphan keeps printing
#: after its cell ended, and without the rebind those late writes
#: would surface in whatever run is active then -- the next cell's
#: outputs).
class _DropStream:
    def write(self, data):
        pass

    def flush(self):
        pass


def _launcher_head(word):
    """One command word normalized for the pip/python checks: lowercase
    with one trailing ``.exe`` dropped (the Windows shell resolves
    ``Python``, ``PYTHON``, and ``python.exe`` alike, so the checks must
    too -- round 17 review)."""
    head = word.lower()
    if head.endswith('.exe'):
        head = head[:-4]
    return head


def _pip_word(word):
    """True for the pip launcher spellings a venv's Scripts directory
    carries (``pip``, ``pip3``, ``pip3.11``, any casing, a ``.exe``
    suffix): pip commands are the pip button's, never a cell's (round
    17)."""
    head = _launcher_head(word)
    return head == 'pip' or head.startswith('pip3')


_PIP_BUTTON_ONLY = (
    'pip commands do not run from cells; use the pip button on the '
    'notebook toolbar (it takes the full pip command, e.g. pip install '
    'yfinance)\n')


def _run_system_command(command):
    """One ``!command`` line (the IPython system escape, restated for a
    plain interpreter): the command runs in a no-window child with
    devnull stdin (the JSON channel's stdin stays the watcher's alone)
    and CAPTURED pipes -- a grandchild inheriting this process's
    stdout would write into the JSON channel itself -- and whatever it
    prints streams through the cell's swapped streams, so the notebook
    sees it live. A ``python``/``python3`` first word (casing and a
    ``.exe`` suffix alike) reroutes to ``sys.executable`` (the venv's
    Scripts directory is not on PATH in a notebook child, so a bare
    ``python`` would find another interpreter or none at all); PIP IS
    REFUSED (round 17's rule: pip runs only from the toolbar's pip
    button, which takes the full pip command) -- any pip launcher
    first word, the ``python``/``py`` ``-m pip`` module shapes, and an
    unquotable line whose first words are still a launcher all write
    one note naming the button and spawn nothing, so the cell completes
    with the note as its output; anything else runs through the shell,
    because that is what ``!`` means (redirects, ``&&``, builtins) --
    with the one caveat that a rerouted command LEAVES the shell
    behind: a ``>`` or ``&&`` in a python line travels as a literal
    argument and the command answers with its own error (round 17
    review, parity with the posix path, not a regression).
    The wait is sliced (an armed Stop lands between slices); a stopped
    command's own process is orphaned and keeps running, the same
    limitation every shell escape has -- and the interrupt path
    rebinds its forwarder to the drop sink below, so a late write from
    the orphan lands nowhere, never in a later run's outputs."""
    if os.name == 'nt':
        # non-posix splitting keeps the backslashes of Windows paths
        # verbatim (posix splitting would hand pip C:wheelspkg.whl for
        # C:\wheels\pkg.whl) and leaves the quote characters in the
        # tokens, so strip one matching pair per token (round 17
        # review: the reroute's arguments must reach pip verbatim);
        # quoted spans still stay one token
        try:
            raw = shlex.split(command, posix=False)
        except ValueError:
            raw = None
        words = None
        if raw is not None:
            words = []
            for token in raw:
                if (len(token) >= 2 and token[0] == token[-1]
                        and token[0] in ('"', "'")):
                    token = token[1:-1]
                words.append(token)
    else:
        try:
            words = shlex.split(command, posix=True)
        except ValueError:
            words = None
    # round 17's rule, enforced here: pip runs only from the toolbar's
    # pip button, never from a cell -- every launcher spelling (any
    # casing, a .exe suffix), the ``python -m pip`` and ``py`` launcher
    # module shapes, and an unquotable line whose first words are still
    # a launcher are all refused BEFORE any spawn, so the note is the
    # cell's whole output and no child exists (round 17 review: the
    # shell resolves names case-insensitively, so a case-exact check
    # leaked pip.exe / PIP / py -m pip straight to PATH)
    def _pip_attempt(words_):
        if not words_:
            return False
        if _pip_word(words_[0]):
            return True
        if _launcher_head(words_[0]) not in ('python', 'python3', 'py'):
            return False
        for index in (1, 2):  # python -m pip / py -3 -m pip
            if (len(words_) > index + 1
                    and words_[index] == '-m'
                    and _pip_word(words_[index + 1])):
                return True
        return False

    # the split-failure fallback re-checks the NAIVE first tokens with
    # leading quote characters stripped (an unclosed quote can sit
    # before the launcher: !"pip install x -- the mangled line is
    # still a pip attempt and must still refuse, not reach the shell)
    if _pip_attempt(words) or (words is None and _pip_attempt(
            [word.lstrip('"\'') for word in command.split()[:4]])):
        sys.stderr.write(_PIP_BUTTON_ONLY)
        return
    if words and _launcher_head(words[0]) in ('python', 'python3'):
        argv = [sys.executable] + words[1:]
    else:
        argv = command  # the shell path (a str Popen means shell=True)
    try:
        proc = subprocess.Popen(
            argv, shell=not isinstance(argv, list),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
            encoding='utf-8', errors='replace', bufsize=1,
            creationflags=_NO_WINDOW)
    except OSError as exc:
        sys.stderr.write('the command could not start: {}\n'.format(exc))
        return
    # the forwarder writes through a one-slot sink so the interrupt
    # path can rebind it below: on the normal path the slot simply
    # holds the cell's own swapped stream object (late bytes beyond it
    # sit in that object's buffer, never in the JSON channel)
    out_stream, err_stream = sys.stdout, sys.stderr
    sink = [out_stream]

    def forward():
        for line in proc.stdout:
            sink[0].write(line)
            if line.endswith('\n'):
                sink[0].flush()
        try:
            sink[0].flush()
        except Exception:
            pass

    reader = threading.Thread(target=forward, daemon=True)
    reader.start()
    try:
        while proc.poll() is None:
            time.sleep(0.1)  # the interruptible slice a Stop lands in
    except KeyboardInterrupt:
        # the command's process is orphaned by design (documented
        # above); the interrupt PROPAGATES into _execute's existing
        # guard, so the run ends interrupted -- round 17 review: the
        # swallowed version reported the interrupted run as a clean
        # completion while the cell's remaining statements kept
        # executing on. The orphan's forwarder is rebound to the drop
        # sink FIRST: its late writes land nowhere, never in a later
        # run's outputs (the reviewer's misattribution window)
        sink[0] = _DropStream()
        raise
    reader.join()
    if proc.returncode != 0:
        err_stream.write('(the command exited with code {})\n'.format(
            proc.returncode))


def _dfnb_system(command):
    """The ``!command`` rewrite target (see :func:`_run_system_command`;
    a plain function in the namespace, the way IPython routes ``!``
    through ``get_ipython().system``)."""
    _run_system_command(command)


_NO_MAGICS = (
    'this environment kernel runs plain Python cells: % magics are not '
    'supported here (the %pip magic included -- switch the environment '
    'back to the Dragonfly embedded Python for IPython magics), and pip '
    'commands do not run from cells; they run from the pip button on '
    'the notebook toolbar.')


def _parse_cell(code):
    """One cell's AST: plain Python parses directly. ``!command`` lines
    (the system escape) rewrite to the internal helper -- but only when
    plain parsing FAILS first, so a ``!`` or ``%`` inside a string
    literal (valid Python) stays the string it is. ``%`` magics are not
    rewritten at all (``%pip`` lands in the same not-supported bucket
    as every other magic, round 17's rule: pip runs from the toolbar's
    button, not from a cell), and a ``%``-line gets the explanatory
    message naming both that and the embedded kernel (the bare
    SyntaxError at ``%`` says nothing); everything else raises the
    rewrite's own SyntaxError, which is the honest error for the code
    the cell actually contains. Known limitation (round 17 review): on
    a cell whose plain parse fails, a ``%`` line that is really inside
    a multi-line string reads as a magic line to the rewriter, so the
    explanatory magic error can MASK the cell's real syntax error --
    the honest fix needs error attribution on unparseable source."""
    try:
        return ast.parse(code, '<cell>')
    except SyntaxError:
        pass
    lines = []
    touched = False
    other_magic = False
    for line in code.split('\n'):
        stripped = line.lstrip()
        indent = line[:len(line) - len(stripped)]
        first = stripped.split(' ', 1)[0] if stripped.split(' ') else ''
        if stripped.startswith('!'):
            lines.append('{}_dfnb_system({!r})'.format(
                indent, stripped[1:].strip()))
            touched = True
        else:
            if first.startswith('%'):
                other_magic = True
            lines.append(line)
    if touched:
        try:
            return ast.parse('\n'.join(lines), '<cell>')
        except SyntaxError:
            if other_magic:
                raise SyntaxError(_NO_MAGICS, ('<cell>', 1, 0, code))
            raise
    if other_magic:
        # a %magic cell with nothing to rewrite: the bare syntax error
        # at '%' says nothing about what is actually unsupported
        raise SyntaxError(_NO_MAGICS, ('<cell>', 1, 0, code))
    return ast.parse(code, '<cell>')  # the plain error, re-raised


def _flush_figures():
    """Every open matplotlib figure as one inline ``image/png`` display
    line (the inline-backend feel in a process that has no IPython: the
    Agg backend renders, the figures close after capture so a later cell
    does not re-show them). Environments without matplotlib never reach
    this: the pyplot module simply is not in ``sys.modules``."""
    pyplot = sys.modules.get('matplotlib.pyplot')
    if pyplot is None:
        return
    global _displayed_this_run
    try:
        figures = list(pyplot.get_fignums())
    except Exception:
        return
    for number in figures:
        try:
            figure = pyplot.figure(number)
            buffer = io.BytesIO()
            figure.savefig(buffer, format='png')
            _emit({'op': 'display',
                   'data': {'image/png': base64.b64encode(
                       buffer.getvalue()).decode('ascii')},
                   'metadata': {}})
            _displayed_this_run = True
        except Exception as exc:
            _emit({'op': 'stream', 'name': 'stderr',
                   'text': 'the figure could not be rendered: '
                           '{}\n'.format(exc)})
    try:
        pyplot.close('all')
    except Exception:
        pass


def _execute(code):
    """One cell: the last ``ast.Expr`` statement evaluates (an
    ``execute_result`` line when it is not None and nothing displayed),
    everything before it execs; errors and interrupts become ``error``
    lines and NEVER reach the swapped streams. The namespace and the
    execution counter survive runs; ``restart`` wipes both. The whole
    body, epilogue included, lives inside the interrupt guard: a stop
    armed at the tail of a cell (the microseconds between the watcher's
    ``_executing`` check and the interrupt's delivery) would otherwise
    land in the unguarded epilogue and kill this process, taking the
    namespace with it -- it must end as one interrupted ``cell_done``
    instead."""
    global _execution_count, _executing, _stop_pending, _displayed_this_run
    _executing = True
    _displayed_this_run = False
    _execution_count += 1
    count = _execution_count
    pending_stop = _stop_pending
    _stop_pending = False
    interrupted = False
    error_fields = None
    result_value = None
    stdout_stream, stderr_stream = _RunStream('stdout'), _RunStream('stderr')
    saved = (sys.stdout, sys.stderr, sys.stdin)
    sys.stdout, sys.stderr, sys.stdin = stdout_stream, stderr_stream, _NoStdin()
    try:
        try:
            if pending_stop:
                # a stop that landed while the run was still queued: the cell
                # must not run at all (the embedded kernel's queued-stop
                # semantics, minus the tracer race)
                raise KeyboardInterrupt
            tree = _parse_cell(code)
            body = list(tree.body)
            if body and isinstance(body[-1], ast.Expr):
                last = body.pop()
                if body:
                    exec(compile(ast.Module(body=body, type_ignores=[]),
                                 '<cell>', 'exec'), _ns)
                result_value = eval(compile(ast.Expression(last.value),
                                            '<cell>', 'eval'), _ns)
            else:
                exec(compile(tree, '<cell>', 'exec'), _ns)
        except KeyboardInterrupt:
            interrupted = True
            error_fields = ('KeyboardInterrupt', '', ['KeyboardInterrupt'])
        except BaseException as exc:
            error_fields = _format_exception(exc)
        finally:
            _executing = False
            sys.stdout, sys.stderr, sys.stdin = saved
            stdout_stream.flush()
            stderr_stream.flush()
        _flush_figures()
        if result_value is not None and not _displayed_this_run:
            _emit({'op': 'result', 'data': {'text/plain': repr(result_value)},
                   'metadata': {}, 'count': count})
    except KeyboardInterrupt:
        # the stop armed at the very end of the cell and landed here, in
        # the epilogue: the code region is over, so report the run as
        # interrupted and keep serving (never let a stale interrupt
        # escape to the command loop and kill the child)
        interrupted = True
        error_fields = ('KeyboardInterrupt', '', ['KeyboardInterrupt'])
    if error_fields is not None:
        ename, evalue, traceback_lines = error_fields
        _emit({'op': 'error', 'ename': ename, 'evalue': evalue,
               'traceback': traceback_lines})
    _emit({'op': 'cell_done', 'count': count, 'interrupted': interrupted})


def _restart():
    """Wipes the namespace and the execution counter (the embedded
    kernel's ``shell.reset(new_session=True)`` + re-seed, restated), and
    closes any figures a pre-restart cell left open."""
    global _execution_count
    pyplot = sys.modules.get('matplotlib.pyplot')
    if pyplot is not None:
        try:
            pyplot.close('all')
        except Exception:
            pass
    _seed_ns()
    _execution_count = 0
    _emit({'op': 'restarted'})


_STDIN_POLL_SECONDS = 0.05


def _handle_stdin_line(line):
    """One line from the watcher (both reader mechanisms end here):
    run / restart / quit go to the command queue; stop arms the
    interrupt inside a run and latches between runs; dfnb responses go
    to their queue. A non-JSON line is skipped (this channel is JSON
    only)."""
    global _stop_pending
    try:
        message = json.loads(line)
    except ValueError:
        return
    op = message.get('op')
    if op == 'run':
        _commands.put(message)
    elif op == 'restart':
        _commands.put({'op': 'restart'})
    elif op == 'quit':
        _commands.put({'op': 'quit'})
    elif op == 'stop':
        if _executing:
            _arm_interrupt(_main_thread_id)
        else:
            _stop_pending = True
    elif op == 'dfnb_response':
        _responses.put(message)


def _watch_stdin_polling(fd):
    """The Windows stdin reader, which NEVER blocks in ReadFile (the
    loader deadlock, see the module docstring: a thread blocked in a
    synchronous read on the stdin pipe hangs the first OpenBLAS-linked
    DLL load on another thread, which is what froze the user's
    yfinance cell -- probe-proven 6/6 with the blocking reader, clean
    with this poll). PeekNamedPipe says how many bytes are already in
    the pipe; the read takes exactly those bytes (it completes
    immediately) and the loop slices lines out of its own buffer. A
    failed peek (the pipe is broken) or an EOF read means the parent
    is gone: return, and the watcher's epilogue finishes the process.
    The blocking-read fallback exists only for a stdin that cannot be
    peeked at all, a shape the real child never takes."""
    import ctypes
    from ctypes import wintypes
    try:
        handle = ctypes.c_void_p(msvcrt.get_osfhandle(fd))
    except (AttributeError, OSError, ValueError):
        handle = None
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.PeekNamedPipe.restype = wintypes.BOOL
        kernel32.PeekNamedPipe.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
            ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p]
    except (OSError, ValueError):
        handle = None  # no peeking without kernel32
    available = wintypes.DWORD(0)
    pending = b''
    while True:
        if handle is not None:
            if not kernel32.PeekNamedPipe(
                    handle, None, 0, None, ctypes.byref(available), None):
                return
            count = available.value
        else:
            count = 4096
        if count:
            try:
                chunk = os.read(fd, count)
            except OSError:
                return
            if not chunk:
                return
            pending += chunk
            while b'\n' in pending:
                raw, pending = pending.split(b'\n', 1)
                _handle_stdin_line(
                    raw.decode('utf-8', 'replace').strip())
        else:
            time.sleep(_STDIN_POLL_SECONDS)


def _watch_stdin():
    """The ONLY stdin reader (the run loop must never read the pipe, so
    an interrupt always has a thread that sees it). The latch serves
    the parent's queued stops (a stop line written just before the run
    line, for a stop requested while the run was still queued), which
    means a stop that races the previous run's end -- the child
    finished, the parent has not read its ``cell_done`` yet -- also
    lands in it and interrupts the NEXT run before its code runs; that
    pump-latency window is inherent and accepted. stdin closing means
    the application is gone: unwedge any run and make sure this process
    does not outlive its parent.

    Windows reads through :func:`_watch_stdin_polling` -- never a
    blocking ReadFile (the loader deadlock, module docstring); POSIX
    keeps the plain blocking iteration, which there is the right
    design."""
    global _parent_gone, _stop_pending
    try:
        if os.name == 'nt':
            _watch_stdin_polling(0)
        else:
            for line in _real_stdin:
                _handle_stdin_line(line)
    except Exception:
        pass
    _parent_gone = True
    if _executing:
        _arm_interrupt(_main_thread_id)  # unwedge the last cell
    time.sleep(0.5)  # give the run loop a moment to exit on its own
    os._exit(0)      # never leave an environment process behind its parent


def _main():
    global _real_stdout, _real_stdin, _main_thread_id
    _real_stdout = sys.stdout
    _real_stdin = sys.stdin
    _main_thread_id = threading.get_ident()
    # the application's package must not be importable from a cell: a venv
    # that could ``import notebook_kernel`` would pull the GUI stack into
    # the child and lose the whole point of the isolation
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [entry for entry in sys.path
                   if os.path.abspath(entry or os.curdir) != here]
    _seed_ns()
    threading.Thread(target=_watch_stdin, daemon=True).start()
    _emit({'op': 'ready'})
    while True:
        try:
            message = _commands.get()
            op = message.get('op')
            if op == 'quit':
                return
            if op == 'restart':
                _restart()
            elif op == 'run':
                _execute(message.get('code') or '')
        except KeyboardInterrupt:
            # a stale stop interrupt (armed at the tail of a cell that
            # had already ended, delivered while the loop sat between
            # commands): the run it was meant for is over -- swallow it
            # and keep serving
            continue


if __name__ == '__main__':
    _main()
