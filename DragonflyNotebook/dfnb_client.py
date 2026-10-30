r"""The ``dfnb`` client of the standalone notebook application.

Round 8: the notebook editor and its IPython kernel live in their own
process; ``dfnb`` is no longer the ORS data module but THIS client — same
eleven public signatures, same validation texts, same behavior from a cell's
point of view, but every call crosses a Qt named pipe to the bridge service
running inside Dragonfly (``bridge_service`` + the handlers in
``dragonflynotebook_lib``). This module imports NO ORS code: it is pure
PyQt6 + numpy + pandas + the shared protocol.

The public surface (identical signatures and texts to the data layer):

* ``import_volume_as_np(source, timestep=0)`` / ``import_roi_as_np`` /
  ``import_multiroi_as_np`` — the resolved object's data as a fresh numpy
  array (a real copy: the service ``numpy.save``s it across the pipe);
* ``publish_np_as_volume(array, title=None, reference=None, spacing=None)``,
  ``publish_np_as_roi(...)``, ``publish_np_as_multiroi(..., properties=...)``
  — return a published-object handle: a ``{'guid', 'title', 'class_name'}``
  dict that later ``source`` / ``reference`` parameters re-accept (an object
  does not cross the pipe; its GUID does);
* ``import_measured_properties(source, properties=None, timestep=0)`` — a
  ``pandas.DataFrame``;
* ``export_measured_properties(source, properties, timestep=0,
  wipe=False)`` — ``None`` (``wipe=True`` replaces ALL existing measured
  properties; validation fails fast before anything is wiped);
* ``list_measured_properties(source, timestep=0)`` — column names;
* ``list_objects(class_name='Channel', representable_only=True)`` — the
  ``{'guid', 'title', 'class_name'}`` dicts of the live objects;
* ``delete_object(source)`` — deletes one object from the session by
  title, GUID or handle (housekeeping: remove a stale same-titled object
  before publishing a new one with the same title).

``source`` / ``reference`` accept the object's title or GUID as a string,
or a previously returned handle dict. An ORS object instance cannot cross
the pipe (the kernel process never loads ORS) and is refused with a clear
message.

Validation happens here first (fail fast, no round trip): the dtype/shape
conforming, the timestep, spacing and title helpers carry the SAME texts as
the data layer, so a bad argument raises the identical ``ValueError`` in
the notebook process. The data layer re-validates on its side (its
contracts are pinned by the tier-2 suite).

Threading. One client instance is shared by the whole application: the GUI
thread (the form's object pickers) and the kernel's worker thread (cell
code) both call it. The underlying ``QLocalSocket`` belongs to the thread
that created the client (the GUI thread): calls from that thread run the
transaction directly; calls from any other thread marshal the request to
the owner thread through a queued Qt signal and wait in small slices —
which is exactly where a kernel-worker ``KeyboardInterrupt`` (the Stop
button's async interrupt) lands: it sends a ``cancel`` frame for the
in-flight request and re-raises, and the service's next cancel checkpoint
aborts the handler. While the owner thread waits for a response it pumps
the application's OWN event loop — the notebook process is ORS-free, so
that is safe (the round-6 hazard was pumping DRAGONFLY's loop during
native ORS calls) and it is what delivers the Stop click and the queued
cancel signal mid-call; the form's guards (toolbar and structural actions
disabled while a cell runs) keep a nested second call unreachable.
Progress events of a running publish arrive as frames
and are forwarded to ``progress_callback`` on the owner thread.

Connection loss. A dfnb call raised while disconnected fails with a clear
``RuntimeError``; the form polls :meth:`poll_reconnect` on a timer — it
retries the original pipe name first, then any ``dfnb_bridge_*`` pipe it
can handshake with (with two Dragonfly instances running, the first bridge
to answer wins — the notebook simply talks to that Dragonfly).
"""

import os

os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'True')

from . import notebook_bridge_protocol as protocol  # noqa: E402 (registers the DLL dirs before numpy loads)
import numpy as np  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

from PyQt6.QtCore import QObject, QThread, pyqtSignal  # noqa: E402
from PyQt6.QtNetwork import QLocalSocket  # noqa: E402

__all__ = ['DfnbClient', 'PublishedObject']

_CANCEL_MESSAGE = 'Operation cancelled by user.'

_DISCONNECT_MESSAGE = (
    'The Dragonfly bridge is disconnected — Dragonfly may have been closed '
    'or restarted. The notebook window reconnects automatically once the '
    'bridge is back; run the cell again then.')

_NOT_AN_OBJECT_MESSAGE = (
    'dfnb runs in the notebook application, separate from Dragonfly: pass '
    "the object's title or GUID as a string, or a handle returned by a "
    'dfnb publish — an ORS object itself cannot cross the bridge.')


class PublishedObject(dict):
    """The handle a ``publish_np_as_*`` returns: a ``{'guid', 'title',
    'class_name'}`` dict (it IS a plain dict, so it stays
    bridge-serializable and truthiness-friendly) whose GUID later
    ``source`` / ``reference`` parameters re-accept. Attribute access is a
    convenience for cell code: ``handle.guid``."""

    __slots__ = ()

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


# ---------------------------------------------------------------------------
# validation helpers — same texts as dragonflynotebook_lib (user-facing
# contracts); the data layer re-validates on its side
# ---------------------------------------------------------------------------

def _check_timestep(timestep):
    if isinstance(timestep, bool) or not isinstance(timestep, (int, np.integer)) \
            or int(timestep) < 0:
        raise ValueError(
            f"timestep must be a non-negative integer; got {timestep!r}.")
    return int(timestep)


def _check_spacing(spacing):
    if spacing is None:
        return None
    try:
        values = tuple(float(v) for v in spacing)
    except (TypeError, ValueError):
        raise ValueError(
            f"spacing must be None or a sequence of three positive numbers "
            f"(spacing_x, spacing_y, spacing_z); got {spacing!r}.") from None
    if len(values) != 3 or not all(np.isfinite(values)) or not all(v > 0 for v in values):
        raise ValueError(
            f"spacing must be None or a sequence of three positive numbers "
            f"(spacing_x, spacing_y, spacing_z); got {spacing!r}.")
    return values


def _pick_title(title, default_title):
    if title is None:
        return default_title
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"title must be a non-empty string; got {title!r}.")
    return title.strip()


def _check_wipe(wipe):
    """The wipe flag is a STRICT bool: a truthy non-bool (1, 'true') is
    refused the same fail-fast way as a bad timestep — a wipe replaces all
    existing properties, so it must never fire from an accidental truthy
    value."""
    if not isinstance(wipe, bool):
        raise ValueError(f"wipe must be a boolean; got {wipe!r}.")
    return wipe


def _smallest_uint_type(max_value, what):
    if max_value <= 255:
        return np.uint8
    if max_value <= 65535:
        return np.uint16
    if max_value <= 4294967295:
        return np.uint32
    raise ValueError(
        f"{what} holds values up to {max_value}, which exceed the largest "
        f"ORS channel type (uint32). Clip or scale the data first.")


def _conform_array_for_channel(array, what):
    """Same dtype/shape policy as the data layer; returns the array to
    ship (the service conforms again — idempotent)."""
    a = np.asarray(array)
    if a.ndim not in (2, 3):
        raise ValueError(
            f"{what} must be a 2-D (Y, X) or 3-D (Z, Y, X) numpy array; "
            f"got a {a.ndim}-D array of shape {tuple(a.shape)}.")
    if a.size == 0:
        raise ValueError(f"{what} is empty (shape {tuple(a.shape)}).")
    dtype = a.dtype
    if dtype == np.float32 or dtype in (np.uint8, np.uint16, np.uint32):
        out = a
    elif dtype == np.bool_:
        out = a.astype(np.uint8)
    elif dtype in (np.float16, np.float64):
        out = a.astype(np.float32)  # ORS channels have no 16/64-bit floats
    elif dtype in (np.int8, np.int16, np.int32, np.int64):
        if int(a.min()) >= 0:
            out = a.astype(_smallest_uint_type(int(a.max()), what))
        else:
            out = a.astype(np.float32)
    elif dtype == np.uint64:
        out = a.astype(_smallest_uint_type(int(a.max()), what))
    else:
        raise ValueError(
            f"{what} has unsupported dtype {dtype}. Use one of uint8, "
            f"uint16, uint32, float32 — or anything the dfnb dtype policy "
            f"converts (bool, float16/64, signed ints, uint64).")
    return np.ascontiguousarray(out)


def _label_dtype(label_count):
    if label_count <= 254:
        return np.uint8
    if label_count <= 65534:
        return np.uint16
    return np.uint32


def _conform_labels_for_multiroi(array, what):
    """Same labels policy as the data layer; returns the conformed labels
    array and its label count."""
    a = np.asarray(array)
    if a.ndim not in (2, 3):
        raise ValueError(
            f"{what} must be a 2-D (Y, X) or 3-D (Z, Y, X) numpy array; "
            f"got a {a.ndim}-D array of shape {tuple(a.shape)}.")
    if a.size == 0:
        raise ValueError(f"{what} is empty (shape {tuple(a.shape)}).")
    kind = a.dtype.kind
    if kind == 'b':
        labels = a.astype(np.float64)
    elif kind == 'f':
        if not bool(np.isfinite(a).all()):
            raise ValueError(f"{what} contains NaN or infinity; labels must "
                             f"be finite whole numbers.")
        rounded = np.round(a)
        deviation = np.abs(a - rounded)
        if bool((deviation > 0).any()):
            worst = int(np.argmax(deviation))
            bad = float(np.asarray(a, dtype=np.float64).ravel()[worst])
            raise ValueError(
                f"{what} contains non-integer labels (e.g. {bad!r}); "
                f"round or cast the labels first.")
        labels = rounded
    elif kind in 'iu':
        labels = a.astype(np.float64)
    else:
        raise ValueError(
            f"{what} has unsupported dtype {a.dtype}; labels must be whole "
            f"numbers (integer, float or boolean).")
    if int(labels.min()) < 0:
        raise ValueError(f"{what} contains negative values; labels must be "
                         f"0 (background) or positive integers.")
    label_count = int(labels.max())
    if label_count == 0:
        raise ValueError(
            f"{what} contains no labels (every voxel is 0); nothing would be "
            f"segmented.")
    target = _label_dtype(label_count)
    return np.ascontiguousarray(labels.astype(target)), label_count


# ---------------------------------------------------------------------------
# transports
# ---------------------------------------------------------------------------

class _LocalSocketTransport(object):
    """The production transport: one ``QLocalSocket`` owned by the thread
    that opened it."""

    def __init__(self):
        self._socket = None

    def open(self, name, timeout_s=3.0):
        self.close()
        socket = QLocalSocket()
        socket.connectToServer(name)
        if not socket.waitForConnected(int(timeout_s * 1000)):
            error_text = socket.errorString()
            socket.disconnectFromServer()
            del socket
            return False, error_text
        self._socket = socket
        return True, ''

    def close(self):
        if self._socket is not None:
            try:
                self._socket.disconnectFromServer()
                if self._socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
                    self._socket.waitForDisconnected(1000)
            except Exception:
                pass
            self._socket.deleteLater()
            self._socket = None

    def write(self, data):
        if self._socket is None:
            raise RuntimeError(_DISCONNECT_MESSAGE)
        self._socket.write(data)

    def read_available(self):
        if self._socket is None or self._socket.bytesAvailable() == 0:
            return b''
        return bytes(self._socket.readAll().data())

    def wait_readable(self, timeout_s):
        """True once bytes are readable (or already buffered). A timed-out
        wait also spins the process's own event loop once: the notebook
        application is ORS-free, so pumping ITS loop while a bridge response
        is awaited is safe (the round-6 hazard was pumping DRAGONFLY's loop
        during native ORS calls) and it is what keeps the app's GUI alive
        during a long transfer and delivers the Stop click / the queued
        cancel signal mid-call."""
        if self._socket is None:
            return False
        if self._socket.bytesAvailable() > 0:
            return True
        state = self._socket.state()
        if state != QLocalSocket.LocalSocketState.ConnectedState:
            return False
        got = self._socket.waitForReadyRead(int(timeout_s * 1000))
        if not got:
            from PyQt6.QtCore import QCoreApplication, QEventLoop
            app = QCoreApplication.instance()
            if app is not None:
                app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
        return self._socket.bytesAvailable() > 0

    @property
    def is_connected(self):
        return self._socket is not None and \
            self._socket.state() == QLocalSocket.LocalSocketState.ConnectedState

    @property
    def error_text(self):
        if self._socket is None:
            return ''
        return self._socket.errorString()


def list_pipe_candidates(preferred=None):
    """Every ``dfnb_bridge_*`` pipe name visible on this machine, the
    preferred one first. Used by :meth:`DfnbClient.poll_reconnect`."""
    names = []
    try:
        entries = os.listdir('//./pipe/')
    except OSError:
        entries = []
    for entry in entries:
        if entry.startswith(protocol.BRIDGE_NAME_PREFIX):
            names.append(entry)
    if preferred in names:
        names.remove(preferred)
        names.insert(0, preferred)
    return names


# ---------------------------------------------------------------------------
# the client
# ---------------------------------------------------------------------------

class _MarshalCall(object):
    """One worker-thread dfnb call queued to the owner thread."""

    __slots__ = ('seq', 'method', 'params', 'box', 'event')

    def __init__(self, seq, method, params):
        self.seq = seq
        self.method = method
        self.params = params
        self.box = {}
        self.event = threading.Event()


class DfnbClient(QObject):
    """The notebook-side ``dfnb``: the eleven public functions over the pipe,
    one shared instance for the GUI thread and the kernel worker."""

    #: emitted (on the owner thread) when the handshake completes
    connected_signal = pyqtSignal()
    #: emitted (on the owner thread) when the pipe drops
    disconnected_signal = pyqtSignal()
    #: worker -> owner: run this call's transaction
    _marshal_call = pyqtSignal(object)
    #: worker -> owner: the worker was interrupted mid-call; cancel it
    _marshal_cancel = pyqtSignal(int)

    def __init__(self, pipe_name=None, transport_class=None, parent=None):
        super().__init__(parent)
        self._preferred_name = pipe_name
        self._transport = (transport_class() if transport_class
                           is not None else _LocalSocketTransport())
        self._owner_thread = QThread.currentThread()
        self._lock = threading.Lock()
        self._seq = 0
        self._connected = False
        self._closed = False
        self._reader = protocol.FrameReader()
        self._responses = {}
        self._cancelled = set()
        self._in_flight = None
        self.progress_callback = None
        self._marshal_call.connect(self._on_marshal_call)
        self._marshal_cancel.connect(self._on_marshal_cancel)

    # -- connection lifecycle (owner thread) --------------------------------

    def _check_owner_thread(self, what):
        if QThread.currentThread() is not self._owner_thread:
            raise RuntimeError(
                'dfnb_client.{} must run on the thread that created the '
                'client (the application GUI thread).'.format(what))

    @property
    def pipe_name(self):
        """The name this client prefers (or was constructed with)."""
        return self._preferred_name

    def is_connected(self):
        return self._connected

    def ensure_connected(self):
        """Raises the clear RuntimeError when the bridge is down (no
        auto-reconnect here: the form's timer owns reconnecting)."""
        if self._closed or not self._connected:
            raise RuntimeError(_DISCONNECT_MESSAGE)

    def connect(self, pipe_name=None):
        """Opens the pipe and completes the version handshake. Owner thread
        only. Returns True on success."""
        self._check_owner_thread('connect()')
        if self._closed:
            raise RuntimeError(_DISCONNECT_MESSAGE)
        name = pipe_name or self._preferred_name
        if not name:
            candidates = list_pipe_candidates()
            if not candidates:
                return False
            name = candidates[0]
        ok, _error = self._transport.open(name)
        if not ok:
            return False
        if not self._handshake():
            self._transport.close()
            return False
        self._preferred_name = name
        self._set_connected(True)
        return True

    def _handshake(self):
        self._reader = protocol.FrameReader()
        self._responses = {}
        self._cancelled = set()
        try:
            frame = protocol.encode_frame({'type': protocol.HELLO,
                                           'version': protocol.PROTOCOL_VERSION})
            self._transport.write(frame)
        except Exception:
            return False
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if not self._transport.is_connected:
                return False
            for envelope, _parts in self._drain():
                if envelope.get('type') == protocol.WELCOME:
                    version = envelope.get('version')
                    if version != protocol.PROTOCOL_VERSION:
                        return False
                    return True
            self._transport.wait_readable(0.1)
        return False

    def _set_connected(self, value):
        was = self._connected
        self._connected = value
        if value and not was:
            self.connected_signal.emit()
        elif not value and was:
            self.disconnected_signal.emit()

    def poll_reconnect(self):
        """One reconnect attempt: the preferred pipe first, then every other
        ``dfnb_bridge_*`` pipe this machine exposes. Owner thread only;
        True when a bridge answered the handshake."""
        self._check_owner_thread('poll_reconnect()')
        if self._closed:
            return False
        if self._connected:
            # cheap liveness probe: the state is the socket's truth
            if self._transport.is_connected:
                return True
            self._on_connection_lost()
            return False
        candidates = list_pipe_candidates(self._preferred_name)[:8]
        for name in candidates:
            ok, _error = self._transport.open(name, timeout_s=0.75)
            if not ok:
                continue
            if self._handshake():
                self._preferred_name = name
                self._set_connected(True)
                return True
            self._transport.close()
        return False

    def close(self):
        """Drops the pipe for good (application shutdown)."""
        self._check_owner_thread('close()')
        self._closed = True
        self._on_connection_lost()

    def _on_connection_lost(self):
        self._transport.close()
        self._responses = {}
        self._in_flight = None
        self._set_connected(False)

    # -- the call path -------------------------------------------------------

    def call(self, method, params):
        """One dfnb request -> the decoded payload. On the owner thread the
        transaction runs directly; from any other thread it is marshaled to
        the owner thread and awaited in small slices (a worker-thread
        interrupt lands there and cancels the request)."""
        if QThread.currentThread() is self._owner_thread:
            return self._transaction(method, params)
        with self._lock:
            self._seq += 1
            seq = self._seq
        marshal = _MarshalCall(seq, method, params)
        self._marshal_call.emit(marshal)
        try:
            while not marshal.event.wait(0.05):
                if self._closed:
                    raise RuntimeError(_DISCONNECT_MESSAGE)
        except KeyboardInterrupt:
            # A kernel-worker interrupt landed between the wait slices (the
            # Stop button): the owner thread sends the cancel frame for the
            # in-flight request -- the service aborts the handler at its
            # next cancel checkpoint -- and the interrupt re-raises into
            # the cell.
            self._marshal_cancel.emit(seq)
            raise
        if 'error' in marshal.box:
            raise marshal.box['error']
        return marshal.box.get('result')

    def _on_marshal_call(self, marshal):
        """Owner thread: the queued worker call."""
        try:
            marshal.box['result'] = self._transaction(marshal.method,
                                                      marshal.params)
        except BaseException as exc:  # the worker inspects the box
            marshal.box['error'] = exc
        finally:
            marshal.event.set()

    def _on_marshal_cancel(self, seq):
        """Owner thread: cancel one in-flight request (the worker was
        interrupted while waiting)."""
        self._cancelled.add(seq)

    def _next_seq(self):
        with self._lock:
            self._seq += 1
            return self._seq

    def _transaction(self, method, params):
        """One whole request/response round trip on the owner thread."""
        if self._in_flight is not None:
            # the wait cycle pumps the app's own event loop, so a nested
            # delivery (a second call racing the running transaction) is
            # possible in principle; refusing it keeps the frames
            # uninterleaved. During a cell run the form's guards make this
            # unreachable (the toolbar and every structural action are
            # disabled while a cell runs).
            raise RuntimeError(
                'A dfnb call is already in flight; wait for the running '
                'cell or dialog to finish first.')
        if self._closed or not self._connected:
            raise RuntimeError(_DISCONNECT_MESSAGE)
        seq = self._next_seq()
        parts = []
        frame = protocol.encode_frame(
            {'type': protocol.REQUEST, 'id': seq, 'method': method,
             'params': protocol.encode_value(params, parts)}, parts)
        try:
            self._transport.write(frame)
        except Exception:
            self._on_connection_lost()
            raise RuntimeError(_DISCONNECT_MESSAGE) from None
        self._in_flight = seq
        try:
            while True:
                response = self._responses.pop(seq, None)
                if response is not None:
                    ok, payload = response
                    if not ok:
                        error_class, message = payload
                        raise _bridge_exception(error_class, message)
                    return payload
                if self._cancelled and seq in self._cancelled:
                    self._cancelled.discard(seq)
                    self._send_cancel(seq)
                    raise KeyboardInterrupt(_CANCEL_MESSAGE)
                if not self._transport.is_connected:
                    self._on_connection_lost()
                    raise RuntimeError(_DISCONNECT_MESSAGE)
                for envelope, frame_parts in self._drain():
                    kind = envelope.get('type')
                    if kind == protocol.RESPONSE:
                        rid = envelope.get('id')
                        if envelope.get('ok'):
                            payload = protocol.decode_value(
                                envelope.get('payload') or {}, frame_parts)
                            self._responses[rid] = (True, payload)
                        else:
                            self._responses[rid] = (
                                False, (str(envelope.get('error_class') or
                                        'Exception'),
                                        str(envelope.get('message') or '')))
                    elif kind == protocol.PROGRESS:
                        self._on_progress_frame(envelope)
                    elif kind == protocol.WELCOME:
                        self._set_connected(True)
                if seq in self._responses:
                    continue
                self._transport.wait_readable(0.1)
        finally:
            self._in_flight = None

    def _send_cancel(self, seq):
        try:
            self._transport.write(protocol.encode_frame(
                {'type': protocol.CANCEL, 'id': seq}))
        except Exception:
            pass  # the connection is going away anyway

    def _drain(self):
        """Reads whatever arrived, splits frames, returns the decoded ones."""
        frames = []
        data = self._transport.read_available()
        if data:
            try:
                frames = self._reader.feed(data)
            except ValueError:
                # a corrupted stream cannot recover; drop the connection
                self._on_connection_lost()
                raise RuntimeError(_DISCONNECT_MESSAGE) from None
        return frames

    def _on_progress_frame(self, envelope):
        callback = self.progress_callback
        if callback is None:
            return
        try:
            callback(str(envelope.get('caption') or ''),
                     int(envelope.get('step') or 0),
                     int(envelope.get('of') or 0))
        except Exception:
            pass  # a failing progress UI must never break the call

    # -- the eleven public dfnb functions ------------------------------------

    @staticmethod
    def _source_param(source):
        if isinstance(source, str):
            return source
        if isinstance(source, dict):
            return {'guid': source.get('guid'), 'title': source.get('title'),
                    'class_name': source.get('class_name')}
        raise ValueError(_NOT_AN_OBJECT_MESSAGE)

    def import_volume_as_np(self, source, timestep=0):
        array = self.call('import_volume_as_np', {
            'source': self._source_param(source),
            'timestep': _check_timestep(timestep)})
        return array['array']

    def import_roi_as_np(self, source, timestep=0):
        array = self.call('import_roi_as_np', {
            'source': self._source_param(source),
            'timestep': _check_timestep(timestep)})
        return array['array']

    def import_multiroi_as_np(self, source, timestep=0):
        array = self.call('import_multiroi_as_np', {
            'source': self._source_param(source),
            'timestep': _check_timestep(timestep)})
        return array['array']

    def publish_np_as_volume(self, array, title=None, reference=None,
                             spacing=None):
        _check_title_and_spacing(title, spacing)
        payload = self.call('publish_np_as_volume', {
            'array': _conform_array_for_channel(array, 'The volume array'),
            'title': _pick_title(title, None),
            'reference': self._source_param(reference) if reference is not None else None,
            'spacing': _check_spacing(spacing)})
        return PublishedObject(payload['published'])

    def publish_np_as_roi(self, array, title=None, reference=None,
                          spacing=None):
        _check_title_and_spacing(title, spacing)
        mask = _conform_array_for_channel(array, 'The ROI mask')
        if not bool(mask.any()):
            raise ValueError(
                'The ROI mask contains no foreground voxels (every value is '
                '0); an empty ROI would not be created.')
        payload = self.call('publish_np_as_roi', {
            'array': mask,
            'title': _pick_title(title, None),
            'reference': self._source_param(reference) if reference is not None else None,
            'spacing': _check_spacing(spacing)})
        return PublishedObject(payload['published'])

    def publish_np_as_multiroi(self, array, title=None, reference=None,
                               spacing=None, properties=None):
        _check_title_and_spacing(title, spacing)
        labels, _label_count = _conform_labels_for_multiroi(
            array, 'The labels array')
        payload = self.call('publish_np_as_multiroi', {
            'array': labels,
            'title': _pick_title(title, None),
            'reference': self._source_param(reference) if reference is not None else None,
            'spacing': _check_spacing(spacing),
            'properties': properties})
        return PublishedObject(payload['published'])

    def import_measured_properties(self, source, properties=None, timestep=0):
        table = self.call('import_measured_properties', {
            'source': self._source_param(source),
            'properties': [properties] if isinstance(properties, str) else properties,
            'timestep': _check_timestep(timestep)})
        return table['table']

    def export_measured_properties(self, source, properties, timestep=0,
                                   wipe=False):
        self.call('export_measured_properties', {
            'source': self._source_param(source),
            'properties': properties,
            'timestep': _check_timestep(timestep),
            'wipe': _check_wipe(wipe)})
        return None

    def list_measured_properties(self, source, timestep=0):
        result = self.call('list_measured_properties', {
            'source': self._source_param(source),
            'timestep': _check_timestep(timestep)})
        return result['properties']

    def list_objects(self, class_name='Channel', representable_only=True):
        result = self.call('list_objects', {
            'class_name': class_name,
            'representable_only': representable_only})
        return result['objects']

    def delete_object(self, source):
        self.call('delete_object', {'source': self._source_param(source)})
        return None


def _check_title_and_spacing(title, spacing):
    """The publish kwargs validate client-side even when defaulted: a bad
    title or spacing must fail before anything is shipped."""
    if title is not None:
        _pick_title(title, None)
    if spacing is not None:
        _check_spacing(spacing)


def _bridge_exception(error_class, message):
    """Rebuilds the service-side exception with its ORIGINAL class so
    ValueError / RuntimeError semantics survive the pipe."""
    if error_class == 'ValueError':
        return ValueError(message)
    if error_class == 'RuntimeError':
        return RuntimeError(message)
    if error_class == 'KeyboardInterrupt':
        return KeyboardInterrupt(message)
    if error_class == 'TimeoutError':
        return TimeoutError(message)
    if error_class == 'OSError':
        return OSError(message)
    return RuntimeError('{}: {}'.format(error_class, message) if error_class
                        else message)
