r"""Tier-1 tests for the Dragonfly Notebook's ORS-free layer: the .ipynb
core (DragonflyNotebook/dragonflynotebook_core.py) plus, since round 8,
the process-separation modules (``notebook_bridge_protocol`` and
``dfnb_client``) against a fake in-process protocol server.

Standalone CASES-style suite: per-case PASS/FAIL lines, run_all() returning
bool, main() printing SUITE PASSED/FAILED and exiting 0/1 -- NOT pytest
(the MatcherDragonfly/DragonflyH5Importer template). The core is the
framework-free .ipynb document model + the dfnb snippet templates + the
last-folder registry; every case pins it bare (stdlib json + text, no ORS,
no Qt) on the embedded interpreter:

  * the canonical writer: a fresh notebook is nbformat 4.5 with the exact
    Dragonfly kernelspec {'name': 'dragonfly', 'display_name': 'Dragonfly',
    'language': 'python'}, one empty code cell with a fresh id, 1-space
    indent + sorted keys + trailing newline, and code sources as lists of
    keepends lines;
  * the tolerant reader: sources as str AND as list-of-strings read the
    same; missing cell ids are generated unique; code/markdown/raw all
    load; outputs ride through verbatim (arbitrary nested JSON preserved);
    execution_count int/None (a float 3.0 -> 3, garbage -> None);
  * the reader's rejections: non-JSON text, a non-object top level, a
    missing/non-list cells entry, an unknown cell type and a non-string
    source raise ValueError with a message naming the problem;
  * byte-identity round trip: read(canonical text).to_json() == the same
    text; and read -> write is idempotent (a second pass changes nothing);
  * cell operations: add at end/middle, remove, move up/down (False at both
    boundaries, no reorder), set source, retype (code -> markdown drops
    outputs + execution_count, markdown -> code gains empty outputs + None),
    clear outputs (one cell / every code cell, markdown untouched) -- every
    mutation raises is_dirty, save()/from_json() clear it, and
    save-without-a-path raises ValueError;
  * the eight snippet templates, generated text pinned exactly: default
    placeholders per kind, repr-escaped titles (a quote title is pinned
    through its escape), timestep omitted at 0 and appended at nonzero,
    reference/spacing/properties only when provided, publish targets
    new_volume/new_roi/new_multiroi, and the export kind's TWO modes (a
    bare write-into-existing call vs a publish_np_as_multiroi call
    carrying the properties variable);
  * identifier sanitization: spaces/punctuation -> '_', leading digit ->
    leading '_', a Python keyword gets a trailing '_', empty falls back;
  * the registry: load on a missing/corrupt file returns the None default,
    save then load round-trips the folder (temp file, no %LOCALAPPDATA%
    writes).

Round 8 added the process-separation tier-1 half (still ORS-free -- these
modules import no ORS code and no Dragonfly module):

  * the shared bridge protocol (``notebook_bridge_protocol``): encode/
    decode round-trips of every bridge-serializable shape (scalars, lists,
    dicts, numpy arrays npy-encoded, bytes, pandas DataFrames as column/
    index parts with an object-dtype JSON fallback), the refusals (a set,
    a dict carrying the reserved ``__dfnb__`` key, a pickled-object
    array), the frame layout (8-byte little-endian length prefix, 4-byte
    JSON length), chunked FrameReader delivery at arbitrary boundaries,
    the oversize guard, and the PID-suffixed pipe name;
  * the dfnb client (``dfnb_client``) against a FAKE in-process protocol
    server (real frames, real encode/decode, no QLocalServer, no ORS --
    that is what tier 2's loopback is for): the version handshake (a
    mismatched WELCOME refuses), call round trips, the error envelope
    mapping (ValueError/RuntimeError rebuilt with their original class,
    an exotic class becomes RuntimeError), progress frames forwarded to
    ``progress_callback``, the eleven public methods' client-side
    validation and dtype conforming (fail fast, no round trip), the
    publish handle that later source parameters re-accept, the
    disconnect message, the worker->owner marshal box, and the cancel
    mechanism (a delayed request + the cancel flag -> a REAL cancel
    frame on the wire and a KeyboardInterrupt with the exact user-facing
    message in the calling thread).

Round 16 added the custom Python environments (``notebook_envs``,
pure stdlib): the name rules and discovery (only directories carrying
pyvenv.cfg AND the venv interpreter count, sorted case-insensitively),
the in-use lock (same-process refusal + a real CROSS-process hold that
frees when the holding process exits, a stale marker file alone meaning
nothing), the environment runners (``dfnb_env_kernel``, the JSON-line
child the kernel spawns on the venv's own interpreter: the whole-cell
interrupt guard that keeps a tail-of-cell stop from killing the child),
the lazy-numpy proxies (no bind at import, the reset hook as a test
seam -- pinned in a child process so a fake numpy never poisons this
suite's own), and the long-run argv shapes plus the no-window
bootstrap's runpy plumbing, run for real on cheap no-write commands.
Cells actually RUNNING on an environment's interpreter (spawn, relay,
stop, restart, switch) are the kernel smoke's environment pins; the
REAL venv creation, a real OFFLINE pip install and the form-side UI
rules are tier 3's section 10.

Run (tier 1 is framework-free: straight on the embedded interpreter, no
dfpy_ors launcher needed):

  uv run --no-project --python "C:\Program Files\Dragonfly\Python_env\python.exe" \
      verification/test_dragonflynotebook_core.py
"""

import importlib.util
import json
import keyword
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback

import ctypes

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

# As in the core itself and the reference template: MUST precede numpy,
# so the ORS/libs OpenMP mix never aborts the process with 'OMP: Error #15'.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "True")

# (The core cases below stay pure stdlib; the round-8 bridge cases import
# numpy + pandas through the protocol module, which also registers the two
# Dragonfly DLL directories its guarded add_dll_directory needs on a bare
# embedded interpreter.)

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

# ---------------------------------------------------------------------------
# import: the shipped core, BY FILE PATH (the tier-1 suite must never import
# the plugin package's ORS glue -- the __init__'s find_spec guard keeps the
# package import ORS-free here: ORSServiceClass is not importable in this
# launcher-free environment, so the eager re-export is skipped -- exactly
# what the round-8 submodules rely on)
# ---------------------------------------------------------------------------

_CORE_PATH = os.path.join(_ROOT, "DragonflyNotebook", "dragonflynotebook_core.py")
_core_spec = importlib.util.spec_from_file_location("dragonflynotebook_core",
                                                    _CORE_PATH)
core = importlib.util.module_from_spec(_core_spec)
sys.modules["dragonflynotebook_core"] = core
_core_spec.loader.exec_module(core)

# ---------------------------------------------------------------------------
# import: the round-8 bridge modules, AS A PACKAGE (dfnb_client does a
# relative import of the protocol; both are ORS-free)
# ---------------------------------------------------------------------------

if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from DragonflyNotebook import notebook_bridge_protocol as protocol  # noqa: E402
from DragonflyNotebook import dfnb_client as dfnb  # noqa: E402
from DragonflyNotebook import notebook_envs as envs  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# Round 16: the suite's own children (the environment cases spawn a few)
# run CREATE_NO_WINDOW'd, the round-14 convention: a console-less child
# of this console-bearing gate would allocate its own visible console.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Round 16: the children must also see a BARE interpreter. This suite
# runs both standalone (tier 1: a clean shell environment) and imported
# by the tier-2 suite INSIDE the dfpy_ors launcher, which sets PYTHONPATH
# to Dragonfly's ORS tree (and QT_PLUGIN_PATH); a child inheriting those
# runs the ORS auto-import chain at startup (its numpy bound before the
# first line of -c code, the ORS console banner on its stdout). The same
# two keys the app launcher strips for the notebook application process
# (pinned by tier 3) are dropped here; notebook_envs.popen does the
# identical scrub for the form's own long-run children.
_CHILD_ENV = {key: value for key, value in os.environ.items()
              if key not in ("PYTHONPATH", "QT_PLUGIN_PATH")}


# ---------------------------------------------------------------------------
# fixtures: a hand-built canonical notebook (1-space indent, sorted keys,
# trailing newline -- exactly what the writer emits)
# ---------------------------------------------------------------------------

_CANONICAL_NOTEBOOK = """{
 "cells": [
  {
   "cell_type": "code",
   "execution_count": 3,
   "id": "aaaa0001",
   "metadata": {},
   "outputs": [
    {
     "data": {
      "text/plain": [
       "42"
      ]
     },
     "execution_count": 3,
     "metadata": {},
     "output_type": "execute_result"
    }
   ],
   "source": [
    "answer = 40 + 2\\n",
    "answer"
   ]
  },
  {
   "cell_type": "markdown",
   "id": "aaaa0002",
   "metadata": {},
   "source": [
    "# Title\\n"
   ]
  },
  {
   "cell_type": "raw",
   "id": "aaaa0003",
   "metadata": {},
   "source": [
    "raw text, canonical as a list like every source\\n"
   ]
  }
 ],
 "metadata": {
  "kernelspec": {
   "display_name": "Dragonfly",
   "language": "python",
   "name": "dragonfly"
  }
 },
 "nbformat": 4,
 "nbformat_minor": 5
}
"""


# ---------------------------------------------------------------------------
# the fake in-process protocol server + transport (round-8 bridge cases)
# ---------------------------------------------------------------------------

def _boom_value(_params, _progress):
    raise ValueError('bad value input')


def _boom_runtime(_params, _progress):
    raise RuntimeError('the operation failed halfway')


def _boom_exotic(_params, _progress):
    raise KeyError('missing')


def _echo(params, _progress):
    return {'echo': params}


def _progress_then_ok(_params, progress):
    progress('Working', 1, 3)
    progress('Working', 2, 3)
    return {'done': True}


def _published(_params, _progress):
    return {'published': {'guid': 'guid-1', 'title': 'Result volume',
                          'class_name': 'Channel'}}


def _imported(_params, _progress):
    return {'array': np.zeros((2, 2), np.uint8)}


def _never_called(_params, _progress):
    raise AssertionError('the delayed handler must never run')


class _FakeServer(object):
    """The in-process protocol server the dfnb client unit tests talk to:
    it consumes the client's written bytes through the REAL FrameReader,
    dispatches requests through a handler table, and answers with REAL
    ``encode_frame`` frames (payloads through ``encode_value``). It stands
    in for ``bridge_service.NotebookBridgeService`` at the protocol level
    only -- no QLocalServer, no ORS (that is what tier 2's loopback is
    for). ``delay_methods`` holds methods whose requests are recorded but
    NEVER answered (the cancel case holds a transaction open)."""

    def __init__(self, handlers):
        self.reader = protocol.FrameReader()
        self.handlers = handlers
        self.delay_methods = set()
        self.welcome_version = None   # None -> echo the client's version
        self.inbox = bytearray()
        self.out = bytearray()
        self.requests = []            # (method, params) pins
        self.cancels = []             # cancelled request ids, in order
        self.produced = []            # emitted progress triples

    def write_in(self, data):
        self.inbox += bytes(data)

    def pump(self):
        for envelope, parts in self.reader.feed(bytes(self.inbox)):
            self._handle(envelope, parts)
        del self.inbox[:]

    def _handle(self, envelope, parts):
        kind = envelope.get('type')
        if kind == protocol.HELLO:
            version = self.welcome_version
            if version is None:
                version = envelope.get('version')
            self._send({'type': protocol.WELCOME, 'version': version})
            return
        if kind == protocol.CANCEL:
            self.cancels.append(envelope.get('id'))
            return
        if kind != protocol.REQUEST:
            return
        request_id = envelope.get('id')
        method = envelope.get('method')
        try:
            params = protocol.decode_value(envelope.get('params') or {}, parts)
        except Exception as exc:
            self._send_error(request_id, 'ValueError', str(exc))
            return
        self.requests.append((method, params))
        if method in self.delay_methods:
            return
        self._dispatch(request_id, method, params)

    def _dispatch(self, request_id, method, params):
        handler = self.handlers.get(method)
        if handler is None:
            self._send_error(request_id, 'RuntimeError',
                             'The bridge has no handler for {!r}.'
                             .format(method))
            return
        try:
            payload = handler(params, self._progress_emitter(request_id))
        except Exception as exc:
            self._send_error(request_id, type(exc).__name__, str(exc))
            return
        out_parts = []
        self._send({'type': protocol.RESPONSE, 'id': request_id, 'ok': True,
                    'payload': protocol.encode_value(payload, out_parts)},
                   out_parts)

    def _progress_emitter(self, request_id):
        def emit(caption, step, of):
            self.produced.append((caption, step, of))
            self._send({'type': protocol.PROGRESS, 'id': request_id,
                        'caption': caption, 'step': step, 'of': of})
        return emit

    def _send(self, envelope, parts=None):
        self.out += protocol.encode_frame(envelope, parts)

    def _send_error(self, request_id, error_class, message):
        self._send({'type': protocol.RESPONSE, 'id': request_id, 'ok': False,
                    'error_class': error_class, 'message': message})


class _FakeTransport(object):
    """The client-side transport the tests inject through DfnbClient's
    ``transport_class`` seam: writes pump the fake server synchronously
    (its responses are readable immediately, exactly like a real pipe
    round trip), reads drain its output buffer, and ``close()`` simulates
    the peer's death for the disconnect pins."""

    server = None   # class attribute: bound per test by _make_client

    def __init__(self):
        self.connected = False
        self.name = None

    def open(self, name, timeout_s=3.0):
        self.name = name
        self.connected = True
        return True, ''

    def close(self):
        self.connected = False

    def write(self, data):
        if not self.connected:
            raise RuntimeError('not connected')
        self.server.write_in(data)
        self.server.pump()

    def read_available(self):
        data = bytes(self.server.out)
        del self.server.out[:]
        return data

    def wait_readable(self, timeout_s):
        time.sleep(min(timeout_s, 0.01))
        return len(self.server.out) > 0

    @property
    def is_connected(self):
        return self.connected


def _make_client(handlers, pipe_name='dfnb_bridge_test'):
    """A client wired to a fresh fake server: explicit pipe names only --
    a None name would make ``connect`` scan the REAL pipe namespace."""
    server = _FakeServer(handlers)
    transport_class = type('BoundFakeTransport', (_FakeTransport,),
                           {'server': server})
    client = dfnb.DfnbClient(pipe_name=pipe_name,
                             transport_class=transport_class)
    return client, server


# ---------------------------------------------------------------------------
# cases (one behavior each)
# ---------------------------------------------------------------------------

def case_canonical_writer_pins_nbformat_45_and_kernelspec():
    """A fresh notebook serializes as nbformat 4.5 with the exact Dragonfly
    kernelspec, one empty code cell (fresh id, empty outputs/execution_count
    None), 1-space indent + sorted keys, a trailing newline, and code
    sources as keepends line lists."""
    text = core.NotebookDocument.new_notebook().to_json()
    assert text.endswith('\n') and not text.endswith('\n\n'), \
        "the canonical text ends with exactly one newline"
    data = json.loads(text)
    assert data['nbformat'] == 4 and data['nbformat_minor'] == 5, \
        "nbformat 4.5, got {}/{}".format(data['nbformat'], data['nbformat_minor'])
    assert data['metadata'] == {'kernelspec': {
        'name': 'dragonfly', 'display_name': 'Dragonfly',
        'language': 'python'}}, \
        "the Dragonfly kernelspec exactly, got {!r}".format(data['metadata'])
    assert len(data['cells']) == 1, \
        "a fresh notebook starts with one cell, got {}".format(len(data['cells']))
    cell = data['cells'][0]
    assert cell['cell_type'] == 'code', "the fresh cell is a code cell"
    assert isinstance(cell['id'], str) and cell['id'], "a fresh cell id exists"
    assert cell['source'] == [], "an empty source serializes as []"
    assert cell['execution_count'] is None and cell['outputs'] == [], \
        "a fresh code cell has no outputs and no execution count"
    assert cell['metadata'] == {}, "cell metadata starts empty"
    # canonical layout spot-checks: sorted keys at every level, 1-space indent
    assert text.startswith('{\n "cells": [\n  {\n   "cell_type":'), \
        "1-space indent with sorted top-level keys, got:\n{}".format(text[:60])
    print("  canonical writer: nbformat 4.5, Dragonfly kernelspec, one "
          "empty code cell, 1-space indent, sorted keys, trailing \\n")


def case_canonical_roundtrip_is_byte_identical_and_idempotent():
    """Reading the canonical text and writing it back reproduces the text
    BYTE FOR BYTE (deterministic writer), and a second read->write pass
    changes nothing (idempotence)."""
    document = core.NotebookDocument.from_json(_CANONICAL_NOTEBOOK)
    assert document.to_json() == _CANONICAL_NOTEBOOK, \
        "read -> write must be byte-identical on canonical input"
    twice = core.NotebookDocument.from_json(document.to_json()).to_json()
    assert twice == _CANONICAL_NOTEBOOK, "the writer must be idempotent"
    print("  round trip: read -> write byte-identical on canonical input, "
          "and idempotent on a second pass")


def case_tolerant_reader_str_and_list_sources_and_generated_ids():
    """The reader accepts both nbformat source spellings to the same
    internal str, generates unique ids where missing, and keeps code cells'
    outputs verbatim -- including arbitrary nested output objects no other
    code path interprets."""
    raw = {
        'nbformat': 4, 'nbformat_minor': 4,  # a foreign 4.4 file is fine
        'metadata': {'kernelspec': {'name': 'python3'}},
        'cells': [
            {'cell_type': 'code', 'source': 'a = 1\nb = 2\n',  # str spelling
             'outputs': [
                 {'output_type': 'stream', 'name': 'stdout',
                  'text': ['hello\n', 'world\n']},
                 {'output_type': 'display_data', 'data':
                     {'image/png': 'aGVsbG8=', 'application/vnd.custom+json':
                      {'weird': [1, 2, {'nested': True}]}},
                  'metadata': {'tag': 'keep me'}},
             ],
             'execution_count': None},
            {'cell_type': 'markdown', 'source': ['# Head\n', '\n', 'Body text\n']},
            {'cell_type': 'raw', 'source': []},           # empty list source
            {'cell_type': 'code', 'source': 'x',          # garbage count
             'execution_count': 'not-a-count'},
        ],
    }
    document = core.NotebookDocument.from_json(json.dumps(raw))
    assert document.get_cell(0)['source'] == 'a = 1\nb = 2\n', \
        "a str source passes through unchanged"
    assert document.get_cell(1)['source'] == '# Head\n\nBody text\n', \
        "a list source joins with '' (items carry their newlines)"
    assert document.get_cell(2)['source'] == '', "an empty list source -> ''"
    assert document.get_cell(3)['execution_count'] is None, \
        "a garbage execution_count normalizes to None (tolerant)"
    outputs = document.get_cell(0)['outputs']
    assert len(outputs) == 2, "outputs ride through verbatim"
    assert outputs[1]['data']['application/vnd.custom+json'] == \
        {'weird': [1, 2, {'nested': True}]} and \
        outputs[1]['metadata'] == {'tag': 'keep me'}, \
        "nested custom outputs are preserved untouched"
    ids = [document.get_cell(i)['id'] for i in range(document.cell_count())]
    assert len(set(ids)) == len(ids) and all(ids), \
        "every cell carries a unique id: {!r}".format(ids)
    assert document.metadata == {'kernelspec': {'name': 'python3'}}, \
        "a foreign file keeps its own metadata"
    print("  tolerant reader: str + list sources equal, ids generated "
          "unique, outputs verbatim, garbage count -> None, metadata kept")


def case_tolerant_reader_normalizes_float_counts_and_keeps_them():
    """An execution_count of 3.0 normalizes to the int 3 (an integer-valued
    float is nbformat-compatible garbage some writers emit); None stays
    None; a bool is not a count."""
    raw = {'nbformat': 4, 'nbformat_minor': 5,
           'metadata': {'kernelspec': {'name': 'dragonfly'}},
           'cells': [
               {'cell_type': 'code', 'source': '1', 'execution_count': 3.0},
               {'cell_type': 'code', 'source': '2', 'execution_count': None},
               {'cell_type': 'code', 'source': '3', 'execution_count': True},
           ]}
    document = core.NotebookDocument.from_json(json.dumps(raw))
    counts = [document.get_cell(i)['execution_count'] for i in range(3)]
    assert counts == [3, None, None], \
        "3.0 -> 3, None -> None, bool -> None, got {!r}".format(counts)
    print("  execution_count normalization: 3.0 -> 3, None and bool -> None")


def case_reader_rejects_malformed_notebooks_with_clear_errors():
    """Every malformed input raises ValueError (never KeyError/TypeError)
    with a message that names the problem: non-JSON text, a non-object top
    level, a missing/non-list cells entry, an unknown cell type, a
    non-string/list source, an unsupported nbformat, and a non-string item
    inside a list source."""
    bad_inputs = (
        ('not json at all {', 'valid JSON'),
        ('[1, 2]', 'top-level JSON value'),
        ('{"nbformat": 4, "nbformat_minor": 5, "metadata": {}}', '"cells"'),
        ('{"nbformat": 4, "nbformat_minor": 5, "cells": {"a": 1}}', '"cells"'),
        ('{"nbformat": 3, "nbformat_minor": 0, "cells": []}', 'nbformat 3'),
        ('{"nbformat": 4, "nbformat_minor": 5, "cells": ['
         '{"cell_type": "slide", "source": ""}]}', "cell type 'slide'"),
        ('{"nbformat": 4, "nbformat_minor": 5, "cells": ['
         '{"cell_type": "code", "source": 5}]}', 'string or a list'),
        ('{"nbformat": 4, "nbformat_minor": 5, "cells": ['
         '{"cell_type": "code", "source": ["ok", 5]}]}', 'only strings'),
        ('{"nbformat": 4, "nbformat_minor": 5, "cells": ["a cell"]}',
         'JSON object'),
    )
    for text, fragment in bad_inputs:
        try:
            core.NotebookDocument.from_json(text)
            raise AssertionError(" {!r} must raise ValueError".format(text[:40]))
        except ValueError as exc:
            assert fragment in str(exc), \
                "the message must name the problem ({}), got {!r}".format(
                    fragment, str(exc))
    print("  malformed input: 9 rejection shapes all raise ValueError with "
          "a message naming the problem")


def case_cell_operations_and_dirty_tracking():
    """add/remove/move/retype/clear behave exactly, and every mutation
    raises is_dirty while save()/from_json() clear it; save without a path
    raises ValueError."""
    document = core.NotebookDocument.from_json(_CANONICAL_NOTEBOOK)
    assert document.is_dirty is False, "a fresh read is clean"

    # add at the end and in the middle
    document.add_cell('markdown', source='# added at end')
    assert document.is_dirty is True and document.cell_count() == 4
    document.mark_clean()
    document.add_cell('code', index=1, source='middle')
    assert document.get_cell(1)['source'] == 'middle', "inserted at index 1"
    assert document.cell_count() == 5
    document.mark_clean()

    # move: up/down work, both boundaries refuse without reordering
    order_before = [document.get_cell(i)['source'] for i in range(5)]
    assert document.move_cell(0, -1) is False, "the first cell cannot move up"
    assert document.move_cell(4, +1) is False, "the last cell cannot move down"
    assert [document.get_cell(i)['source'] for i in range(5)] == order_before, \
        "a refused move leaves the order untouched"
    assert document.is_dirty is False
    assert document.move_cell(1, +1) is True, "an interior move succeeds"
    assert document.get_cell(2)['source'] == 'middle'
    document.mark_clean()

    # retype: code -> markdown drops the code extras, source survives;
    # markdown -> code regains empty outputs + None count
    document.set_cell_type(0, 'markdown')
    cell0 = document.get_cell(0)
    assert cell0['cell_type'] == 'markdown' and 'outputs' not in cell0 \
        and 'execution_count' not in cell0 \
        and cell0['source'].startswith('answer ='), \
        "code -> markdown keeps the source and drops outputs/count"
    document.set_cell_type(0, 'code')
    cell0 = document.get_cell(0)
    assert cell0['outputs'] == [] and cell0['execution_count'] is None, \
        "markdown -> code starts with empty outputs and None count"
    document.mark_clean()

    # clear outputs: one cell, then every code cell
    document.record_execution(0, 7, [{'output_type': 'stream',
                                      'name': 'stdout', 'text': 'x\n'}])
    assert document.get_cell(0)['execution_count'] == 7 and \
        len(document.get_cell(0)['outputs']) == 1
    document.clear_outputs(0)
    assert document.get_cell(0)['outputs'] == [] and \
        document.get_cell(0)['execution_count'] == 7, \
        "clearing outputs keeps the execution count"
    document.mark_clean()
    document.record_execution(0, 1, [{'output_type': 'stream',
                                      'name': 'stdout', 'text': 'x\n'}])
    document.record_execution(2, 2, [{'output_type': 'error', 'ename': 'E',
                                      'evalue': 'v', 'traceback': []}])
    document.clear_outputs()  # every code cell
    assert all(document.get_cell(i).get('outputs') == []
               for i in range(document.cell_count())
               if document.get_cell(i)['cell_type'] == 'code'), \
        "clear_outputs() with no index clears every code cell"

    # remove
    document.mark_clean()
    removed = document.remove_cell(1)
    assert removed['source'] == '# Title\n' and document.cell_count() == 4 \
        and document.is_dirty

    # save without a path raises; save to a temp file clears dirty
    document.mark_clean()
    try:
        document.save()
        raise AssertionError("saving a path-less notebook must raise")
    except ValueError as exc:
        assert 'no file path' in str(exc)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'roundtrip.ipynb')
        document.save(path)
        assert document.is_dirty is False
        reloaded = core.NotebookDocument.from_file(path)
        assert reloaded.to_json() == document.to_json(), \
            "file save -> load round-trips the exact text"
        assert reloaded.file_path == path and reloaded.is_dirty is False
    print("  cell ops: add/remove/move (boundary-refused)/retype/clear all "
          "exact; mutations dirty, save/load clean, path-less save raises")


def case_snippets_generated_text_pinned_exactly():
    """The eight templates generate their exact call lines: placeholder
    defaults per kind, repr-escaped titles (a quote-bearing title pinned
    through its escape), timestep only when nonzero, reference/spacing/
    properties only when provided, publish targets new_volume/new_roi/
    new_multiroi."""
    # placeholder defaults (the text/plain drag fallback)
    expected_defaults = {
        'import_volume': "volume_np = dfnb.import_volume_as_np('<volume title>')",
        'import_roi': "mask_np = dfnb.import_roi_as_np('<ROI title>')",
        'import_multiroi': "labels_np = dfnb.import_multiroi_as_np('<MultiROI title>')",
        'publish_volume': "new_volume = dfnb.publish_np_as_volume(volume_np, "
                          "title='Result volume')",
        'publish_roi': "new_roi = dfnb.publish_np_as_roi(mask_np, "
                       "title='Processed ROI')",
        'publish_multiroi': "new_multiroi = dfnb.publish_np_as_multiroi(labels_np, "
                            "title='New segmentation')",
        'import_properties': "props = dfnb.import_measured_properties('<MultiROI title>')",
        'export_properties': "dfnb.export_measured_properties("
                             "'<MultiROI title>', props_df)",
    }
    for kind, expected in expected_defaults.items():
        got = core.generate_snippet(kind)
        assert got == expected, \
            "{} placeholder mismatch:\n  {!r}\n!=\n  {!r}".format(kind, got, expected)

    # the ask's three worked examples, generated exactly
    assert core.generate_snippet('import_volume', source='Bone scan') == \
        "volume_np = dfnb.import_volume_as_np('Bone scan')"
    assert core.generate_snippet(
        'import_properties', source='Segmentation',
        properties=['Volume', 'Sphericity']) == \
        "props = dfnb.import_measured_properties('Segmentation', " \
        "properties=['Volume', 'Sphericity'])"
    assert core.generate_snippet('publish_roi', array='mask_np',
                                 title='Processed ROI',
                                 reference='Bone scan') == \
        "new_roi = dfnb.publish_np_as_roi(mask_np, title='Processed ROI', " \
        "reference='Bone scan')"

    # repr-escaping: a quote-bearing title survives cell execution (repr
    # picks single quotes around a string containing double quotes)
    quoted = core.generate_snippet('import_volume', variable="my scan",
                                   source='Bone scan "2024"', timestep=2)
    assert quoted == ("my_scan = dfnb.import_volume_as_np("
                      "'Bone scan \"2024\"', timestep=2)"), \
        "the quote-bearing title must be repr-escaped with a timestep: {!r}".format(quoted)

    # timestep omitted at 0, appended at nonzero (pinned above at 2)
    assert core.generate_snippet('import_roi', source='R',
                                 timestep=0) == \
        "mask_np = dfnb.import_roi_as_np('R')"

    # spacing only when provided, formatted as a 3-tuple
    assert core.generate_snippet(
        'publish_volume', array='v', title='T', spacing=(0.5, 0.25, 2)) == \
        "new_volume = dfnb.publish_np_as_volume(v, title='T', " \
        "spacing=(0.5, 0.25, 2))"
    assert core.generate_snippet('publish_volume', array='v') == \
        "new_volume = dfnb.publish_np_as_volume(v, title='Result volume')"

    # an explicit variable override survives sanitization; an empty
    # properties list means every slot (omitted)
    assert core.generate_snippet('import_multiroi', variable='Seg 1',
                                 source='Seg') == \
        "Seg_1 = dfnb.import_multiroi_as_np('Seg')"
    assert core.generate_snippet('import_properties', source='S',
                                 properties=[]) == \
        "props = dfnb.import_measured_properties('S')"

    try:
        core.generate_snippet('no_such_kind')
        raise AssertionError("an unknown kind must raise ValueError")
    except ValueError:
        pass
    print("  snippets: all eight kinds pinned exactly (placeholders, the "
          "ask's examples, repr-escaped quote titles, timestep 0-omitted, "
          "optional reference/spacing/properties)")


def case_export_properties_snippets_pinned():
    """The export kind's TWO generated lines, pinned exactly: mode A (write
    into an EXISTING MultiROI) is a bare ``dfnb.export_measured_properties``
    call -- no assignment target, the function returns None so the cell
    echoes nothing -- with the timestep appended only when nonzero and the
    properties variable sanitized; mode B (publish a NEW MultiROI from an
    array) is the publish_np_as_multiroi call carrying the properties
    variable, defaulting its target to new_mroi."""
    # the ask's pinned example (mode A)
    assert core.generate_snippet('export_properties', source='Segmentation',
                                 properties='props_df') == \
        "dfnb.export_measured_properties('Segmentation', props_df)"

    # timestep appended only when nonzero
    assert core.generate_snippet('export_properties', source='Seg',
                                 timestep=2) == \
        "dfnb.export_measured_properties('Seg', props_df, timestep=2)"

    # the properties VARIABLE is sanitized (it is kernel-side Python, not a
    # title): 'props df' -> props_df
    assert core.generate_snippet('export_properties', source='S',
                                 properties='props df') == \
        "dfnb.export_measured_properties('S', props_df)"

    # mode B: the publish call itself, target new_mroi (no publish noun is
    # registered for the export kind -- the multiroi fallback)
    assert core.generate_snippet('export_properties', array='mask_np',
                                 title='Measured segmentation',
                                 reference='Bone scan',
                                 properties='props_df') == \
        ("new_mroi = dfnb.publish_np_as_multiroi(mask_np, "
         "title='Measured segmentation', reference='Bone scan', "
         "properties=props_df)")

    # mode B with an explicit target override, and spacing when given
    assert core.generate_snippet('export_properties', array='mask_np',
                                 variable='labeled seg',
                                 title='Measured segmentation',
                                 spacing=(0.5, 0.5, 0.5)) == \
        ("labeled_seg = dfnb.publish_np_as_multiroi(mask_np, "
         "title='Measured segmentation', spacing=(0.5, 0.5, 0.5))")

    # round 10: the wipe flag threads through mode A ONLY
    assert core.generate_snippet('export_properties', source='Seg',
                                 properties='props_df', wipe=True) == \
        "dfnb.export_measured_properties('Seg', props_df, wipe=True)"

    # mode B NEVER renders the wipe: a wipe alongside an array is
    # meaningless (a fresh MultiROI has no existing properties) and is
    # simply not emitted
    publish_line = core.generate_snippet('export_properties', array='mask_np',
                                         title='Measured segmentation',
                                         properties='props_df', wipe=True)
    assert publish_line == \
        ("new_mroi = dfnb.publish_np_as_multiroi(mask_np, "
         "title='Measured segmentation', properties=props_df)"), \
        publish_line
    assert 'wipe' not in publish_line, \
        "the publish line must carry no wipe kwarg, got {!r}".format(
            publish_line)
    print("  export snippets: mode A bare call (placeholder, the ask's "
          "example, timestep, props sanitization, wipe=True) + mode B "
          "publish line (new_mroi default, override, spacing, no wipe)")


def case_identifier_sanitization():
    """sanitize_identifier: spaces/punctuation -> '_', repeats collapse,
    a leading digit gets a leading '_', a Python keyword gets a trailing
    '_', empty falls back -- and the result is always a valid identifier."""
    cases = {
        'Bone scan': 'Bone_scan',
        'Segmentation (final)': 'Segmentation_final',
        'ROI---1 ... 2': 'ROI_1_2',
        '3D volume': '_3D_volume',
        'import': 'import_',
        '': 'value',
        '   ': 'value',
        'clean_name_123': 'clean_name_123',
    }
    for name, expected in cases.items():
        got = core.sanitize_identifier(name)
        assert got == expected, \
            "{!r} -> {!r}, expected {!r}".format(name, got, expected)
        assert got.isidentifier() and not keyword.iskeyword(got), \
            "{!r} must sanitize to a valid non-keyword identifier".format(name)
    assert core.sanitize_identifier('x', fallback='labels') == 'x'
    assert core.sanitize_identifier('', fallback='labels') == 'labels'
    print("  identifier sanitization: spaces/punct collapse, leading-digit "
          "and keyword guarded, empty falls back")


def case_registry_roundtrip_and_tolerant_defaults():
    """The registry: a missing file and a corrupt file both load as the
    {'last_used_folder': None} default (never raise); save then load
    round-trips a folder string; an empty/garbage folder value normalizes
    to None. Uses a temp file -- never %LOCALAPPDATA%."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'registry.json')
        assert core.registry_load(path) == {'last_used_folder': None}, \
            "a missing registry file loads the default"
        with open(path, 'w') as handle:
            handle.write('{not json')
        assert core.registry_load(path) == {'last_used_folder': None}, \
            "a corrupt registry file loads the default"
        with open(path, 'w') as handle:
            handle.write('["a list"]')
        assert core.registry_load(path) == {'last_used_folder': None}, \
            "a non-object registry file loads the default"
        core.registry_save({'last_used_folder': r'C:\data\notebooks'}, path)
        assert core.registry_load(path) == \
            {'last_used_folder': r'C:\data\notebooks'}, \
            "save -> load round-trips the folder"
        core.registry_save({'last_used_folder': 42}, path)
        assert core.registry_load(path) == {'last_used_folder': None}, \
            "a non-string folder value normalizes to None"
    print("  registry: missing/corrupt/non-object files default to None, "
          "save -> load round-trips, garbage values normalize")


# ---------------------------------------------------------------------------
# cases: the round-8 bridge protocol (ORS-free)
# ---------------------------------------------------------------------------

def case_protocol_value_roundtrips_pins_scalars_arrays_and_tables():
    """encode_value -> decode_value round-trips every bridge-serializable
    shape: scalars, lists, nested dicts, numpy arrays (npy-encoded, exact
    dtype/shape/values), bytes, and pandas DataFrames (float and object
    columns, a non-default index). A tuple is documented to arrive as a
    list (JSON has no tuple)."""
    parts = []
    cases = [
        (42, 42),
        (3.5, 3.5),
        ('text', 'text'),
        (True, True),
        (None, None),
        ([1, 'a', None], [1, 'a', None]),
        ({'k': [1, {'deep': 2}]}, {'k': [1, {'deep': 2}]}),
        ((1, 2), [1, 2]),          # tuple -> list across JSON
        (b'\x00\x01\xff', b'\x00\x01\xff'),
    ]
    for original, expected in cases:
        got = protocol.decode_value(protocol.encode_value(original, parts),
                                    parts)
        assert got == expected, \
            "round-trip mismatch for {!r}: got {!r}".format(original, got)
    parts = []
    array = np.arange(24, dtype=np.uint8).reshape(2, 3, 4)
    got = protocol.decode_value(
        protocol.encode_value({'a': array, 'f': np.array([[1.5, -2.5]],
                                                          np.float32)}, parts),
        parts)
    assert got['a'].dtype == np.uint8 and got['a'].shape == (2, 3, 4) \
        and np.array_equal(got['a'], array), "the uint8 3-D array survives"
    assert got['f'].dtype == np.float32 and \
        np.array_equal(got['f'], np.array([[1.5, -2.5]], np.float32)), \
        "the float32 array survives with its dtype"
    parts = []
    table = pd.DataFrame({'count': [1.0, 2.0, 3.0],
                          'name': ['a', 'b', 'c']},
                         index=[10, 20, 30])
    got = protocol.decode_value(protocol.encode_value(table, parts), parts)
    assert isinstance(got, pd.DataFrame), "a DataFrame comes back a DataFrame"
    assert got.equals(table), \
        "the DataFrame survives: columns, values and index"
    print("  protocol values: scalars/lists/dicts/bytes/tuple->list, "
          "uint8 + float32 arrays npy-exact, DataFrame columns+index")


def case_protocol_value_rejections_pinned():
    """The protocol REFUSES what cannot cross the bridge, with a clear
    ValueError: an unsupported type (a set), a dict carrying the reserved
    ``__dfnb__`` marker key, and a pickled-object array (npy refuses
    pickles)."""
    parts = []
    for bad in ({1, 2}, np.array([object(), object()])):
        try:
            protocol.encode_value(bad, parts)
            raise AssertionError("{!r} must be refused".format(bad))
        except ValueError:
            pass
    try:
        protocol.encode_value({'ok': 1, '__dfnb__': 2}, parts)
        raise AssertionError("the reserved marker key must be refused")
    except ValueError:
        pass
    print("  protocol refusals: sets, object-dtype arrays and the reserved "
          "__dfnb__ key all raise ValueError")


def case_protocol_frame_layout_and_chunked_reader_pins():
    """The wire format, pinned byte-for-byte: an 8-byte little-endian
    length prefix counting the REST of the frame, a 4-byte JSON length,
    JSON first then the blob parts; the FrameReader reassembles frames fed
    at arbitrary chunk boundaries (two frames back to back; a large
    frame's blob chunked at 777 bytes); the oversize guard refuses a
    lying length prefix; a truncated blob is a ValueError."""
    parts = []
    envelope = {'type': 'request', 'id': 7, 'method': 'echo',
                'params': {'a': 1}}
    frame = protocol.encode_frame(envelope, parts)
    total = struct.unpack('<Q', frame[:8])[0]
    assert total == len(frame) - 8, \
        "the 8-byte prefix counts the rest of the frame, got {}".format(total)
    json_len = struct.unpack('<I', frame[8:12])[0]
    assert json.loads(frame[12:12 + json_len].decode('utf-8'))['type'] == \
        'request', "the JSON envelope follows the JSON length"
    assert json_len + 12 == len(frame), "no blob parts -> nothing after JSON"

    # a frame WITH a blob part: the array rides after the JSON
    parts = []
    payload = protocol.encode_value({'array': np.arange(300000)},
                                    parts)
    framed = protocol.encode_frame({'type': 'response', 'id': 1, 'ok': True,
                                    'payload': payload}, parts)
    assert len(framed) > 300000, "the blob is inside the frame"

    reader = protocol.FrameReader()
    got = []
    data = framed + protocol.encode_frame(envelope, [])
    for start in range(0, len(data), 777):
        got.extend(reader.feed(data[start:start + 777]))
    assert len(got) == 2, "two frames back to back, chunked at 777"
    assert got[0][0]['type'] == 'response' and got[0][0]['id'] == 1
    assert got[1][0] == envelope, "the second frame decodes exactly"
    decoded = protocol.decode_value(got[0][0]['payload'], got[0][1])
    assert np.array_equal(decoded['array'], np.arange(300000)), \
        "the large blob survives the chunked delivery"

    reader = protocol.FrameReader()
    try:
        reader.feed(struct.pack('<Q', protocol.MAX_FRAME_BYTES + 1))
        raise AssertionError("a lying length prefix must be refused")
    except ValueError:
        pass

    parts = []
    payload = protocol.encode_value({'array': np.ones(64, np.uint8)}, parts)
    framed = protocol.encode_frame({'type': 'response', 'id': 2, 'ok': True,
                                    'payload': payload}, parts)
    try:
        protocol.decode_payload(framed[:-5])
        raise AssertionError("a truncated blob must be refused")
    except ValueError:
        pass
    print("  protocol frames: 8-byte LE prefix + 4-byte JSON length pinned, "
          "chunked reassembly (2 frames, 300k blob @777), oversize + "
          "truncation refused")


def case_bridge_pipe_name_and_version_pins():
    """The pipe name is PID-suffixed (two Dragonfly instances never
    collide) and the protocol version is 1."""
    assert protocol.BRIDGE_NAME_PREFIX == 'dfnb_bridge_'
    assert protocol.bridge_pipe_name() == \
        'dfnb_bridge_{}'.format(os.getpid()), \
        "the default pipe name carries THIS process's pid"
    assert protocol.bridge_pipe_name(4242) == 'dfnb_bridge_4242'
    assert protocol.PROTOCOL_VERSION == 1
    print("  pipe name: 'dfnb_bridge_<pid>' pinned (default = own pid); "
          "protocol version 1")


# ---------------------------------------------------------------------------
# cases: the dfnb client against the fake in-process server
# ---------------------------------------------------------------------------

def case_client_handshake_version_and_pipe_name_pins():
    """connect() completes the version handshake: a matching WELCOME marks
    the client connected (and emits connected_signal); a mismatched one
    refuses the connection and leaves the client disconnected. The client
    remembers the pipe name it connected to."""
    events = []
    client, server = _make_client({'echo': _echo})
    client.connected_signal.connect(lambda: events.append('connected'))
    assert client.connect('dfnb_bridge_ok') is True
    assert client.is_connected() is True
    assert client.pipe_name == 'dfnb_bridge_ok'
    assert events == ['connected'], \
        "connected_signal emitted exactly once, got {!r}".format(events)

    server_bad = _FakeServer({})
    server_bad.welcome_version = protocol.PROTOCOL_VERSION + 999
    client_bad = dfnb.DfnbClient(
        pipe_name='dfnb_bridge_bad',
        transport_class=type('Bound', (_FakeTransport,),
                             {'server': server_bad}))
    bad_events = []
    client_bad.connected_signal.connect(lambda: bad_events.append(1))
    assert client_bad.connect('dfnb_bridge_bad') is False, \
        "a version-mismatched WELCOME refuses the connection"
    assert client_bad.is_connected() is False and not bad_events
    print("  handshake: matching WELCOME connects (+signal, name kept), "
          "mismatched version refuses")


def case_client_call_roundtrip_error_mapping_and_progress_pins():
    """call(): the payload round-trips through the real frames; an error
    envelope raises the ORIGINAL exception class with the SAME message
    (ValueError, RuntimeError), an exotic class becomes RuntimeError
    'Class: message'; PROGRESS frames reach ``progress_callback`` in
    order, and the fake server received the request exactly as sent."""
    client, server = _make_client({
        'echo': _echo,
        'boom_value': _boom_value,
        'boom_runtime': _boom_runtime,
        'boom_exotic': _boom_exotic,
        'progressing': _progress_then_ok,
    })
    assert client.connect('dfnb_bridge_calls') is True

    assert client.call('echo', {'x': [1, 2], 's': 'hi'}) == \
        {'echo': {'x': [1, 2], 's': 'hi'}}
    assert server.requests == [('echo', {'x': [1, 2], 's': 'hi'})], \
        "the request crossed as sent"

    progress_seen = []
    client.progress_callback = lambda caption, step, of: \
        progress_seen.append((caption, step, of))
    assert client.call('progressing', {}) == {'done': True}
    assert progress_seen == [('Working', 1, 3), ('Working', 2, 3)], \
        "progress frames forwarded in order, got {!r}".format(progress_seen)
    client.progress_callback = None

    for method, expected_class, fragment in (
            ('boom_value', ValueError, 'bad value input'),
            ('boom_runtime', RuntimeError, 'the operation failed halfway'),
            ('boom_exotic', RuntimeError, "KeyError: 'missing'")):
        try:
            client.call(method, {})
            raise AssertionError("{} must raise".format(method))
        except expected_class as exc:
            assert fragment in str(exc), \
                "{} message must carry {!r}, got {!r}".format(
                    method, fragment, str(exc))
    print("  calls: payload round-trip, ValueError/RuntimeError rebuilt with "
          "their class, exotic -> RuntimeError, progress frames in order")


def case_client_public_methods_validate_and_conform_client_side():
    """The eleven public methods validate CLIENT-side (fail fast, no round
    trip, same texts as the data layer) and conform arrays before
    shipping: a bad source/timestep/title/spacing/shape raises here, an
    int32 volume ships as uint8, a float64 volume ships as float32, bool
    labels ship as the labels-count dtype, and an empty ROI mask never
    reaches the wire."""
    client, server = _make_client({
        'import_volume_as_np': _echo,
        'publish_np_as_volume': _published,
        'publish_np_as_roi': _published,
        'publish_np_as_multiroi': _published,
    })
    assert client.connect('dfnb_bridge_valid') is True

    for bad_call, fragment in (
            (lambda: client.import_volume_as_np(42),
             'cannot cross the bridge'),
            (lambda: client.import_volume_as_np('V', timestep=-1),
             'timestep must be a non-negative integer'),
            (lambda: client.import_volume_as_np('V', timestep=1.5),
             'timestep must be a non-negative integer'),
            (lambda: client.publish_np_as_volume(np.zeros((2, 2, 2, 2))),
             'must be a 2-D (Y, X) or 3-D (Z, Y, X) numpy array'),
            (lambda: client.publish_np_as_volume(np.ones((2, 2), np.uint8),
                                                 spacing=(0, 1, 1)),
             'spacing must be None or a sequence of three positive numbers'),
            (lambda: client.publish_np_as_volume(np.ones((2, 2), np.uint8),
                                                 title='   '),
             'title must be a non-empty string'),
            (lambda: client.publish_np_as_roi(np.zeros((4, 4), np.uint8)),
             'contains no foreground voxels'),
            (lambda: client.publish_np_as_multiroi(
                np.array([[0.5, 1.1]], float)),
             'contains non-integer labels'),
            # the round-10 wipe flag is a strict bool, refused before the
            # wire (a truthy non-bool must never fire a replace-all wipe)
            (lambda: client.export_measured_properties('V', {'A': [1.0]},
                                                       wipe=1),
             'wipe must be a boolean'),
            # the round-12 delete_object refuses a non-str, non-handle
            # source with its siblings' cannot-cross message
            (lambda: client.delete_object(42),
             'cannot cross the bridge'),
    ):
        try:
            bad_call()
            raise AssertionError("{!r} must raise ValueError".format(bad_call))
        except ValueError as exc:
            assert fragment in str(exc), \
                "the message must name the problem ({}), got {!r}".format(
                    fragment, str(exc))
    assert server.requests == [], \
        "a client-side refusal must never reach the wire, got {!r}".format(
            server.requests)

    # the conforming, pinned through what the server actually received
    client.publish_np_as_volume(np.array([[0, 200]], np.int32),
                                title='Result volume')
    shipped = server.requests[-1][1]['array']
    assert shipped.dtype == np.uint8 and np.array_equal(shipped,
                                                        [[0, 200]]), \
        "a small int32 volume ships as uint8"
    client.publish_np_as_volume(np.array([[1.5, -2.0]], np.float64))
    assert server.requests[-1][1]['array'].dtype == np.float32, \
        "a float64 volume ships as float32"
    client.publish_np_as_multiroi(np.array([[0, 1], [1, 0]], bool))
    assert server.requests[-1][1]['array'].dtype == np.uint8, \
        "boolean labels with count 1 ship as uint8"
    client.publish_np_as_multiroi(np.array([[[0], [300]]], np.int64))
    assert server.requests[-1][1]['array'].dtype == np.uint16, \
        "300 labels ship as uint16"
    client.publish_np_as_volume(np.ones((2, 2), np.uint8), title='T',
                                spacing=(2.0, 1.0, 0.5))
    assert list(server.requests[-1][1]['spacing']) == [2.0, 1.0, 0.5], \
        "the validated spacing crosses (a tuple arrives as a JSON list)"
    print("  public methods: 10 client-side refusals never reach the wire; "
          "int32->uint8, float64->float32, bool labels->uint8, 300 labels->"
          "uint16 conforming pinned through the received requests")


def case_client_publish_handle_roundtrips_through_source_params():
    """A publish returns a PublishedObject handle ({'guid', 'title',
    'class_name'}, attribute access included) and that handle is
    re-accepted by a later source parameter -- it crosses back as the
    same identifier dict, which is how a published object is used by the
    next cell without any ORS object in this process."""
    client, server = _make_client({
        'publish_np_as_volume': _published,
        'import_volume_as_np': _imported,
    })
    assert client.connect('dfnb_bridge_handle') is True
    handle = client.publish_np_as_volume(np.ones((2, 2), np.uint8))
    assert isinstance(handle, dfnb.PublishedObject) and isinstance(handle, dict)
    assert handle.guid == 'guid-1' and handle.title == 'Result volume' \
        and handle.class_name == 'Channel', \
        "attribute access works on the handle"
    client.import_volume_as_np(handle)
    assert server.requests[-1][1]['source'] == \
        {'guid': 'guid-1', 'title': 'Result volume',
         'class_name': 'Channel'}, \
        "the handle re-enters as the identifier dict"
    print("  publish handle: PublishedObject with attribute access, "
          "re-accepted by a later source parameter as the GUID dict")


def case_client_delete_object_conformance_pins():
    """delete_object, the 11th dfnb function (round 12): the method exists
    on the client, a non-str non-handle source is refused client-side with
    its siblings' cannot-cross message (no round trip), and the success
    path ships the same by-source request envelope the other by-source
    methods produce -- a raw title string crosses verbatim, a handle dict
    crosses as the identifier dict."""
    client, server = _make_client({'delete_object': _echo})
    assert client.connect('dfnb_bridge_delete') is True
    assert callable(getattr(client, 'delete_object', None)), \
        "delete_object must exist on the client"
    try:
        client.delete_object(42)
        raise AssertionError("a non-str, non-handle source must raise")
    except ValueError as exc:
        assert 'cannot cross the bridge' in str(exc), \
            f"the siblings' refusal message, got {str(exc)!r}"
    assert server.requests == [], \
        "a client-side refusal must never reach the wire"

    client.delete_object('tp_stale_volume')
    assert server.requests[-1] == \
        ('delete_object', {'source': 'tp_stale_volume'}), \
        f"the by-source envelope, got {server.requests[-1]!r}"
    client.delete_object({'guid': 'guid-9', 'title': 'stale title',
                          'class_name': 'ROI'})
    assert server.requests[-1][1]['source'] == \
        {'guid': 'guid-9', 'title': 'stale title', 'class_name': 'ROI'}, \
        f"a handle crosses as the identifier dict, got {server.requests[-1]!r}"
    print("  delete_object: exists on the client, refuses non-str/non-handle "
          "sources before the wire, ships the by-source envelope (raw title "
          "verbatim, handle as the identifier dict)")


def case_client_disconnect_message_pins():
    """A lost bridge raises a CLEAR RuntimeError carrying the reconnect
    guidance: through a mid-write failure, through the disconnected
    pre-check, from ensure_connected(), and after close() -- the exact
    user-facing text every time."""
    client, server = _make_client({'echo': _echo})
    assert client.connect('dfnb_bridge_drop') is True

    # the peer dies mid-call: the write fails, the connection is marked lost
    client._transport.close()
    try:
        client.call('echo', {})
        raise AssertionError("a call on a dead transport must raise")
    except RuntimeError as exc:
        assert 'The Dragonfly bridge is disconnected' in str(exc)
    assert client.is_connected() is False, "the loss is registered"

    # the pre-check: every later call fails with the same message
    for attempt in (lambda: client.call('echo', {}),
                    lambda: client.ensure_connected()):
        try:
            attempt()
            raise AssertionError("the disconnected client must raise")
        except RuntimeError as exc:
            assert 'The Dragonfly bridge is disconnected' in str(exc)

    client.close()
    try:
        client.call('echo', {})
        raise AssertionError("a closed client must raise")
    except RuntimeError as exc:
        assert 'The Dragonfly bridge is disconnected' in str(exc)
    print("  disconnect: mid-call death, pre-check, ensure_connected and "
          "closed client all raise the clear reconnect-guidance message")


def case_client_reconnect_discovery_pins():
    """poll_reconnect's candidate discovery: every ``dfnb_bridge_*`` pipe
    the machine exposes, the preferred one first (os.listdir is faked --
    the real namespace scan itself is exercised by the tier-3 spawn
    test's reconnect)."""
    entries = ['srvstuff', 'dfnb_bridge_99', 'chrome som',
               'dfnb_bridge_5', 'dfnb_bridgeX_1']
    original_listdir = os.listdir

    def fake_listdir(path):
        assert path == '//./pipe/'
        return list(entries)

    os.listdir = fake_listdir
    try:
        assert dfnb.list_pipe_candidates() == ['dfnb_bridge_99',
                                               'dfnb_bridge_5'], \
            "only real bridge pipes, in namespace order"
        assert dfnb.list_pipe_candidates('dfnb_bridge_5') == \
            ['dfnb_bridge_5', 'dfnb_bridge_99'], \
            "the preferred pipe is tried first"
    finally:
        os.listdir = original_listdir
    print("  reconnect discovery: prefix filter exact, preferred pipe "
          "first")


def case_client_marshal_box_delivers_result_or_error():
    """The worker->owner marshal box: ``_on_marshal_call`` runs the
    transaction into the box (the result, or the exception OBJECT) and
    sets the event -- the exact contract the kernel worker's wait-slices
    rely on. Called directly here (no event loop needed; the full
    queued-signal delivery is pinned end-to-end by the tier-3 spawn
    test)."""
    client, server = _make_client({'echo': _echo, 'boom_value': _boom_value})
    assert client.connect('dfnb_bridge_marshal') is True
    ok_box = dfnb._MarshalCall(client._next_seq(), 'echo', {'k': 1})
    assert not ok_box.event.is_set()
    client._on_marshal_call(ok_box)
    assert ok_box.event.is_set() and ok_box.box.get('result') == \
        {'echo': {'k': 1}}
    bad_box = dfnb._MarshalCall(client._next_seq(), 'boom_value', {})
    client._on_marshal_call(bad_box)
    assert bad_box.event.is_set()
    assert isinstance(bad_box.box.get('error'), ValueError) and \
        'bad value input' in str(bad_box.box['error'])
    print("  marshal box: result and exception both land in the box with "
          "the event set (the worker's wait-slice contract)")


def case_client_cancel_mechanism_sends_cancel_frame_and_raises_interrupt():
    """The Stop path's client half, pinned mechanically: a transaction
    held open by a delayed fake server, the cancel flag added the way the
    worker-interrupt path adds it (``_on_marshal_cancel``/the marshal
    box), and the transaction answers by sending a REAL cancel frame on
    the wire and raising KeyboardInterrupt with the exact user-facing
    message in the CALLING thread. The transaction runs on a plain thread
    here (a unit test of the loop's mechanics; the full worker->owner
    interrupt path is pinned end-to-end by the tier-3 spawn test's Stop
    pin)."""
    client, server = _make_client({'wait_for_cancel': _never_called})
    server.delay_methods.add('wait_for_cancel')
    assert client.connect('dfnb_bridge_cancel') is True
    outcome = {}

    def run():
        try:
            client._transaction('wait_for_cancel', {})
            outcome['result'] = 'returned'
        except BaseException as exc:
            outcome['error'] = exc

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and client._in_flight is None:
        time.sleep(0.01)
    try:
        assert client._in_flight is not None, \
            "the transaction never went in flight"
        in_flight = client._in_flight
        client._cancelled.add(in_flight)   # what _on_marshal_cancel does
        thread.join(5.0)
        assert not thread.is_alive(), "the cancel never landed"
        assert 'result' not in outcome
        assert isinstance(outcome.get('error'), KeyboardInterrupt), \
            "the cancel raises KeyboardInterrupt, got {!r}".format(
                outcome.get('error'))
        assert str(outcome['error']) == 'Operation cancelled by user.', \
            "the exact user-facing cancel message"
        server.pump()
        assert server.cancels == [in_flight], \
            "a REAL cancel frame crossed for the in-flight request, " \
            "got {!r}".format(server.cancels)
    finally:
        client.close()
    print("  cancel: delayed request + flag -> real cancel frame on the "
          "wire + KeyboardInterrupt('Operation cancelled by user.') in the "
          "calling thread")


# ---------------------------------------------------------------------------
# round 16: custom Python environments (notebook_envs, pure stdlib)
# ---------------------------------------------------------------------------

def case_envs_names_and_discovery_rules():
    """Environment names and discovery: validate_name accepts letter/
    digit/'.'/'_'/'-' names (stripped), refuses empties, bad characters
    and collisions with an actionable message each; list_environments
    counts only directories that carry pyvenv.cfg AND the venv
    interpreter (a half-created or hand-dropped folder is skipped),
    sorted case-insensitively."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "envs")
        os.makedirs(root)
        assert envs.validate_name("scipy-lab", root) == "scipy-lab"
        assert envs.validate_name("  padded  ", root) == "padded"
        for bad, fragment in (
                ("", "cannot be empty"),
                ("   ", "cannot be empty"),
                ("-leading", "may use letters"),
                ("has space", "may use letters"),
                ("sl/ash", "may use letters"),
                ("x" * 65, "may use letters")):
            try:
                envs.validate_name(bad, root)
                raise AssertionError("accepted {!r}".format(bad))
            except ValueError as exc:
                assert fragment in str(exc), (bad, str(exc))
        os.makedirs(os.path.join(root, "taken"))
        try:
            envs.validate_name("taken", root)
            raise AssertionError("collision accepted")
        except ValueError as exc:
            assert "already exists" in str(exc)

        assert envs.list_environments(root) == []
        # no pyvenv.cfg: skipped even though it looks like a venv
        os.makedirs(os.path.join(root, "no_cfg", "Scripts"))
        open(os.path.join(root, "no_cfg", "Scripts",
                          os.path.basename(envs.venv_python("x"))),
             "w").close()
        # pyvenv.cfg but no interpreter: skipped (cannot run pip)
        os.makedirs(os.path.join(root, "no_py"))
        open(os.path.join(root, "no_py", "pyvenv.cfg"), "w").close()
        for name in ("Beta", "alpha"):
            good = os.path.join(root, name)
            os.makedirs(os.path.join(good, "Scripts"))
            open(os.path.join(good, "pyvenv.cfg"), "w").close()
            open(envs.venv_python(good), "w").close()
        assert envs.list_environments(root) == ["alpha", "Beta"], \
            "valid environments, sorted case-insensitively"
    print("  names: strip/accept/refuse (empty, characters, collision) "
          "+ discovery skips cfg-less and interpreter-less dirs")


def case_envs_in_use_lock_lifecycle():
    """The in-use lock: acquire holds (the probe says so), a
    same-process second acquire is refused with the actionable message,
    release frees it, release without a hold is a False no-op. A stale
    marker FILE alone (no lock) is NOT in use: the LOCK is the signal,
    which is what makes the lock crash-safe (the OS frees it at process
    exit). The CROSS-process half (another notebook window) is pinned
    with a real second process that holds the lock for 3 s."""
    with tempfile.TemporaryDirectory() as tmp:
        venv = os.path.join(tmp, "envx")
        os.makedirs(venv)
        assert envs.is_environment_in_use(venv) is False
        open(envs.in_use_marker_path(venv), "w").close()  # stale file
        assert envs.is_environment_in_use(venv) is False, \
            "the marker file alone must not mean in use"
        envs.acquire_in_use(venv)
        assert envs.is_environment_in_use(venv) is True
        try:
            envs.acquire_in_use(venv)
            raise AssertionError("double acquire allowed")
        except RuntimeError as exc:
            assert "already in use in this notebook window" in str(exc)
        assert envs.release_in_use(venv) is True
        assert envs.is_environment_in_use(venv) is False
        assert envs.release_in_use(venv) is False

        # cross-process: a child holds the lock for 3 s; the parent's
        # probe must see it held, and free again once the child exits
        child_code = (
            "import sys, time\n"
            "sys.path.insert(0, {root!r})\n"
            "from DragonflyNotebook import notebook_envs as ne\n"
            "ne.acquire_in_use({venv!r})\n"
            "print('HELD', flush=True)\n"
            "time.sleep(3)\n").format(root=_ROOT, venv=venv)
        proc = subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, env=_CHILD_ENV,
            creationflags=_NO_WINDOW)
        try:
            first = proc.stdout.readline()
            assert first.strip() == "HELD", first
            assert envs.is_environment_in_use(venv) is True, \
                "the child process's hold must be visible across processes"
        finally:
            proc.wait(30)
        assert envs.is_environment_in_use(venv) is False, \
            "the lock frees when the holding process exits (crash-safe)"
    print("  lock: same-process refuse + cross-process hold via the OS "
          "byte lock + release/exit frees it")


def case_bridge_lazy_numpy_proxies_in_a_child():
    """The lazy-numpy proxies (dfnb_client + protocol since round 16) in
    a child process (a fake numpy at the front of sys.path must never
    poison this suite's own numpy): the protocol module imports WITHOUT
    numpy, the first np touch binds whatever the FRONT of sys.path
    supplies, and reset_lazy_numpy re-arms the proxy so the next touch
    re-imports instead of holding the bound module object. The proxies
    order numpy after the DLL-directory registration; the
    custom-environment cells no longer share this process's numpy at
    all (they run in a subprocess on the venv's own interpreter), so
    the proxy contract left to pin is exactly this binding discipline."""
    with tempfile.TemporaryDirectory() as tmp:
        fake_site = os.path.join(tmp, "FakeSite")
        fake = os.path.join(fake_site, "numpy")
        os.makedirs(fake)
        with open(os.path.join(fake, "__init__.py"), "w") as handle:
            handle.write("MARK = 'FAKE'\n")

        child_code = (
            "import sys\n"
            "sys.path.insert(0, {root!r})\n"
            "sys.path.insert(0, {fake_site!r})\n"
            "from DragonflyNotebook import notebook_bridge_protocol "
            "as protocol\n"
            "from DragonflyNotebook import dfnb_client\n"
            "assert 'numpy' not in sys.modules, \\\n"
            "    'protocol import must not bind numpy'\n"
            "assert protocol.np.MARK == 'FAKE', \\\n"
            "    'the first touch binds the front of sys.path'\n"
            "import numpy\n"
            "assert numpy is protocol.np, 'the proxy rebinds the global'\n"
            "assert getattr(numpy, 'MARK', None) == 'FAKE'\n"
            "protocol.reset_lazy_numpy()\n"
            "dfnb_client.reset_lazy_numpy()\n"
            "assert type(protocol.np).__name__ == '_LazyNumpy', \\\n"
            "    'the protocol proxy was re-armed'\n"
            "assert type(dfnb_client.np).__name__ == '_LazyNumpy', \\\n"
            "    'the client proxy was re-armed too'\n"
            "del sys.modules['numpy']  # the next touch must re-import\n"
            "assert protocol.np.MARK == 'FAKE', \\\n"
            "    'the re-armed touch re-imports rather than rebinding'\n"
            "sys.path.remove({fake_site!r})\n"
            "del sys.modules['numpy']\n"
            "protocol.reset_lazy_numpy()\n"
            "assert protocol.np.__version__  # the real numpy re-imports\n"
            "import numpy as real\n"
            "assert getattr(real, 'MARK', None) is None and \\\n"
            "    hasattr(real, 'zeros')\n"
            "print('CHILD-OK')\n").format(root=_ROOT, fake_site=fake_site)
        proc = subprocess.run(
            [sys.executable, "-c", child_code],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, timeout=120,
            env=_CHILD_ENV, creationflags=_NO_WINDOW)
        assert proc.returncode == 0, \
            "the proxy child failed:\n{}".format(proc.stdout)
        assert "CHILD-OK" in proc.stdout
    print("  lazy numpy: no bind at import, first touch binds the front "
          "of sys.path, reset re-arms, the next touch re-imports")


def case_env_kernel_tail_stop_survives():
    """The environment runner's whole-cell interrupt guard
    (dfnb_env_kernel._execute): a stop armed at the TAIL of a cell --
    after the guarded code region ended, while the epilogue still runs --
    must end as one interrupted cell_done, never as a crash that takes
    the child process and its namespace down (the round-16 review's P1).
    Pinned in both placements: the interrupt landing inside the guarded
    code region (the normal mid-cell stop) and one landing in the
    epilogue (armed from _flush_figures, which runs after the guarded
    region -- exactly the tail-of-cell race), then a plain run proving
    the module still serves."""
    import threading as _threading

    from DragonflyNotebook import dfnb_env_kernel as env_kernel

    class _Pipe:
        def __init__(self):
            self.lines = []
        def write(self, text):
            self.lines.append(text)
        def flush(self):
            pass

    pipe = _Pipe()
    saved_stdout = env_kernel._real_stdout
    saved_count = env_kernel._execution_count
    env_kernel._real_stdout = pipe
    env_kernel._seed_ns()
    env_kernel._ns['arm_here'] = (
        lambda: env_kernel._arm_interrupt(_threading.get_ident()))
    try:
        # mid-cell placement: armed from inside the guarded code region
        env_kernel._execute('arm_here()\n1 + 1')
        events = [json.loads(line) for line in pipe.lines]
        pipe.lines = []
        assert [event['op'] for event in events] == ['error', 'cell_done'] \
            and events[0]['ename'] == 'KeyboardInterrupt' \
            and events[1]['interrupted'] is True, events

        # epilogue placement: armed from _flush_figures (it runs AFTER
        # the guarded region), so the interrupt lands between the
        # regions -- the tail-of-cell race -- and must end the SAME way
        real_flush = env_kernel._flush_figures

        def flush_armer():
            env_kernel._arm_interrupt(_threading.get_ident())

        env_kernel._flush_figures = flush_armer
        try:
            env_kernel._execute('1 + 1')
        finally:
            env_kernel._flush_figures = real_flush
        events = [json.loads(line) for line in pipe.lines]
        pipe.lines = []
        assert [event['op'] for event in events] == ['error', 'cell_done'] \
            and events[0]['ename'] == 'KeyboardInterrupt' \
            and events[1]['interrupted'] is True, events

        # the module is still usable: a plain run produces its result
        env_kernel._execute('2 + 2')
        events = [json.loads(line) for line in pipe.lines]
        pipe.lines = []
        assert [event['op'] for event in events] == ['result', 'cell_done'] \
            and events[0]['data']['text/plain'] == '4' \
            and events[1]['interrupted'] is False, events
    finally:
        env_kernel._real_stdout = saved_stdout
        env_kernel._execution_count = saved_count
        env_kernel._seed_ns()
    print("  tail stop: a stop armed at the cell's end ends as one "
          "interrupted cell_done, the child and its namespace survive")


def case_envs_creation_and_pip_argv_pins():
    """The long-run argv shapes + the bootstrap's runpy plumbing for
    real: venv_creation_command is the distribution's STOCK interpreter
    (embedded_python: sys.base_prefix's python.exe, NOT sys.executable --
    the notebook application runs as Dragonfly's licensed
    pythonnoconsole.exe, and a venv COPIES the creating exe into Scripts,
    where its QlmLicenseLib dependentAssembly cannot resolve: WinError
    14001, observed live in round 16, probe-reproduced) with -c
    <bootstrap> venv <dir> (the bootstrap patches the CHILD's Popen to
    CREATE_NO_WINDOW and runpy-runs the module with argv[0] set);
    pip_command (the pip button's field, round 17: the FULL command as
    typed) and pip_install_command (its install contract, what the
    creation flow's base install uses) run the VENV's python with
    --no-input, the pip-version-check disabled, and the normalized
    subcommand behind them (launcher spellings stripped, update run as
    install --upgrade, uninstall auto-confirmed: --no-input turns pip's
    confirmation question into a hard error, probe-proven on pip 23.0.1).
    Both are then RUN for real through the bootstrap on cheap no-write
    commands (venv --help, pip --version) to pin that the plumbing works
    end to end. The real creation + a real offline install are pinned by
    tier 3."""
    bootstrap = envs.venv_creation_command(r"C:\nowhere\venv1")[2]
    assert "0x08000000" in bootstrap and \
        "subprocess.Popen.__init__" in bootstrap and \
        'run_name="__main__"' in bootstrap

    stock = envs.embedded_python()
    assert stock == os.path.join(sys.base_prefix, "python.exe") and \
        os.path.isfile(stock), stock
    argv = envs.venv_creation_command(r"C:\nowhere\venv1")
    assert argv[0] == stock
    assert argv[1] == "-c" and argv[2] == bootstrap
    assert argv[3] == "venv" and argv[4] == r"C:\nowhere\venv1"

    venv = os.path.join(tempfile.gettempdir(), "dfnb_t1_pin_venv")
    argv = envs.pip_install_command(venv, ["scipy", "scikit-image>=0.22"])
    assert argv[0] == envs.venv_python(venv)
    assert argv[1] == "-c" and argv[2] == bootstrap
    assert argv[3:6] == ["pip", "--no-input",
                         "--disable-pip-version-check"]
    assert argv[6:] == ["install", "scipy", "scikit-image>=0.22"]

    # the pip button's field: the FULL command as typed, every spelling
    # normalized to the subcommand behind the fixed flags
    def tail(command):
        return envs.pip_command(venv, command)[3:]

    assert tail("pip install yfinance") == \
        ["pip", "--no-input", "--disable-pip-version-check",
         "install", "yfinance"]
    assert tail("pip3 list")[-1] == "list"
    assert tail("pip3.11 install x") == \
        ["pip", "--no-input", "--disable-pip-version-check",
         "install", "x"]
    assert tail("python -m pip install x")[-2:] == ["install", "x"]
    assert tail("install numpy")[-2:] == ["install", "numpy"]
    assert tail("pip update numpy")[-3:] == \
        ["install", "--upgrade", "numpy"]
    assert tail("pip upgrade numpy")[-3:] == \
        ["install", "--upgrade", "numpy"]
    assert tail("pip uninstall yfinance")[-3:] == \
        ["uninstall", "yfinance", "-y"]
    assert tail("pip uninstall -y yfinance")[-3:] == \
        ["uninstall", "-y", "yfinance"]
    assert tail("pip uninstall --yes yfinance")[-3:] == \
        ["uninstall", "--yes", "yfinance"]
    if os.name == "nt":
        # the same nt split the runner uses: backslashes verbatim
        # (posix splitting would hand pip C:wheelspkg.whl), quoted
        # spans one word
        assert tail('pip install "C:\\wheels\\pkg.whl"')[-2:] == \
            ["install", "C:\\wheels\\pkg.whl"]
    try:
        envs.pip_command(venv, "pip")
    except ValueError:
        pass
    else:
        raise AssertionError("a bare launcher must refuse (nothing to "
                             "run)")

    # the runpy plumbing, run for real: a usage text means the module
    # executed as __main__ with argv[0] restored
    proc = subprocess.run(
        [sys.executable, "-c", bootstrap, "venv", "--help"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, text=True, timeout=90,
        env=_CHILD_ENV, creationflags=_NO_WINDOW)
    assert proc.returncode == 0, proc.stdout
    assert "usage" in proc.stdout.lower()
    proc = subprocess.run(
        [sys.executable, "-c", bootstrap, "pip", "--version"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, text=True, timeout=90,
        env=_CHILD_ENV, creationflags=_NO_WINDOW)
    assert proc.returncode == 0, proc.stdout
    assert "pip" in proc.stdout
    print("  argv pins: -c bootstrap + module layout for venv and the "
          "pip command builder, the full-command normalizations, run "
          "for real on venv --help and pip --version")


def case_env_kernel_cell_syntax_pins():
    """The runner's cell syntax (round 17): ``!command`` lines rewrite
    to the internal helper ONLY when plain parsing fails (a ``!``
    inside a string literal is plain Python and must stay verbatim,
    including inside an indented block); ``%`` magics are not rewritten
    at all (``%pip`` included, round 17's rule: pip runs from the
    toolbar's pip button, not from a cell) and get the explanatory
    error naming both the embedded kernel and the button; a genuine
    syntax error raises as itself."""
    import ast as _ast

    from DragonflyNotebook import dfnb_env_kernel as env_kernel

    def rewritten_calls(code):
        return [
            (node.func.id, node.args[0].value)
            for node in _ast.walk(env_kernel._parse_cell(code))
            if isinstance(node, _ast.Call)
            and isinstance(node.func, _ast.Name)
            and node.func.id == '_dfnb_system']

    # plain Python parses as itself
    tree = env_kernel._parse_cell('1 + 1')
    assert isinstance(tree, _ast.Module) and \
        isinstance(tree.body[0].value, _ast.BinOp)

    assert rewritten_calls('!dir /b\nx = 1') == \
        [('_dfnb_system', 'dir /b')]
    assert rewritten_calls('if True:\n    !echo hi\n') == \
        [('_dfnb_system', 'echo hi')]

    # a ! inside a string is plain Python: the string stays verbatim
    code = 'text = """\n!not a command\n"""\nassert "!x" in "!x"'
    constants = [
        node.value for node in _ast.walk(env_kernel._parse_cell(code))
        if isinstance(node, _ast.Constant) and isinstance(node.value, str)]
    assert constants == ['\n!not a command\n', '!x', '!x'], constants

    # every % magic -- pip included (round 17: pip runs from the
    # button, never from a cell) -- gets the explanatory error, not a
    # bare SyntaxError and never a rewrite
    for magic in ('%pip install yfinance', '%pip3 list', '%pip',
                  '%matplotlib inline'):
        try:
            env_kernel._parse_cell(magic)
        except SyntaxError as exc:
            assert 'magic' in str(exc) and 'pip' in str(exc) and \
                'button' in str(exc), str(exc)
        else:
            raise AssertionError('the magic line parsed: ' + magic)

    # a plain syntax error raises as itself
    try:
        env_kernel._parse_cell('1 +')
    except SyntaxError:
        pass
    else:
        raise AssertionError('the broken cell parsed')
    print('  cell syntax: !command rewrites (strings verbatim), every '
          '% magic explained -- pip included, pointed at the button, '
          'never rewritten -- and syntax errors honest')


def case_env_kernel_shell_escape_runs():
    """The runner's ``!`` executed for real (round 17): ``!python -V``
    reroutes to THIS interpreter (the venv's Scripts directory is never
    on a notebook child's PATH, so a bare ``python`` word must not be a
    PATH lookup) and its version line streams as cell output; pip is
    REFUSED in cells (round 17's rule: pip runs only from the toolbar's
    pip button): ``!pip --version`` and ``!python -m pip --version``
    each complete with one clean cell_done, no error op, no spawned
    child -- their whole output is the single stderr note naming the
    button (offline commands -- no network)."""
    from DragonflyNotebook import dfnb_env_kernel as env_kernel

    class _Pipe:
        def __init__(self):
            self.lines = []

        def write(self, text):
            self.lines.append(text)

        def flush(self):
            pass

    pipe = _Pipe()
    saved_stdout = env_kernel._real_stdout
    saved_count = env_kernel._execution_count
    env_kernel._real_stdout = pipe
    env_kernel._seed_ns()
    try:
        env_kernel._execute('!python -V')
        events = [json.loads(line) for line in pipe.lines]
        pipe.lines = []
        assert events[-1]['op'] == 'cell_done' and \
            events[-1]['interrupted'] is False and \
            not [event for event in events if event['op'] == 'error'], \
            events
        text = ''.join(event.get('text', '') for event in events
                       if event['op'] == 'stream')
        assert text.strip().startswith('Python'), repr(text)

        for refused in ('!pip --version', '!python -m pip --version'):
            env_kernel._execute(refused)
            events = [json.loads(line) for line in pipe.lines]
            pipe.lines = []
            assert events[-1]['op'] == 'cell_done' and \
                events[-1]['interrupted'] is False and \
                not [event for event in events
                     if event['op'] == 'error'], (refused, events)
            notes = [event for event in events
                     if event['op'] == 'stream']
            assert len(notes) == 1 and \
                notes[0].get('name') == 'stderr' and \
                'pip button' in notes[0].get('text', ''), (refused, notes)
    finally:
        env_kernel._real_stdout = saved_stdout
        env_kernel._execution_count = saved_count
        env_kernel._seed_ns()
    print('  shell escapes: !python -V ran through the interpreter '
          'reroute and streamed as cell output; !pip and !python -m pip '
          'were refused -- one button note on stderr, nothing spawned')


def case_env_kernel_polling_watch_dispatches():
    """The round-17 Windows stdin watcher (``_watch_stdin_polling``):
    a real pipe's lines dispatch exactly like the blocking reader did
    (run lines to the command queue, stop latched between runs, dfnb
    responses to their queue) and the writer closing ends the watcher
    (PeekNamedPipe fails with ERROR_BROKEN_PIPE -- probe-proven, the
    parent's death surfaces). The REASON the reader polls -- a blocked
    ReadFile on the stdin pipe deadlocks a concurrent OpenBLAS-linked
    DLL load, the user's stuck yfinance cell -- needs a real venv child
    and is receipted live in the round, not in this process."""
    import threading as _threading

    from DragonflyNotebook import dfnb_env_kernel as env_kernel

    read_fd, write_fd = os.pipe()
    saved_pending = env_kernel._stop_pending
    env_kernel._stop_pending = False
    try:
        os.write(write_fd, json.dumps(
            {'op': 'run', 'code': 'pass'}).encode('ascii') + b'\n')
        os.write(write_fd, json.dumps(
            {'op': 'stop'}).encode('ascii') + b'\n')
        os.write(write_fd, json.dumps(
            {'op': 'dfnb_response', 'seq': 1, 'ok': True,
             'result': None}).encode('ascii') + b'\n')
        os.close(write_fd)
        watcher = _threading.Thread(
            target=env_kernel._watch_stdin_polling, args=(read_fd,),
            daemon=True)
        watcher.start()
        watcher.join(10)
        assert not watcher.is_alive(), \
            'the watcher never returned (EOF not detected)'
        assert env_kernel._stop_pending is True, 'the stop did not latch'
        commands = []
        while not env_kernel._commands.empty():
            commands.append(env_kernel._commands.get_nowait())
        responses = []
        while not env_kernel._responses.empty():
            responses.append(env_kernel._responses.get_nowait())
        assert any(item['op'] == 'run' for item in commands), commands
        assert responses and responses[0].get('seq') == 1, responses
    finally:
        os.close(read_fd)
        env_kernel._stop_pending = saved_pending
        while not env_kernel._commands.empty():
            env_kernel._commands.get_nowait()
        while not env_kernel._responses.empty():
            env_kernel._responses.get_nowait()
    print('  polling stdin watch: lines dispatched, stop latched, the '
          'closed pipe ended the watcher')


def case_envs_round17_contract_pins():
    """Round 17's environment contract: BASE_PACKAGES is exactly numpy
    and pandas (the minimum the notebook's own data paths need:
    volumes cross as numpy arrays, measured properties as DataFrames),
    the delete entry's label and sentinel exist, ensure_runtime_dlls
    delivers the MACHINE'S misparked runtime DLL set into the venv's
    Scripts -- scan-driven, not name-keyed (the import tables of the
    base tree's extension modules, closed transitively, with API-set
    names and the already-loaded python3*.dll family excluded; round
    17's follow-up: a fix keyed to sqlite3.dll alone would miss
    whatever a different build parks elsewhere -- this one also parks
    tcl86t.dll, which broke tkinter) -- pinned against the machine's
    real sqlite3.dll as the known instance and guarded for hosts
    without the distribution, idempotent, and the runner arms its own
    exe directory for dependent-DLL loads -- the copy and the arm are
    BOTH required (probe-proven: a base-tree pyd's altered search never
    reaches the exe dir on its own)."""
    assert envs.BASE_PACKAGES == ('numpy', 'pandas'), envs.BASE_PACKAGES
    assert envs.DELETE_ENV_LABEL == '- Delete Environment'
    assert envs.DELETE_ENV_SENTINEL == '__dfnb_delete_environment__'

    from DragonflyNotebook import dfnb_env_kernel as env_kernel
    if os.name == 'nt':
        assert env_kernel._OWN_DLL_DIR == os.path.dirname(
            os.path.abspath(sys.executable)), env_kernel._OWN_DLL_DIR
    else:
        assert env_kernel._OWN_DLL_DIR is None, \
            env_kernel._OWN_DLL_DIR

    venv = os.path.join(tempfile.gettempdir(), 'dfnb_t1_dll_venv')
    shutil.rmtree(venv, ignore_errors=True)
    try:
        base = envs._venv_base_prefix(venv)
        expected = envs._missing_runtime_dlls(base)
        envs.ensure_runtime_dlls(venv)
        scripts = os.path.join(venv, 'Scripts')
        if os.path.isdir(scripts):
            delivered = {name for name in os.listdir(scripts)
                         if name.lower().endswith('.dll')}
        else:
            delivered = set()
        # every resolvable name the scan found is delivered, verbatim
        # (and nothing else lands in Scripts)
        assert delivered == {name for name, source in expected.items()
                             if source is not None}, (
            delivered, sorted(expected))
        # the exclusions hold on whatever this machine found: no
        # API-set name and no python3*.dll family member is copied
        for name in delivered:
            assert not name.lower().startswith(
                ('api-ms-', 'ext-ms-')), name
            assert not (name.lower().startswith('python')
                        and name.lower().endswith('.dll')), name
        # the known instance: this machine's real sqlite3.dll, verbatim
        source = None
        for runtime_dir in (os.path.join(base, 'Library', 'bin'),
                             os.path.join(os.path.dirname(base), 'libs')):
            candidate = os.path.join(runtime_dir, 'sqlite3.dll')
            if os.path.isfile(candidate):
                source = candidate
                break
        target = os.path.join(scripts, 'sqlite3.dll')
        if source is not None:
            assert os.path.isfile(target), target
            assert os.path.getsize(target) == os.path.getsize(source)
            # idempotent: a second call changes nothing and raises nothing
            envs.ensure_runtime_dlls(venv)
            assert os.path.getsize(target) == os.path.getsize(source)
        else:
            assert 'sqlite3.dll' not in delivered, \
                'a DLL appeared without a source'
    finally:
        shutil.rmtree(venv, ignore_errors=True)
    print('  round-17 contract: base packages numpy+pandas, the delete '
          'entry, the scan-discovered runtime DLL set delivered into the '
          'venv Scripts + the runner\'s own-directory arm')


def case_env_kernel_stop_during_command():
    """Round 17 review, the P0: a Stop that lands while a '!command'
    cell runs must END the run interrupted -- the watcher's
    one-shot async interrupt propagates out of _run_system_command
    into _execute's existing guard (the swallowed version reported the
    interrupted run as a clean completion while the cell's remaining
    statements kept executing on). The command's own process is the
    documented orphan -- and its forwarder is rebound to the drop
    sink BEFORE the raise, so the orphan's late writes land nowhere,
    never in a later run's outputs (the reviewer's misattribution
    window)."""
    from DragonflyNotebook import dfnb_env_kernel as env_kernel

    released = threading.Event()
    late_lines = []

    class _FakeProc:
        returncode = 0

        def poll(self):
            return None

        @property
        def stdout(self):
            return _orphan_output()

    def _orphan_output():
        # the orphan prints ONLY after the interrupt (event-ordered:
        # no sleep races in the critical path)
        released.wait(5)
        yield 'late line\n'

    class _RecordingDrop(env_kernel._DropStream):
        def write(self, data):
            late_lines.append(data)

    def _fake_popen(*args, **kwargs):
        return _FakeProc()

    def _raise_interrupt(seconds):
        raise KeyboardInterrupt

    saved_popen = env_kernel.subprocess.Popen
    saved_sleep = env_kernel.time.sleep
    saved_drop = env_kernel._DropStream
    try:
        env_kernel.subprocess.Popen = _fake_popen
        env_kernel.time.sleep = _raise_interrupt
        env_kernel._DropStream = _RecordingDrop
        raised = False
        try:
            env_kernel._run_system_command('ping -n 100 localhost')
        except KeyboardInterrupt:
            raised = True
    finally:
        # restored BEFORE the grace wait below: time is a shared module,
        # and the patched sleep would raise inside this very case
        env_kernel.subprocess.Popen = saved_popen
        env_kernel.time.sleep = saved_sleep
        env_kernel._DropStream = saved_drop
    assert raised, 'the interrupt must propagate out of the wait'
    # the orphan prints now (the rebind happened inside the interrupt
    # path, before the raise): the write must land in the drop sink,
    # not in a live stream
    released.set()
    time.sleep(0.3)
    assert late_lines == ['late line\n'], late_lines
    print('  stop during !command: the interrupt propagates (the run '
          'ends interrupted; the orphan\'s late writes land in the '
          'drop sink)')


def case_env_kernel_reroute_words_and_nt_paths():
    """Round 17: the python reroute splits the command line WITHOUT
    posix backslash-eating on Windows -- a '!python C:\\wheels\\pkg.py'
    cell reroutes to this interpreter with the path VERBATIM (posix
    splitting had handed it C:wheelspkg.py), a quoted span stays one
    token with one matching quote pair stripped, 'python' reroutes to
    sys.executable (casing and a '.exe' suffix alike) -- and PIP IS
    REFUSED (round 17's rule: pip runs only from the toolbar's pip
    button): every launcher spelling ('!pip install ...', '!pip3
    ...', '!pip3.11 ...', '!python -m pip ...') PLUS the spellings a
    case-exact check leaked to PATH (round 17 review: the shell
    resolves names case-insensitively -- '!pip.exe', '!PIP', the
    '!py -m pip' and '!py -3 -m pip' launcher shapes, and an
    unquotable line whose first words are still a launcher) write the
    button note to stderr and spawn NOTHING."""
    from DragonflyNotebook import dfnb_env_kernel as env_kernel

    class _FakeProc:
        returncode = 0

        def poll(self):
            return 0

        @property
        def stdout(self):
            return iter([])

    captured = []
    notes = []

    class _ErrPipe:
        def write(self, text):
            notes.append(text)

        def flush(self):
            pass

    def _fake_popen(*args, **kwargs):
        captured.append(args[0])
        return _FakeProc()

    saved_popen = env_kernel.subprocess.Popen
    saved_stderr = sys.stderr
    try:
        env_kernel.subprocess.Popen = _fake_popen
        sys.stderr = _ErrPipe()
        for refused in ('pip install C:\\wheels\\pkg.whl',
                        'pip install "my pkg==1.0"',
                        'pip3.11 list',
                        'python -m pip install numpy',
                        # the review's leaked spellings: casing, the
                        # .exe suffix, the py launcher, and a line shlex
                        # cannot split at all
                        'pip.exe install x',
                        'PIP install x',
                        'py -m pip install x',
                        'py -3 -m pip install numpy',
                        'python.exe -m pip3 list',
                        'pip install "unbalanced',
                        # the unclosed quote BEFORE the launcher, the
                        # shape _parse_cell hands over with its '!'
                        # already stripped
                        '"pip install x'):
            captured_before = len(captured)
            env_kernel._run_system_command(refused)
            assert len(captured) == captured_before, \
                (refused, captured[-1])
        env_kernel._run_system_command('python -m timeit')
        env_kernel._run_system_command('python C:\\wheels\\pkg.py')
        env_kernel._run_system_command('python "my script.py" arg')
        env_kernel._run_system_command('Python.exe -V')
    finally:
        env_kernel.subprocess.Popen = saved_popen
        sys.stderr = saved_stderr
    assert len(notes) == 11 and \
        all('pip button' in note for note in notes), notes
    assert len(captured) == 4, captured
    first, second, third, fourth = captured
    assert first == [sys.executable, '-m', 'timeit'], first
    assert third == [sys.executable, 'my script.py', 'arg'], third
    assert fourth == [sys.executable, '-V'], fourth
    if os.name == 'nt':
        assert second[0] == sys.executable and \
            'C:\\wheels\\pkg.py' in second, second
    print('  reroute words: every pip spelling (casing, .exe, py '
          'launcher, unquotable) refused with the button note and no '
          'spawn; nt paths verbatim, quoted spans whole, python '
          'reroutes to sys.executable')


def case_env_kernel_magic_mask_limitation():
    """Round 17 review, the documented limitation: on a cell whose
    plain parse fails, a '%' line that is really inside a multi-line
    string reads as a magic line to the rewriter, so the explanatory
    magic error MASKS the cell's real syntax error (here the broken
    'def broken(:'). The pin documents today's behavior; the honest fix
    needs error attribution on unparseable source."""
    from DragonflyNotebook import dfnb_env_kernel as env_kernel
    cell = 'note = """\n%time the timer\n"""\ndef broken(:'
    raised = None
    try:
        env_kernel._parse_cell(cell)
    except SyntaxError as exc:
        raised = exc
    assert raised is not None, 'the broken cell must raise'
    assert 'magic' in str(raised) and 'pip' in str(raised), str(raised)
    print('  magic-mask limitation: the multi-line string\'s % line '
          'masks the real syntax error with the magic explanation '
          '(documented in _parse_cell)')


def case_text_contract_no_dashes_and_no_attribution():
    """The permanent text contract over every committed text file: no em
    dash (U+2014) and no en dash (U+2013) anywhere, and no phrase that
    attributes a change to a person's request -- comments, docstrings
    and README prose state the behavior and the rationale directly
    instead. Scans DragonflyNotebook/*.py, README.md, Install.ps1,
    LICENSE, .gitignore, examples/*.ipynb, docs/*.md and
    verification/*.py (this file included) as UTF-8 and fails listing
    every violation (file, line, fragment); the attribution check runs
    per line and again over each file's text joined and
    whitespace-normalized, so a phrase split across a line break is
    caught too (the dash check stays per line)."""
    # the phrases are assembled at runtime: this file is scanned too, so
    # the banned literals must never appear in it verbatim
    who = 'the use' + 'r'
    banned_phrases = (
        who + ' asked for', who + ' asked', who + ' asks',
        who + "'s ask", who + ' requested', who + "'s request",
        'user has ' + 'asked', 'i was ' + 'requested', 'based on the ' + 'request',
        'per the ' + 'request', who + ' reported', who + "'s report",
        who + "'s contract", who + "'s instruction", who + "'s word",
        who + "'s go-ahead", who + "'s follow-up",
        who + "'s clarification",
        'requested by the ' + who, who + ' wanted',
        'on the ' + who + "'s behalf",
    )
    targets = []
    for entry in sorted(os.listdir(os.path.join(_ROOT, 'DragonflyNotebook'))):
        if entry.endswith('.py'):
            targets.append(os.path.join('DragonflyNotebook', entry))
    targets.append('README.md')
    targets.append('Install.ps1')
    targets.append('LICENSE')
    targets.append('.gitignore')
    docs_dir = os.path.join(_ROOT, 'docs')
    if os.path.isdir(docs_dir):
        for entry in sorted(os.listdir(docs_dir)):
            if entry.endswith('.md'):
                targets.append(os.path.join('docs', entry))
    for entry in sorted(os.listdir(os.path.join(_ROOT, 'examples'))):
        if entry.endswith('.ipynb'):
            targets.append(os.path.join('examples', entry))
    for entry in sorted(os.listdir(_HERE)):
        if entry.endswith('.py'):
            targets.append(os.path.join('verification', entry))
    violations = []
    for relative in targets:
        path = os.path.join(_ROOT, relative)
        if not os.path.isfile(path):
            continue
        with open(path, 'r', encoding='utf-8') as handle:
            lines = handle.read().splitlines()
        seen_per_line = set()
        for lineno, line in enumerate(lines, 1):
            if '\u2014' in line or '\u2013' in line:
                violations.append('{}:{}: em/en dash in {!r}'.format(
                    relative, lineno, line.strip()[:120]))
                continue
            normalized = line.lower().replace('\u2019', "'")
            for phrase in banned_phrases:
                if phrase in normalized:
                    seen_per_line.add(phrase)
                    violations.append(
                        '{}:{}: attribution phrase {!r} in {!r}'.format(
                            relative, lineno, phrase, line.strip()[:120]))
        # backstop: a phrase can hide across a line break, which the
        # per-line scan never sees, so scan the whole text joined
        joined_normalized = re.sub(
            r'\s+', ' ', ' '.join(lines)).lower().replace('\u2019', "'")
        for phrase in banned_phrases:
            if phrase in joined_normalized and phrase not in seen_per_line:
                start = joined_normalized.find(phrase)
                fragment = joined_normalized[
                    max(0, start - 60):start + len(phrase) + 60]
                violations.append(
                    '{}: attribution phrase {!r} in {!r} (may span a '
                    'line break)'.format(relative, phrase, fragment))
    assert not violations, \
        'the text contract is violated:\n' + '\n'.join(violations)
    print('  text contract: {} files scanned (DragonflyNotebook, README.md, '
          'Install.ps1, LICENSE, .gitignore, examples, docs, verification); '
          'no em/en dash, no attribution phrase'.format(len(targets)))


CASES = (
    ("writer: fresh notebook is canonical nbformat 4.5 with the Dragonfly "
     "kernelspec",
     case_canonical_writer_pins_nbformat_45_and_kernelspec),
    ("round trip: byte-identical on canonical input, idempotent",
     case_canonical_roundtrip_is_byte_identical_and_idempotent),
    ("tolerant reader: str/list sources, generated ids, verbatim outputs",
     case_tolerant_reader_str_and_list_sources_and_generated_ids),
    ("tolerant reader: execution_count normalization",
     case_tolerant_reader_normalizes_float_counts_and_keeps_them),
    ("reader rejections: malformed inputs raise ValueError naming the problem",
     case_reader_rejects_malformed_notebooks_with_clear_errors),
    ("cell operations + dirty tracking",
     case_cell_operations_and_dirty_tracking),
    ("snippets: the eight templates' exact generated text",
     case_snippets_generated_text_pinned_exactly),
    ("export snippets: mode A bare call + mode B publish line",
     case_export_properties_snippets_pinned),
    ("identifier sanitization",
     case_identifier_sanitization),
    ("registry: tolerant defaults + round trip",
     case_registry_roundtrip_and_tolerant_defaults),
    ("bridge protocol: value round-trips (arrays, DataFrames, bytes)",
     case_protocol_value_roundtrips_pins_scalars_arrays_and_tables),
    ("bridge protocol: refusals (sets, pickled arrays, the marker key)",
     case_protocol_value_rejections_pinned),
    ("bridge protocol: frame layout + chunked reader + guards",
     case_protocol_frame_layout_and_chunked_reader_pins),
    ("bridge protocol: PID-suffixed pipe name + version",
     case_bridge_pipe_name_and_version_pins),
    ("dfnb client: version handshake + pipe name",
     case_client_handshake_version_and_pipe_name_pins),
    ("dfnb client: call round-trip, error-envelope mapping, progress",
     case_client_call_roundtrip_error_mapping_and_progress_pins),
    ("dfnb client: client-side validation + array conforming",
     case_client_public_methods_validate_and_conform_client_side),
    ("dfnb client: publish handle re-accepted as a source parameter",
     case_client_publish_handle_roundtrips_through_source_params),
    ("dfnb client: delete_object conformance (refusals + envelope)",
     case_client_delete_object_conformance_pins),
    ("dfnb client: the clear disconnect message",
     case_client_disconnect_message_pins),
    ("dfnb client: reconnect candidate discovery",
     case_client_reconnect_discovery_pins),
    ("dfnb client: the worker->owner marshal box",
     case_client_marshal_box_delivers_result_or_error),
    ("dfnb client: cancel mechanism (real cancel frame + interrupt)",
     case_client_cancel_mechanism_sends_cancel_frame_and_raises_interrupt),
    ("environments: names + discovery rules",
     case_envs_names_and_discovery_rules),
    ("environments: the in-use lock (same process + cross process)",
     case_envs_in_use_lock_lifecycle),
    ("bridge lazy-numpy proxies: no bind at import, reset re-arms (child)",
     case_bridge_lazy_numpy_proxies_in_a_child),
    ("environment runner: a tail-of-cell stop ends interrupted, the "
     "child survives",
     case_env_kernel_tail_stop_survives),
    ("environments: creation/pip argv pins + the bootstrap run for real",
     case_envs_creation_and_pip_argv_pins),
    ("environment runner: !command cell syntax, every % magic explained "
     "(pip included)",
     case_env_kernel_cell_syntax_pins),
    ("environment runner: !python -V runs, !pip refused with the button "
     "note",
     case_env_kernel_shell_escape_runs),
    ("environment runner: the polling stdin watch dispatches and ends",
     case_env_kernel_polling_watch_dispatches),
    ("environments: round-17 contract (base packages, delete entry, "
     "the scan-discovered runtime DLL set)",
     case_envs_round17_contract_pins),
    ("environment runner: a Stop during !command propagates the "
     "interrupt (the orphan stays)",
     case_env_kernel_stop_during_command),
    ("environment runner: reroute words keep nt paths verbatim; pip "
     "refused with the button note",
     case_env_kernel_reroute_words_and_nt_paths),
    ("environment runner: the %magic-masks-syntax-error limitation",
     case_env_kernel_magic_mask_limitation),
    ("text contract: no em/en dash, no request attribution in any "
     "committed text file",
     case_text_contract_no_dashes_and_no_attribution),
)


def run_all():
    """The suite loop minus sys.exit -- the single entry point, because
    test_dragonflynotebook_datapath.py (tier 2) imports this module and calls
    run_all() inside the ORS process. Returns True only when every case
    passed."""
    ok = True
    print("=== Dragonfly Notebook core: .ipynb model + snippets + the "
          "round-8 bridge layer ===", flush=True)
    for name, fn in CASES:
        print(f"\n[{name}]", flush=True)
        try:
            fn()
            print(f"[{name}] PASSED", flush=True)
        except Exception:
            traceback.print_exc()
            print(f"[{name}] FAILED", flush=True)
            ok = False
    return ok


def main():
    ok = run_all()
    print("\n" + ("SUITE PASSED" if ok else "SUITE FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
