r"""The Dragonfly Notebook bridge protocol -- shared by both sides, ORS-free.

Round 8 moves the notebook editor and its IPython kernel into a standalone
application process; the Dragonfly plugin shrinks to a launcher plus a
bridge service. The two processes talk over a Qt local (named) pipe --
``QLocalServer`` on the Dragonfly side, ``QLocalSocket`` on the app side --
and this module owns everything about the wire format:

* **Framing** -- every frame is an 8-byte little-endian unsigned payload
  length followed by exactly that many payload bytes. The payload itself is
  a 4-byte little-endian unsigned JSON length, the JSON envelope bytes, and
  then the optional binary blob. The blob is a sequence of parts, each
  4-byte little-endian length-prefixed; the envelope's ``blob_parts`` list
  names each part's kind in order (``'npy'`` = ``numpy.save`` bytes for an
  ndarray, ``'json'`` = a JSON-encoded plain value, ``'bytes'`` = raw
  bytes). The 8-byte outer length lets a single frame carry multi-megabyte
  arrays without ambiguity; readers must never trust it beyond
  ``MAX_FRAME_BYTES``.

* **Values** -- :func:`encode_value` turns a Python value into something
  JSON-encodable, appending binary parts to a list as needed; a numpy
  ndarray becomes one ``'npy'`` part, a pandas DataFrame one ``'npy'`` part
  per column plus one for the index (column names and order travel in the
  JSON marker), plain scalars / lists / dicts stay JSON. :func:`decode_value`
  is the exact inverse, so arrays and DataFrames cross the pipe without
  losing dtype, shape, column names or column order.

* **Envelopes** -- JSON objects carrying a ``'type'``:

  ``{'type': 'hello', 'version': N}``
      the app's first frame on a fresh connection;
  ``{'type': 'welcome', 'version': N}``
      the service's answer; the handshake is complete when the app has it
      and the versions match;
  ``{'type': 'request', 'id': N, 'method': str, 'params': {...}}``
      one dfnb call from the app; ``params`` may carry arrays and
      DataFrames via the parts mechanism;
  ``{'type': 'progress', 'id': N, 'caption': str, 'step': int, 'of': int}``
      a handler's progress step, forwarded to the app;
  ``{'type': 'cancel', 'id': N}``
      the app gave up on request N (the kernel worker was interrupted
      mid-call); the service flags the request so the handler's next
      cancel checkpoint raises;
  ``{'type': 'response', 'id': N, 'ok': True, 'payload': {...}}`` or
  ``{'type': 'response', 'id': N, 'ok': False,
    'error_class': str, 'message': str}``
      the handler's result or its failure. The error envelope carries the
      exception CLASS NAME plus its message, so the app-side client can
      re-raise a ``ValueError`` as a ``ValueError`` and a
      ``RuntimeError`` as a ``RuntimeError`` -- their texts are
      user-facing contracts and must survive the trip exactly.

Nothing in this module imports ORS or PyQt: it is pure Python + numpy +
pandas, importable by the plugin, by the standalone app, and by the
framework-free tier-1 suite.

The ``KMP_DUPLICATE_LIB_OK`` guard and the two Dragonfly DLL directories
are set up before the numpy import, exactly per the platform gauntlet, so
importing this module on the embedded interpreter is safe in every host.
"""

import io
import json
import os
import struct

os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'True')

# The embedded-interpreter / headless rule: numpy's shared libs live in
# Dragonfly's two directories; register them (win32, guarded) before the
# import below. In the Dragonfly process and the app process they exist
# already; keeping the handles in a module list stops the GC from removing
# the registration.
if os.name == 'nt':
    _dll_dir_handles = []
    for _dir in (r'C:\Program Files\Dragonfly\libs',
                 r'C:\Program Files\Dragonfly\Python_env\Library\bin'):
        if os.path.isdir(_dir):
            try:
                _dll_dir_handles.append(os.add_dll_directory(_dir))
            except OSError:
                pass

import numpy as np  # noqa: E402  (after the guard / dll dirs, per the rules)

PROTOCOL_VERSION = 1

#: The Qt local-server name prefix; the full name is ``<prefix><pid>`` so
#: two Dragonfly instances never collide.
BRIDGE_NAME_PREFIX = 'dfnb_bridge_'

#: A safety ceiling on one frame's payload (1 GiB): a corrupted length
#: prefix must fail fast instead of asking for an absurd allocation.
MAX_FRAME_BYTES = 1 << 30

_HEADER = struct.Struct('<Q')
_JSON_LEN = struct.Struct('<I')
_PART_LEN = struct.Struct('<I')

HELLO = 'hello'
WELCOME = 'welcome'
REQUEST = 'request'
PROGRESS = 'progress'
CANCEL = 'cancel'
RESPONSE = 'response'

_DFN_MARKER = '__dfnb__'


def bridge_pipe_name(pid=None):
    """The pipe/server name for one Dragonfly process: ``dfnb_bridge_<pid>``."""
    return BRIDGE_NAME_PREFIX + str(os.getpid() if pid is None else int(pid))


# ---------------------------------------------------------------------------
# values: Python <-> (plain JSON value + binary parts)
# ---------------------------------------------------------------------------

def encode_value(value, parts):
    """Returns a JSON-encodable stand-in for ``value``, appending one part
    per binary payload to ``parts`` (a list of ``(kind, bytes)`` pairs).

    * ``numpy.ndarray`` -> one ``'npy'`` part (``numpy.save`` bytes,
      pickle-free); the marker carries the part index.
    * ``pandas.DataFrame`` -> one ``'npy'`` part per column + one for the
      index; the marker carries the column names, their part indices and
      the index's part index, so names and order survive exactly.
    * ``bytes`` -> one ``'bytes'`` part.
    * ``None`` / bool / int / float / str and lists / tuples / dicts of
      those stay JSON (tuples arrive as lists).
    """
    if isinstance(value, np.ndarray):
        marker = {_DFN_MARKER: 'ndarray', 'part': _append_array_part(
            value, parts)}
        return marker
    if _is_dataframe(value):
        column_markers = [
            {'name': str(name),
             'part': _append_series_part(value[str(name)], parts)}
            for name in value.columns]
        return {_DFN_MARKER: 'dataframe',
                'columns': column_markers,
                'index': {'part': _append_index_part(value.index, parts)}}
    if isinstance(value, (bytes, bytearray)):
        marker = {_DFN_MARKER: 'bytes', 'part': len(parts)}
        parts.append(('bytes', bytes(value)))
        return marker
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [encode_value(item, parts) for item in value]
    if isinstance(value, dict):
        if _DFN_MARKER in value:
            raise ValueError(
                'dfnb cannot transfer a dict with the reserved {!r} key '
                'across the bridge.'.format(_DFN_MARKER))
        return {str(key): encode_value(item, parts)
                for key, item in value.items()}
    raise ValueError(
        'dfnb cannot transfer a value of type {} across the bridge; '
        'convert it to a numpy array, a DataFrame or plain Python data '
        'first.'.format(type(value).__name__))


def _is_dataframe(value):
    """True for a pandas DataFrame without importing pandas eagerly: the
    class lives in a ``pandas.*`` module and is named ``DataFrame``."""
    cls = type(value)
    return cls.__name__ == 'DataFrame' and \
        cls.__module__.split('.', 1)[0] == 'pandas'


def _append_array_part(array, parts):
    """One ndarray as an ``'npy'`` part (``numpy.save`` bytes, pickle-free);
    returns the part index."""
    buffer = io.BytesIO()
    try:
        np.save(buffer, array, allow_pickle=False)
    except ValueError as exc:
        raise ValueError(
            'dfnb could not transfer an array of dtype {} across the '
            'bridge: {}.'.format(array.dtype, exc))
    parts.append(('npy', buffer.getvalue()))
    return len(parts) - 1


def _append_series_part(series, parts):
    """One DataFrame column as an ``'npy'`` part; an object-dtyped column
    (pickle is off) travels as a ``'json'`` list part instead. Returns the
    part index."""
    index = len(parts)
    array = series.to_numpy()
    if array.dtype == object:
        parts.append(('json', json.dumps(series.tolist(), ensure_ascii=False,
                                         separators=(',', ':'))
                      .encode('utf-8')))
    else:
        buffer = io.BytesIO()
        np.save(buffer, array, allow_pickle=False)
        parts.append(('npy', buffer.getvalue()))
    return index


def _append_index_part(index, parts):
    """The DataFrame's index, same policy as the columns."""
    return _append_series_part(index, parts)


def decode_value(value, parts):
    """The inverse of :func:`encode_value`: materializes ndarrays and
    DataFrames from their parts; plain JSON values pass through."""
    if isinstance(value, list):
        return [decode_value(item, parts) for item in value]
    if isinstance(value, dict):
        marker = value.get(_DFN_MARKER)
        if marker == 'ndarray':
            buffer = io.BytesIO(parts[value['part']][1])
            return np.load(buffer, allow_pickle=False)
        if marker == 'bytes':
            return parts[value['part']][1]
        if marker == 'dataframe':
            pd = __import__('pandas')
            data = {}
            for column_marker in value['columns']:
                data[column_marker['name']] = _decode_column(
                    column_marker, parts)
            index = _decode_column(value['index'], parts)
            if index is None:
                frame = pd.DataFrame(data)
            else:
                frame = pd.DataFrame(data, index=index)
            return frame[[marker_['name'] for marker_ in value['columns']]]
        return {key: decode_value(item, parts) for key, item in value.items()}
    return value


def _decode_column(marker, parts):
    """One column/index part back to a numpy array; a ``'json'`` part (the
    object-dtyped fallback) comes back as an object array."""
    kind, blob = parts[marker['part']]
    if kind == 'npy':
        return np.load(io.BytesIO(blob), allow_pickle=False)
    return np.asarray(json.loads(blob.decode('utf-8')), dtype=object)


# ---------------------------------------------------------------------------
# frames: (envelope, parts) <-> bytes
# ---------------------------------------------------------------------------

def encode_frame(envelope, parts=None):
    """One full frame: the 8-byte payload-length prefix, the JSON envelope
    and the optional binary parts blob."""
    parts = list(parts or [])
    blob = bytearray()
    for kind, payload in parts:
        blob.extend(_PART_LEN.pack(len(payload)))
        blob.extend(payload)
    envelope = dict(envelope)
    envelope['blob_parts'] = [kind for kind, _payload in parts]
    json_bytes = json.dumps(envelope, ensure_ascii=False,
                            separators=(',', ':')).encode('utf-8')
    payload = _JSON_LEN.pack(len(json_bytes)) + json_bytes + bytes(blob)
    return _HEADER.pack(len(payload)) + payload


def decode_payload(payload):
    """Decodes one frame's payload bytes (everything after the 8-byte
    prefix) into ``(envelope, parts)``."""
    if len(payload) < 4:
        raise ValueError('bridge frame payload is truncated '
                         '({} bytes).'.format(len(payload)))
    json_len, = _JSON_LEN.unpack_from(payload, 0)
    if json_len < 2 or json_len > len(payload) - 4:
        raise ValueError('bridge frame JSON length {} is out of range for a '
                         '{}-byte payload.'.format(json_len, len(payload)))
    try:
        envelope = json.loads(payload[4:4 + json_len].decode('utf-8'))
    except UnicodeDecodeError as exc:
        raise ValueError('bridge frame JSON is not valid UTF-8: '
                         '{}.'.format(exc))
    if not isinstance(envelope, dict) or not isinstance(envelope.get('type'),
                                                        str):
        raise ValueError('bridge frame envelope is not a JSON object with '
                         "a 'type' string.")
    kinds = envelope.pop('blob_parts', [])
    blob = payload[4 + json_len:]
    parts = []
    position = 0
    for kind in kinds:
        if position + 4 > len(blob):
            raise ValueError('bridge frame parts blob is truncated.')
        part_len, = _PART_LEN.unpack_from(blob, position)
        position += 4
        if position + part_len > len(blob):
            raise ValueError('bridge frame parts blob is truncated.')
        parts.append((kind, blob[position:position + part_len]))
        position += part_len
    if position != len(blob):
        raise ValueError('bridge frame parts blob carries {} extra '
                         'bytes.'.format(len(blob) - position))
    return envelope, parts


class FrameReader(object):
    """An incremental frame splitter: :meth:`feed` consumes whatever bytes
    arrived and returns the ``(envelope, parts)`` of every frame that
    completed, whatever the chunk boundaries were."""

    def __init__(self):
        self._buffer = bytearray()
        self._payload_len = None

    def feed(self, data):
        self._buffer.extend(data)
        frames = []
        while True:
            if self._payload_len is None:
                if len(self._buffer) < _HEADER.size:
                    break
                self._payload_len, = _HEADER.unpack_from(self._buffer, 0)
                if self._payload_len > MAX_FRAME_BYTES:
                    raise ValueError(
                        'bridge frame announces a {}-byte payload, beyond '
                        'the {}-byte safety ceiling.'.format(
                            self._payload_len, MAX_FRAME_BYTES))
                del self._buffer[:_HEADER.size]
            remaining = self._payload_len - len(self._buffer)
            if remaining > 0:
                break
            payload = bytes(self._buffer[:self._payload_len])
            del self._buffer[:self._payload_len]
            self._payload_len = None
            frames.append(decode_payload(payload))
        return frames
