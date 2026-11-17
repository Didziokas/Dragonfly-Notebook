r"""The Dragonfly Notebook bridge protocol -- shared by both sides, ORS-free.

Round 8 moves the notebook editor and its IPython kernel into a standalone
application process; the Dragonfly plugin shrinks to a launcher plus a
bridge service. The two processes talk over a Qt local (named) pipe --
``QLocalServer`` on the Dragonfly side, ``QLocalSocket`` on the app side --
and this module owns everything about the wire format:

* **Framing (protocol version 2)** -- a logical message travels as an
  ordered sequence of FRAMES, each frame an 8-byte little-endian unsigned
  payload length followed by exactly that many payload bytes; the payload
  is a 4-byte little-endian unsigned JSON length, the JSON envelope bytes,
  and then the frame's raw data area. The FIRST frame of a message is the
  HEADER frame: its envelope carries the message's own ``'type'`` and
  fields plus a ``blob_parts`` list naming every binary part in order as
  ``{'kind': ..., 'bytes': ...}`` (``'npy'`` = an npy-encoded ndarray,
  ``'json'`` = JSON-encoded plain value bytes, ``'bytes'`` = raw bytes);
  its data area is empty. Exactly ``ceil(total/CHUNK_BYTES)`` CHUNK frames
  follow (none when the total is 0): each carries the envelope
  ``{'type': 'chunk'}`` and up to ``CHUNK_BYTES`` raw payload bytes, the
  parts' bytes concatenated in declared order (an npy part's bytes are its
  npy header followed by the raw array buffer). Every message rides this
  path in both directions -- a message without binary parts is a lone
  header frame. A receiver must never trust a frame length beyond the
  wire-frame guard (``CHUNK_BYTES`` plus ``FRAME_SLACK_BYTES``: one frame
  never carries more than one chunk plus framing overhead, so a corrupted
  length prefix still fails fast) -- the guard bounds ONE WIRE FRAME, not
  a transfer: a transfer's size is unbounded by design, bounded only by
  the RAM feasibility check below.

* **Values** -- :func:`encode_value` turns a Python value into something
  JSON-encodable, appending LAZY part descriptors as needed; an ndarray
  part carries the array itself (a contiguous copy is made when the array
  is not C-contiguous), a pandas DataFrame one array part per column plus
  one for the index (column names and order travel in the JSON marker;
  object-dtyped columns stay ``'json'`` encoded lists), plain scalars /
  lists / dicts stay JSON. Nothing is serialized up front: the array bytes
  stream out of the array's own buffer when the frames are written, so no
  full npy copy is ever materialized. :func:`decode_value` is the exact
  inverse, so arrays and DataFrames cross the pipe without losing dtype,
  shape, column names or column order.

* **RAM feasibility** -- there is NO arbitrary data-size ceiling. A
  payload is refused only when it genuinely cannot fit in memory:
  :func:`check_ram_feasibility` compares the declared payload bytes (plus
  a fixed safety margin, ``RAM_CHECK_MARGIN_BYTES``) against
  :func:`available_memory_bytes` and raises a clean ``RuntimeError``
  naming the data size, the estimated need, the available RAM and what to
  do (free memory, or query a smaller selection such as one ROI or a
  MultiROI). The check runs SEND-side first (the client before writing a
  request, the service before sending a response -- both refusals leave
  the connection alive) and RECEIVER-side as the race-path backstop: a
  reader whose header declares more than fits enters a DISCARD mode that
  consumes exactly the announced chunk frames (keeping the stream aligned)
  and then raises the same clean error.

* **Envelopes** -- JSON objects carrying a ``'type'``:

  ``{'type': 'hello', 'version': N}``
      the app's first frame on a fresh connection;
  ``{'type': 'welcome', 'version': N}``
      the service's answer; the handshake is complete when the app has it
      and the versions match (a stale peer on an older protocol version
      fails the handshake instead of misparsing the new framing);
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
framework-free tier-1 suite. numpy binds LAZILY (round 16: a ``_LazyNumpy``
proxy whose first touch imports it, so the import always lands after the
DLL-directory registration above; the custom-environment cells now run in
a subprocess on the venv's own interpreter, and this process's one numpy
copy stays the embedded one; pandas was always lazy, imported inside
``decode_value``).

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

class _LazyNumpy:
    """Round 16: numpy binds on the FIRST attribute touch, not at
    import -- the DLL-directory registration above must run before the
    import, and nothing in this module needs numpy until a value
    actually crosses the bridge. (The proxy first existed so an
    activated custom environment could supply the process's numpy; the
    environment cells now run in a SUBPROCESS on the venv's own
    interpreter instead, so this process's one numpy copy stays the
    embedded one -- the lazy binding still orders the import after the
    registration.) Until the first touch, every ``np.<attr>`` below
    rides ``__getattr__``, which imports numpy once and REBINDS the
    module-global ``np`` to the real module: the indirection is paid
    exactly once per binding."""

    def __getattr__(self, name):
        import numpy
        globals()['np'] = numpy
        return getattr(numpy, name)


np = _LazyNumpy()  # noqa: E402  (after the guard / dll dirs, per the rules)


def reset_lazy_numpy():
    """Re-arms the lazy-numpy proxy (the tier-1 suite pins the proxy's
    contract through it: the next touch re-imports numpy instead of
    holding whatever module object was bound before)."""
    globals()['np'] = _LazyNumpy()

PROTOCOL_VERSION = 2

#: The Qt local-server name prefix; the full name is ``<prefix><pid>`` so
#: two Dragonfly instances never collide.
BRIDGE_NAME_PREFIX = 'dfnb_bridge_'

#: The data-area size of one chunk frame (8 MiB): a transfer streams as
#: exactly ``ceil(total/CHUNK_BYTES)`` chunk frames, so no frame and no
#: intermediate buffer ever holds more than this much payload regardless
#: of the transfer's total size. Module-level so a test can shrink it and
#: pin the multi-frame path; patch it BEFORE both sides run (the reader's
#: wire-frame guard is derived from it).
CHUNK_BYTES = 8 * 1024 * 1024

#: How far one wire frame may exceed ``CHUNK_BYTES``: framing overhead
#: only. A frame announcing more is a corrupted stream, refused fast --
#: this bounds one FRAME, never a transfer (a multi-gigabyte payload
#: travels as many bounded chunk frames).
FRAME_SLACK_BYTES = 1 << 20

#: How much pure JSON an inline envelope may carry (a request's params or
#: a response's payload with no binary parts can legitimately be a very
#: large list of strings, and a HEADER frame's envelope is also inline).
#: The writer refuses envelopes beyond this cap with a clean ValueError,
#: and the reader's frame guard adds the same slack, so the two sides
#: AGREE: everything the writer can emit, the reader accepts.
_ENVELOPE_SLACK_BYTES = 64 * 1024 * 1024

#: The RAM feasibility margin: the feasibility check requires the payload
#: bytes PLUS this much headroom to be physically available, so a
#: transfer that would squeeze the host's working set is refused cleanly
#: while the connection stays alive. Module-level so a test can patch it.
RAM_CHECK_MARGIN_BYTES = 256 * 1024 * 1024

#: A hard sanity cap on one npy part's header block (16 MiB): a corrupted
#: stream must fail fast instead of buffering a lying header length.
_MAX_NPY_HEADER_BYTES = 1 << 24

_HEADER = struct.Struct('<Q')
_JSON_LEN = struct.Struct('<I')
_NPY_MAGIC = b'\x93NUMPY'
_NPY_VERSION_HLEN = struct.Struct('<H')   # npy 1.0: uint16 header length
_NPY_VERSION_HLEN4 = struct.Struct('<I')  # npy 2.0: uint32 header length

HELLO = 'hello'
WELCOME = 'welcome'
REQUEST = 'request'
PROGRESS = 'progress'
CANCEL = 'cancel'
RESPONSE = 'response'
CHUNK = 'chunk'

_DFN_MARKER = '__dfnb__'


def bridge_pipe_name(pid=None):
    """The pipe/server name for one Dragonfly process: ``dfnb_bridge_<pid>``."""
    return BRIDGE_NAME_PREFIX + str(os.getpid() if pid is None else int(pid))


# ---------------------------------------------------------------------------
# RAM feasibility (the round-25 replacement for the old frame-size ceiling)
# ---------------------------------------------------------------------------

def available_memory_bytes():
    """The physically available RAM in bytes, best effort: Windows reports
    ``ullAvailPhys`` through ``GlobalMemoryStatusEx``, POSIX multiplies
    ``SC_AVPHYS_PAGES`` by the page size. ``None`` when the platform query
    is unavailable, which makes the feasibility check a no-op (never a
    refusal on an unknown answer)."""
    try:
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ('dwLength', wintypes.DWORD),
                    ('dwMemoryLoad', wintypes.DWORD),
                    ('ullTotalPhys', ctypes.c_ulonglong),
                    ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong),
                    ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong),
                    ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
                ]

            stat = _MemoryStatusEx()
            stat.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullAvailPhys)
            return None
        return int(os.sysconf('SC_PAGE_SIZE')) * \
            int(os.sysconf('SC_AVPHYS_PAGES'))
    except Exception:
        return None


def check_ram_feasibility(total_bytes, what='the transfer'):
    """Raises the clean RuntimeError when ``total_bytes`` cannot fit in
    memory; returns otherwise. The estimate is the payload plus
    ``RAM_CHECK_MARGIN_BYTES`` of working headroom, compared against
    :func:`available_memory_bytes` (an unknown availability never
    refuses). The raised message names the data size, the estimated need
    and the available RAM, and says what to do about it; the REFUSAL
    NEVER TOUCHES A CONNECTION -- the client raises it before writing
    anything, the service converts it into a normal ``ok:False`` error
    envelope, and a receiver that already accepted the header discards
    the announced chunks before raising it."""
    available = available_memory_bytes()
    if available is None:
        return
    total = int(total_bytes)
    need = total + RAM_CHECK_MARGIN_BYTES
    if need <= available:
        return
    raise RuntimeError(
        'dfnb refuses {} because it does not fit in memory: the payload is '
        '{} bytes, the transfer needs about {} bytes of free RAM (payload '
        'plus a {} byte working margin), but only {} bytes are available. '
        'Free memory, or query a smaller selection (one ROI or a MultiROI '
        'instead of the whole volume).'.format(
            what, total, need, RAM_CHECK_MARGIN_BYTES, available))


def parts_wire_bytes(parts):
    """The total wire size of the declared parts: for an ``'npy'`` part
    the npy header length plus the array's byte count (the array is
    carried whole; a non-C-contiguous input was already copied once by
    :func:`encode_value`), for ``'bytes'`` / ``'json'`` parts the payload
    length."""
    total = 0
    for kind, value in parts:
        if kind == 'npy':
            total += len(_npy_header_bytes(value)) + int(value.nbytes)
        else:
            total += len(value)
    return total


def check_parts_feasibility(parts, what='the transfer'):
    """The send-side gate over whole parts: the same clean RuntimeError as
    :func:`check_ram_feasibility`, raised BEFORE anything is written."""
    check_ram_feasibility(parts_wire_bytes(parts), what)


# ---------------------------------------------------------------------------
# values: Python <-> (plain JSON value + lazy binary parts)
# ---------------------------------------------------------------------------

def encode_value(value, parts):
    """Returns a JSON-encodable stand-in for ``value``, appending one LAZY
    part descriptor per binary payload to ``parts`` (a list of
    ``(kind, value)`` pairs; an ``'npy'`` descriptor carries the ARRAY
    itself -- made C-contiguous here when it is not already, the one
    counted copy -- and its bytes stream out only when the frames are
    written, so no whole-npy copy is ever materialized).

    * ``numpy.ndarray`` -> one ``'npy'`` part (npy-encoded, pickle-free);
      the marker carries the part index. An object-dtype array is refused
      (pickle stays off).
    * ``pandas.DataFrame`` -> one ``'npy'`` part per column + one for the
      index; the marker carries the column names, their part indices and
      the index's part index, so names and order survive exactly.
    * ``bytes`` -> one ``'bytes'`` part (its bytes, as today).
    * ``None`` / bool / int / float / str and lists / tuples / dicts of
      those stay JSON (tuples arrive as lists).
    """
    if isinstance(value, np.ndarray):
        if value.dtype.hasobject:
            raise ValueError(
                'dfnb could not transfer an array of dtype {} across the '
                'bridge: object arrays need pickle and the bridge '
                'transfers pickle-free.'.format(value.dtype))
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
    """One ndarray as an ``'npy'`` part descriptor; the part carries the
    array itself (one contiguous copy when the input is not
    C-contiguous -- Fortran order, a strided slice, a transpose -- made
    HERE, BEFORE the buffer view is attempted, and accounted by the RAM
    gate, which measures the parts' own wire bytes), not serialized
    bytes. Returns the part index.

    A dtype whose buffer cannot be viewed as raw bytes is refused HERE,
    cleanly, before anything is written: the streaming writer hands
    ``memoryview(array).cast('B')`` slices straight to the socket, and a
    datetime64/timedelta64 array has no bytes-level buffer (the old
    ``numpy.save`` path carried them, so this is a round-25 refusal, not
    a round-25 regression class the reader ever saw). A ZERO-SIZE array
    (a (0,0) or (0,5) shape) has no raw-byte buffer either but is
    perfectly transferable: its part is the npy header ALONE, with no
    data bytes -- the receiver allocates the empty array from the header
    and consumes zero chunk bytes. The wire layout always normalizes to
    C order (the npy header says ``fortran_order: False``): an F-order
    or strided input arrives as its C-order equivalent, values and
    shape exact."""
    if not array.flags['C_CONTIGUOUS']:
        array = np.ascontiguousarray(array)
    if array.size == 0:
        parts.append(('npy', array))
        return len(parts) - 1
    try:
        memoryview(array).cast('B')
    except (TypeError, ValueError, BufferError):
        if array.dtype.kind in ('M', 'm'):
            remedy = ('datetime and timedelta arrays have no raw-bytes '
                      "buffer; convert first (astype('int64') for the "
                      'raw values, or .astype(str) / a formatted string '
                      'column for display values)')
        else:
            remedy = ('its buffer cannot be viewed as raw bytes; convert '
                      'to a plain numeric or string dtype first')
        raise ValueError(
            'dfnb could not transfer an array of dtype {} across the '
            'bridge: {}.'.format(array.dtype, remedy)) from None
    parts.append(('npy', array))
    return len(parts) - 1


def _append_series_part(series, parts):
    """One DataFrame column as an ``'npy'`` part descriptor; an
    object-dtyped column (pickle is off) travels as a ``'json'`` list
    part instead. Returns the part index."""
    index = len(parts)
    array = series.to_numpy()
    if array.dtype == object:
        parts.append(('json', json.dumps(series.tolist(), ensure_ascii=False,
                                         separators=(',', ':'))
                      .encode('utf-8')))
    else:
        _append_array_part(array, parts)
    return index


def _append_index_part(index, parts):
    """The DataFrame's index, same policy as the columns."""
    return _append_series_part(index, parts)


def _lookup_part(parts, index, what):
    """One marker's part out of the assembled list: a marker that
    references a MISSING part (a zero-length part the receiver skipped,
    a corrupt marker index) raises the clean ValueError -- never the raw
    IndexError that used to escape the client's call()."""
    try:
        kind, value = parts[index]
    except (IndexError, TypeError):
        raise ValueError(
            'bridge {} marker references a missing binary part ({} '
            'parts arrived).'.format(what, len(parts))) from None
    return kind, value


def decode_value(value, parts):
    """The inverse of :func:`encode_value`: materializes ndarrays and
    DataFrames from their parts. The reader has ALREADY decoded ``'npy'``
    parts into real arrays by the time a frame is assembled, so a part's
    value passes through; ``'json'`` parts are still bytes."""
    if isinstance(value, list):
        return [decode_value(item, parts) for item in value]
    if isinstance(value, dict):
        marker = value.get(_DFN_MARKER)
        if marker == 'ndarray':
            kind, array = _lookup_part(parts, value['part'], 'ndarray')
            if kind != 'npy':
                raise ValueError('bridge ndarray marker names a {!r} '
                                 'part.'.format(kind))
            return array
        if marker == 'bytes':
            kind, payload = _lookup_part(parts, value['part'], 'bytes')
            if kind != 'bytes':
                raise ValueError('bridge bytes marker names a {!r} '
                                 'part.'.format(kind))
            return payload
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
    kind, value = _lookup_part(parts, marker['part'], 'dataframe column')
    if kind == 'npy':
        return value
    return np.asarray(json.loads(value.decode('utf-8')), dtype=object)


# ---------------------------------------------------------------------------
# the npy streaming codec (both directions copy only bounded chunks)
# ---------------------------------------------------------------------------

def _npy_header_bytes(array):
    """The npy header block (magic, version, JSON dict, padding) for a
    C-contiguous array, exactly the bytes ``numpy.save`` would write:
    version 1.0, upgraded to 2.0 only when the dict does not fit the
    16-bit header length. The data that follows is the array's raw
    C-order buffer."""
    description = {'descr': np.lib.format.dtype_to_descr(array.dtype),
                   'fortran_order': False,
                   'shape': tuple(int(size) for size in array.shape)}
    buffer = io.BytesIO()
    try:
        np.lib.format.write_array_header_1_0(buffer, description)
    except ValueError:
        buffer = io.BytesIO()
        np.lib.format.write_array_header_2_0(buffer, description)
    return buffer.getvalue()


def _part_wire_size(kind, value):
    """One part's byte count on the wire."""
    if kind == 'npy':
        return len(_npy_header_bytes(value)) + int(value.nbytes)
    return len(value)


# ---------------------------------------------------------------------------
# frames: (envelope, parts) -> an ordered sequence of wire frames
# ---------------------------------------------------------------------------

def _frame_segments(envelope, data):
    """One wire frame as a LIST of bytes-like segments (their
    concatenation is the frame): the 8-byte payload-length prefix, the
    JSON envelope and the frame's raw data area. Segment lists let the
    writer stream an array's own buffer to the socket WITHOUT
    materializing the frame bytes as a second whole copy.

    An inline envelope beyond ``_ENVELOPE_SLACK_BYTES`` is refused here
    with a clean ValueError: the reader's frame guard only accepts this
    much inline JSON, so the writer must not emit more (the reader would
    refuse it as corruption and kill the connection). Large payloads
    belong in parts (arrays, DataFrames, bytes), never in JSON text."""
    json_bytes = json.dumps(envelope, ensure_ascii=False,
                            separators=(',', ':')).encode('utf-8')
    if len(json_bytes) > _ENVELOPE_SLACK_BYTES:
        raise ValueError(
            'dfnb cannot transfer a {}-byte JSON envelope across the '
            'bridge: the inline envelope cap is {} bytes. Pass large '
            'payloads as parts (numpy arrays, DataFrames or bytes), not '
            'as JSON text.'.format(len(json_bytes),
                                   _ENVELOPE_SLACK_BYTES))
    head = _HEADER.pack(4 + len(json_bytes) + len(data)) + \
        _JSON_LEN.pack(len(json_bytes)) + json_bytes
    if len(data):
        return [head, data]
    return [head]


def _iter_part_segments(parts, chunk_bytes):
    """The parts' bytes as one concatenated stream, yielded as bounded
    bytes-like segments grouped per CHUNK frame: an npy part emits its
    (small) header once and then MEMORYVIEW slices of its own buffer --
    the array is never serialized, not even per chunk -- while
    bytes/json parts emit their payload directly. Each part contributes
    its own chunks (no cross-part accumulation), so no payload byte is
    ever copied here."""
    for kind, value in parts:
        if kind == 'npy':
            array = value
            header = _npy_header_bytes(array)
            stream = int(array.nbytes)
            view = memoryview(array).cast('B') if stream else None
            if len(header) <= chunk_bytes:
                first_data = min(chunk_bytes - len(header), stream)
                if first_data:
                    yield [header, view[:first_data]]
                else:
                    yield [header]
                sent_data = first_data
            else:
                # degenerate: the npy header alone exceeds the chunk
                # size (only reachable with a patched tiny chunk)
                offset = 0
                while offset < len(header):
                    piece = header[offset:offset + chunk_bytes]
                    yield [piece]
                    offset += len(piece)
                sent_data = 0
            for offset in range(sent_data, stream, chunk_bytes):
                yield [view[offset:offset + chunk_bytes]]
        else:
            if not isinstance(value, bytes):
                value = bytes(value)
            for offset in range(0, len(value), chunk_bytes):
                yield [value[offset:offset + chunk_bytes]]


_CHUNK_ENVELOPE_JSON = json.dumps(
    {'type': CHUNK}, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _chunk_head_bytes(data_len):
    """The head segments' concatenation for one CHUNK frame carrying
    ``data_len`` data bytes."""
    return _HEADER.pack(4 + len(_CHUNK_ENVELOPE_JSON) + data_len) + \
        _JSON_LEN.pack(len(_CHUNK_ENVELOPE_JSON)) + _CHUNK_ENVELOPE_JSON


def iter_frame_segments(envelope, parts=None, chunk_bytes=None):
    """Yields the ordered wire frames of one logical message as SEGMENT
    LISTS (each list's concatenation is one frame): the HEADER frame
    (the envelope plus the declared ``blob_parts``, data area empty),
    then exactly ``ceil(total/chunk_bytes)`` CHUNK frames
    (``{'type': 'chunk'}``) carrying the parts' bytes in declared order.
    A message with no binary parts is a lone header frame. The segments
    reference the parts' own buffers, so nothing here copies payload
    bytes."""
    if chunk_bytes is None:
        chunk_bytes = CHUNK_BYTES
    if not isinstance(chunk_bytes, int) or chunk_bytes <= 0:
        raise ValueError(
            'the bridge chunk size must be a positive integer; got '
            '{!r}.'.format(chunk_bytes))
    parts = list(parts or [])
    declared = [{'kind': kind, 'bytes': _part_wire_size(kind, value)}
                for kind, value in parts]
    header = dict(envelope)
    header['blob_parts'] = declared
    yield _frame_segments(header, b'')
    total = sum(entry['bytes'] for entry in declared)
    if total == 0:
        return
    sent = 0
    for segments in _iter_part_segments(parts, chunk_bytes):
        data_len = sum(len(segment) for segment in segments)
        yield [_chunk_head_bytes(data_len)] + segments
        sent += data_len
    if sent != total:
        raise RuntimeError(
            'the bridge writer streamed {} bytes but declared {} for the '
            'parts.'.format(sent, total))


def iter_frames(envelope, parts=None, chunk_bytes=None):
    """The segment sequence of :func:`iter_frame_segments` materialized
    into whole wire frames (for tests and small fixed messages; the
    production transports stream the segments directly)."""
    for segments in iter_frame_segments(envelope, parts, chunk_bytes):
        yield b''.join(segments)


def write_frames(envelope, parts, write, chunk_bytes=None):
    """Writes the whole frame sequence of one logical message through
    ``write`` (one call per SEGMENT, in wire order -- the pacing against
    the socket's writable state happens inside the transport's
    ``write``)."""
    for segments in iter_frame_segments(envelope, parts, chunk_bytes):
        for segment in segments:
            write(segment)


def encode_frame(envelope, parts=None, chunk_bytes=None):
    """All of one logical message's frames concatenated: the header frame
    followed by exactly ``ceil(total/chunk_bytes)`` chunk frames (none
    when there are no binary parts)."""
    return b''.join(iter_frames(envelope, parts, chunk_bytes))


def _decode_frame_payload(payload):
    """Decodes one frame's payload bytes (everything after the 8-byte
    prefix) into ``(envelope, data)``: the JSON envelope and the frame's
    raw data-area bytes (empty for a header frame, one chunk of payload
    bytes for a chunk frame)."""
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
    except json.JSONDecodeError as exc:
        raise ValueError('bridge frame JSON is invalid '
                         '({}).'.format(exc)) from exc
    if not isinstance(envelope, dict) or not isinstance(envelope.get('type'),
                                                        str):
        raise ValueError('bridge frame envelope is not a JSON object with '
                         "a 'type' string.")
    return envelope, payload[4 + json_len:]


def _frame_guard_bytes():
    """The wire-frame guard: one frame never carries more than one chunk
    plus framing overhead, or an inline envelope beyond
    ``_ENVELOPE_SLACK_BYTES`` (which the writer refuses anyway -- the
    two sides AGREE on what a legal frame is), so a corrupted length
    prefix beyond this fails fast instead of waiting for bytes that will
    never come. Read at feed time so a patched ``CHUNK_BYTES`` is
    honored."""
    return CHUNK_BYTES + FRAME_SLACK_BYTES + _ENVELOPE_SLACK_BYTES


class _PartAssembler(object):
    """The receiving half of one chunked transfer: consumes the chunk
    frames' bytes across the declared parts in order. An ``'npy'`` part
    buffers only until its npy header completes, then allocates the
    destination array ONCE and fills its own buffer from the incoming
    bytes (no second full copy); ``'bytes'`` / ``'json'`` parts
    accumulate their payload (which IS the value)."""

    def __init__(self, envelope, declared):
        self.envelope = envelope
        self.declared = [(str(entry['kind']), int(entry['bytes']))
                         for entry in declared]
        self.values = [None] * len(self.declared)
        self.total = sum(size for _kind, size in self.declared)
        self.received_total = 0
        self.index = None
        self.kind = None
        self.part_declared = 0
        self.part_received = 0
        self.part_remaining = 0
        # npy state
        self.header_buf = None
        self.header_total = 0
        self.array = None
        self.flat = None
        self.filled = 0
        self.shape = None
        self.fortran = False
        # bytes/json state
        self.blob = None

    def feed(self, data):
        """Consumes chunk bytes; True when every declared part is
        complete (and every announced byte accounted for)."""
        offset = 0
        length = len(data)
        while offset < length:
            if self.index is None:
                self._start_part(0)
            if self.part_remaining == 0:
                self._finish_part()
                next_index = self.index + 1
                if next_index >= len(self.declared):
                    raise ValueError(
                        'bridge chunk stream carries {} bytes beyond the '
                        'header-declared transfer.'.format(length - offset))
                self._start_part(next_index)
                continue
            take = min(length - offset, self.part_remaining)
            self._consume_part(data[offset:offset + take])
            offset += take
        if self.received_total > self.total:
            raise ValueError(
                'bridge chunk stream carries more bytes than the header '
                'declared ({} received, {} declared).'.format(
                    self.received_total, self.total))
        # the last announced byte completes the transfer; finish() then
        # finalizes the trailing part (a zero-size trailing part never
        # saw a finishing byte, so its value is built here)
        return self.received_total == self.total

    def finish(self):
        """Finalizes the last part and returns the assembled parts."""
        if self.received_total != self.total:
            raise ValueError(
                'bridge chunk stream ended {} bytes short of the '
                'header-declared transfer.'.format(
                    self.total - self.received_total))
        if self.index is None:
            return []
        if self.part_remaining != 0:
            raise ValueError(
                'bridge chunk stream ended {} bytes into the part it was '
                'still assembling.'.format(self.part_remaining))
        if self.values[self.index] is None:
            self._finish_part()
        # trailing zero-size parts never saw a finishing byte: start and
        # finish them here so every declared part materializes
        while self.index + 1 < len(self.declared):
            self._start_part(self.index + 1)
            self._finish_part()
        next_index = self.index + 1
        if next_index < len(self.declared):
            raise ValueError(
                'bridge chunk stream ended before every declared part '
                'arrived.')
        return self.values

    def _start_part(self, index):
        self.index = index
        self.kind, self.part_declared = self.declared[index]
        self.part_received = 0
        self.part_remaining = self.part_declared
        if self.kind == 'npy':
            self.header_buf = bytearray()
            self.header_total = 0
            self.array = None
            self.flat = None
            self.filled = 0
            self.shape = None
            self.fortran = False
        else:
            self.blob = bytearray()

    def _consume_part(self, chunk):
        if self.kind == 'npy':
            self._consume_npy(chunk)
        else:
            self.blob.extend(chunk)
        self.part_received += len(chunk)
        self.part_remaining -= len(chunk)
        self.received_total += len(chunk)

    def _finish_part(self):
        if self.part_received != self.part_declared:
            raise ValueError(
                'bridge part {} carried {} bytes, its header declared '
                '{}.'.format(self.index, self.part_received,
                             self.part_declared))
        if self.kind == 'npy':
            if self.array is None:
                raise ValueError(
                    'bridge npy part {} ended before its npy header '
                    'completed.'.format(self.index))
            if self.filled != int(self.array.nbytes):
                raise ValueError(
                    'bridge npy part {} carried {} of its {} data '
                    'bytes.'.format(self.index, self.filled,
                                    int(self.array.nbytes)))
            if self.fortran:
                self.values[self.index] = self.array.reshape(
                    self.shape, order='F')
            else:
                self.values[self.index] = self.array.reshape(self.shape)
        else:
            self.values[self.index] = bytes(self.blob)
        # release the per-part state (the array keeps only its own buffer)
        self.header_buf = None
        self.flat = None
        self.blob = None

    def _consume_npy(self, chunk):
        if self.array is None:
            self.header_buf.extend(chunk)
            if not self._parse_npy_header():
                return
            data = bytes(self.header_buf[self.header_total:])
            self.header_buf = None
            if data:
                self._fill_npy_data(data)
            return
        self._fill_npy_data(chunk)

    def _parse_npy_header(self):
        """Buffers until the npy header block is complete, then parses it
        with numpy's own readers and allocates the destination. False
        while more header bytes are needed; raises ValueError on a
        corrupt or unsupported header."""
        buf = self.header_buf
        if self.header_total == 0:
            if len(buf) >= 6 and bytes(buf[:6]) != _NPY_MAGIC:
                raise ValueError(
                    'bridge npy part does not start with the npy magic.')
            if len(buf) >= 8:
                major, minor = buf[6], buf[7]
                if (major, minor) not in ((1, 0), (2, 0)):
                    raise ValueError(
                        'bridge npy part announces the unsupported npy '
                        'format version {}.{}.'.format(major, minor))
                if (major, minor) == (1, 0):
                    if len(buf) < 10:
                        return False
                    header_len, = _NPY_VERSION_HLEN.unpack_from(buf, 8)
                    self.header_total = 10 + header_len
                else:
                    if len(buf) < 12:
                        return False
                    header_len, = _NPY_VERSION_HLEN4.unpack_from(buf, 8)
                    self.header_total = 12 + header_len
                if self.header_total > _MAX_NPY_HEADER_BYTES:
                    raise ValueError(
                        'bridge npy part announces a {}-byte header, '
                        'beyond the {}-byte sanity cap.'.format(
                            self.header_total, _MAX_NPY_HEADER_BYTES))
            else:
                return False
        if len(buf) < self.header_total:
            return False
        source = io.BytesIO(bytes(buf[:self.header_total]))
        version = np.lib.format.read_magic(source)
        if version == (1, 0):
            shape, fortran_order, dtype = \
                np.lib.format.read_array_header_1_0(source)
        else:
            shape, fortran_order, dtype = \
                np.lib.format.read_array_header_2_0(source)
        if dtype.hasobject:
            raise ValueError(
                'bridge npy part carries an object dtype; the bridge '
                'transfers pickle-free.')
        count = 1
        for dim in shape:
            count *= int(dim)
        data_bytes = count * int(dtype.itemsize)
        declared_data = self.part_declared - self.header_total
        if data_bytes != declared_data:
            raise ValueError(
                'bridge npy part header declares {} data bytes but its '
                'transfer header declared {}.'.format(
                    data_bytes, declared_data))
        self.shape = tuple(int(dim) for dim in shape)
        self.fortran = bool(fortran_order)
        # one allocation, filled in place from the incoming chunks
        self.array = np.empty(count, dtype=dtype)
        self.flat = self.array.view(np.uint8) if data_bytes else None
        return True

    def _fill_npy_data(self, data):
        expected = int(self.array.nbytes)
        if self.filled + len(data) > expected:
            raise ValueError(
                'bridge npy part carries more data bytes than its header '
                'declared.')
        self.flat[self.filled:self.filled + len(data)] = \
            np.frombuffer(data, dtype=np.uint8)
        self.filled += len(data)


class FrameReader(object):
    """An incremental frame splitter for protocol version 2: :meth:`feed`
    consumes whatever bytes arrived and returns the ``(envelope, parts)``
    of every logical message that completed, whatever the chunk
    boundaries were. A message's header frame starts an assembly (or, for
    a message without binary parts, completes it immediately); its chunk
    frames feed the :class:`_PartAssembler`; the assembled frame's
    ``'npy'`` parts are ALREADY-DECODED arrays and its ``'bytes'`` /
    ``'json'`` parts are bytes. On a header that declares a transfer the
    RAM feasibility check refuses to hold, the reader enters a DISCARD
    mode that consumes exactly the announced chunk bytes (keeping the
    stream aligned) and then raises the clean RuntimeError -- the
    connection stays usable."""

    def __init__(self):
        self._buffer = bytearray()
        self._payload_len = None
        self._assembly = None
        self._discard_remaining = None
        self._pending_refusal = None

    def feed(self, data):
        self._buffer.extend(data)
        frames = []
        while True:
            if self._payload_len is None:
                if len(self._buffer) < _HEADER.size:
                    break
                self._payload_len, = _HEADER.unpack_from(self._buffer, 0)
                if self._payload_len < 4:
                    # a payload shorter than its own 4-byte JSON length
                    # field is a corrupt frame, not a struct.error from
                    # unpacking the next field off an empty buffer
                    raise ValueError(
                        'bridge frame payload is truncated ({} '
                        'bytes).'.format(self._payload_len))
                guard = _frame_guard_bytes()
                if self._payload_len > guard:
                    raise ValueError(
                        'bridge frame announces a {}-byte payload, beyond '
                        'the {}-byte wire-frame guard (one frame carries '
                        'at most one {}-byte chunk plus framing '
                        'overhead).'.format(
                            self._payload_len, guard, CHUNK_BYTES))
                del self._buffer[:_HEADER.size]
            remaining = self._payload_len - len(self._buffer)
            if remaining > 0:
                break
            # the frame is consumed IN PLACE: the JSON envelope copies
            # only its own small bytes and the data area is handed on as
            # a memoryview into the buffer, so a chunk's payload reaches
            # the destination array without an intermediate whole copy;
            # the view is released before the buffer shrinks. The frame
            # is trimmed from the buffer BEFORE any per-frame error
            # propagates: a receiver-side RAM refusal must leave the
            # stream aligned (the connection stays usable), and a
            # corrupt frame's bytes must not be re-parsed either.
            try:
                frame_result = self._consume_frame_inplace()
            except ValueError:
                # a corruption error (bad magic, a lying declared size, a
                # malformed envelope ...): the stream cannot be trusted,
                # so the assembly is dropped, the frame's bytes are never
                # re-parsed, and THE ORIGINAL clean ValueError propagates.
                # The trim is the robust one (see _trim_frame): the
                # corruption ValueError was raised while memoryview
                # slices of this buffer were still alive in the raising
                # traceback's frames, so a plain resizing del here would
                # itself raise BufferError and REPLACE the clean error
                # (the round-25 defect that killed real service
                # processes from inside the Qt slot).
                self._abort_assembly()
                self._trim_frame()
                self._payload_len = None
                raise
            except RuntimeError:
                # the receiver-side RAM refusal: the reader drained the
                # announced chunks and the stream is ALIGNED again --
                # trim, then propagate unchanged (the connection stays
                # usable and the caller answers a clean error envelope)
                self._trim_frame()
                self._payload_len = None
                raise
            except Exception as exc:
                # ONE clean ValueError for every other internal failure:
                # a struct.error that slipped past a guard, the trim's
                # own BufferError, anything unexpected -- a bare exotic
                # exception escaping feed() reaches the Qt slot and
                # kills the process (the round-25 attacker proof)
                self._abort_assembly()
                self._trim_frame()
                self._payload_len = None
                raise ValueError(
                    'bridge frame is corrupted or truncated '
                    '({}: {}).'.format(type(exc).__name__, exc)) from exc
            except BaseException:
                # an interrupt or a shutdown must never be converted into
                # a corruption report -- but the consumed frame never
                # leaks either
                self._trim_frame()
                self._payload_len = None
                raise
            self._trim_frame()
            self._payload_len = None
            frames.extend(frame_result)
        return frames

    def _abort_assembly(self):
        """Drops every open transfer state after a corruption error: the
        stream can no longer be trusted, and no partial assembly may
        leak into a later frame. Callers tear the connection down on a
        corruption ValueError anyway -- this is the defensive guarantee
        that the reader object itself is left clean."""
        self._assembly = None
        self._discard_remaining = None
        self._pending_refusal = None

    def _trim_frame(self):
        """Drops the consumed frame's bytes from the front of the buffer,
        robust against live memoryview exports: a corruption error
        unwinding through the assembler's frames leaves memoryview
        slices of this buffer alive in the traceback it is carrying, and
        a resizing ``del`` on an exported bytearray raises BufferError.
        On that error the buffer object is DROPPED and reallocated (the
        exports die with the old object) instead of the cleanup itself
        raising and replacing the clean corruption error."""
        try:
            del self._buffer[:self._payload_len]
        except BufferError:
            self._buffer = bytearray(self._buffer[self._payload_len:])

    def _consume_frame_inplace(self):
        json_len, = _JSON_LEN.unpack_from(self._buffer, 0)
        if json_len < 2 or json_len > self._payload_len - 4:
            raise ValueError(
                'bridge frame JSON length {} is out of range for a '
                '{}-byte payload.'.format(json_len, self._payload_len))
        try:
            envelope = json.loads(
                bytes(self._buffer[4:4 + json_len]).decode('utf-8'))
        except UnicodeDecodeError as exc:
            raise ValueError('bridge frame JSON is not valid UTF-8: '
                             '{}.'.format(exc))
        except json.JSONDecodeError as exc:
            # a raw json.JSONDecodeError IS a ValueError, but its message
            # is json-module jargon; the clean bridge wording carries the
            # cause with it
            raise ValueError('bridge frame JSON is invalid '
                             '({}).'.format(exc)) from exc
        if not isinstance(envelope, dict) or not isinstance(
                envelope.get('type'), str):
            raise ValueError('bridge frame envelope is not a JSON object '
                             "with a 'type' string.")
        data_view = memoryview(self._buffer)[4 + json_len:self._payload_len]
        try:
            if 'blob_parts' in envelope:
                return self._on_header(envelope)
            if envelope.get('type') == CHUNK:
                return self._on_chunk(data_view)
            raise ValueError(
                "bridge frame is neither a transfer header nor a chunk "
                "(type {!r}).".format(envelope.get('type')))
        finally:
            data_view.release()

    def _on_header(self, envelope):
        if self._assembly is not None or self._discard_remaining is not None:
            raise ValueError(
                'a bridge transfer header arrived while another transfer '
                'is still open.')
        declared = envelope.pop('blob_parts', [])
        total = sum(int(entry['bytes']) for entry in declared)
        if total == 0:
            # a message whose every part is zero-length: the parts are
            # still DECLARED (the markers name their indices), so they
            # materialize as EMPTY payloads -- skipping them would leave
            # decode_value holding markers that point past the parts
            # list (the round-25 zero-length-bytes defect: 'The request
            # parameters could not be decoded.' on the service, a raw
            # IndexError out of the client). A zero-byte 'npy'
            # declaration is corruption (an npy part is at least its
            # magic): refused cleanly.
            parts = []
            for entry in declared:
                kind = str(entry['kind'])
                if kind not in ('bytes', 'json'):
                    raise ValueError(
                        'bridge transfer header declares a zero-byte '
                        '{!r} part.'.format(kind))
                parts.append((kind, b''))
            return [(envelope, parts)]
        try:
            check_ram_feasibility(total, 'the incoming bridge transfer')
        except RuntimeError as refusal:
            refusal.header_envelope = envelope
            self._discard_remaining = total
            self._pending_refusal = refusal
            return []
        self._assembly = _PartAssembler(envelope, declared)
        return []

    def _on_chunk(self, data):
        if self._discard_remaining is not None:
            size = len(data)
            if size > self._discard_remaining:
                raise ValueError(
                    'bridge chunk stream overruns the announced transfer '
                    'size by {} bytes.'.format(
                        size - self._discard_remaining))
            self._discard_remaining -= size
            if self._discard_remaining == 0:
                refusal = self._pending_refusal
                self._pending_refusal = None
                self._discard_remaining = None
                raise refusal
            return []
        if self._assembly is None:
            raise ValueError(
                'a bridge chunk frame arrived with no open transfer.')
        if not self._assembly.feed(data):
            return []
        envelope = self._assembly.envelope
        values = self._assembly.finish()
        parts = [(kind, value)
                 for (kind, _size), value in zip(self._assembly.declared,
                                                 values)]
        self._assembly = None
        return [(envelope, parts)]
