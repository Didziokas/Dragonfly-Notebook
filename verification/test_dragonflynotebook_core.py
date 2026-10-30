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

Run (tier 1 is framework-free: straight on the embedded interpreter, no
dfpy_ors launcher needed):

  uv run --no-project --python "C:\Program Files\Dragonfly\Python_env\python.exe" \
      verification/test_dragonflynotebook_core.py
"""

import importlib.util
import json
import keyword
import os
import struct
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
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


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
