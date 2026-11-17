r"""The Dragonfly-side bridge service (round 8).

``NotebookBridgeService`` owns one ``QLocalServer`` named ``dfnb_bridge_<pid>``
(protocol :func:`~notebook_bridge_protocol.bridge_pipe_name`, so two
Dragonfly instances never collide). Each standalone notebook application
process connects as its own client; the service

* completes the version handshake (``hello`` -> ``welcome``),
* decodes each ``request``'s parameters (arrays and DataFrames arrive
  materialized, via the shared protocol),
* dispatches it to its handler table -- ALWAYS on Dragonfly's GUI thread,
  one request at a time (the safety invariant every round since 5 rests
  on: ORS object operations happen only on the GUI thread, and requests
  are serialized, never interleaved),
* drives the handler's progress as ``progress`` frames toward the app and
  honors the app's ``cancel`` frames through a duck progress object the
  handlers already accept (the checkpoint pulls pending bytes with a
  timed-out ``waitForReadyRead`` -- probed on the shipped Qt: that moves
  peer bytes into the buffer without running the event loop and without
  delivering queued slots, so no nested handler fires mid-pump),
* writes its responses through the protocol's chunked frame writer
  (protocol version 2): a large response streams as bounded frames, and
  the outbound queue drains synchronously in ``_WRITE_CHUNK`` slices,
  each pushed with a bounded ``waitForBytesWritten`` (never hung, never
  dependent on the peer's event-loop pumping), so a multi-gigabyte
  publish never materializes a full wire copy -- the ACCEPTED COST,
  documented: such a response streams from the GUI thread itself, so
  Dragonfly's UI pauses for the transfer's duration (the handler must
  not pump Qt events from inside the transfer -- the ORS re-entrancy
  rule), and a payload the RAM feasibility check refuses never reaches
  the wire: it becomes a normal ``ok:False`` error envelope and the
  connection stays alive,
* cleans up on disconnect: queued requests are dropped, and a response
  that could not be delivered has its freshly published object deleted
  again through the discard hook (the glue injects
  ``dragonflynotebook_lib.discard_bridge_object``).The service itself is ORS-free: it is constructed with a handler TABLE
(a dict of method name -> ``handler(params, progress) -> payload``), which
keeps it unit-testable in the framework-free tier with fake handlers. The
cancellation path deserves its own note -- it is the round-6 lesson in
reverse: ``getIsCancelled`` during a handler pulls the socket's pending
bytes SYNCHRONOUSLY (a timed-out ``waitForReadyRead`` + ``readAll`` -- no
event loop, and no queued-slot delivery, probed on the shipped Qt), so a
cancel is seen mid-handler WITHOUT pumping Dragonfly's event loop -- the
exact re-entrancy the round-6/7 crash class forbids. The notebook-originated
requests additionally run with the handler's event pump stood down
(``pump=False`` in the lib), so nothing on the notebook path ever opens a
native progress dialog or pumps events.
"""

import collections

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from . import notebook_bridge_protocol as protocol

#: bytes written per bounded synchronous drain step: bounded slices keep
#: each individual pipe wait short while a multi-megabyte response crosses.
_WRITE_CHUNK = 512 * 1024

#: the bound on each ``waitForBytesWritten`` inside the outbound drain: a
#: peer that stops reading for this long kills the connection instead of
#: hanging the handler thread forever.
_WRITE_WAIT_MS = 5000

_HANDSHAKE_TIMEOUT_S = 10.0


class _WriteTimeout(Exception):
    """Raised when a bounded outbound write wait expires (the peer stopped
    reading); the drain converts it into a clean connection abort."""


class _ResponseError(Exception):
    """The carrier for an ``ok:False`` response whose class name and
    message travel verbatim (the handler-failure path); raised nowhere,
    caught by :meth:`_BridgeConnection._send_error_envelope` only."""

    def __init__(self, error_class, message):
        super().__init__(message)
        self.error_class = error_class


class _BridgeProgress(object):
    """The duck progress object a bridge handler receives: the SAME surface
    ``dragonflynotebook_lib._ProgressSession`` drives on a native ORS
    ``Progress`` (``startProgressWithCaption`` / ``updateProgress`` /
    ``getIsCancelled`` / ``closeProgress``), but every step is forwarded to
    the notebook application as a ``progress`` frame and cancellation comes
    from the app's ``cancel`` frames (or the connection dropping). It is
    NOT a real ``Progress`` instance, so the lib never forwards it to
    native ORS calls and never creates a dialog."""

    def __init__(self, connection, request_id):
        self._connection = connection
        self._request_id = request_id
        self._caption = ''
        self._of = 0

    def startProgressWithCaption(self, caption, i_range, b_cancellable):
        self._caption = str(caption)
        self._of = int(i_range)
        self._connection.send_progress(self._request_id, self._caption, 0,
                                       self._of)

    def updateProgress(self, i_position):
        self._connection.send_progress(self._request_id, self._caption,
                                       int(i_position), self._of)

    def getIsCancelled(self):
        return self._connection.is_cancelled(self._request_id)

    def closeProgress(self):
        self._connection.send_progress(self._request_id, self._caption,
                                       self._of, self._of)


class _BridgeConnection(QObject):
    """One connected notebook application: framing in, requests queued,
    responses chunked out."""

    #: emitted when the connection is fully gone (the service drops it)
    gone = pyqtSignal(object)

    def __init__(self, socket, service, parent=None):
        super().__init__(parent)
        self._socket = socket
        self._service = service
        self._reader = protocol.FrameReader()
        self._handshaked = False
        self._pending = []          # (request_id, method, params)
        self._busy = False
        self._current_id = None
        self._cancelled = set()     # request ids the app gave up on
        # the outbound FIFO: ('frame', bytes) for small frames, or
        # ('stream', iterator) for a chunked transfer whose frames are
        # pulled one at a time (as segment lists referencing the parts'
        # own buffers), so a multi-gigabyte response never sits in a
        # queue as a whole second copy; an oversized in-flight frame
        # parks its remaining segments in _current_segments/
        # _current_index/_current_offset, which drain BEFORE the queue
        # resumes
        self._out_queue = collections.deque()
        self._current_segments = None
        self._current_index = 0
        self._current_offset = 0
        self._writing = False
        socket.setParent(self)
        socket.readyRead.connect(self._on_ready_read)
        socket.bytesWritten.connect(self._on_bytes_written)
        socket.disconnected.connect(self._on_disconnected)
        socket.errorOccurred.connect(self._on_disconnected)

    # -- inbound -------------------------------------------------------------

    def _on_ready_read(self):
        data = bytes(self._socket.readAll().data())
        if not data:
            return
        try:
            frames = self._reader.feed(data)
        except ValueError:
            self.abort()  # a corrupted stream cannot recover
            return
        except RuntimeError as refusal:
            # the receiver-side RAM refusal: the reader drained the
            # announced chunks and the stream is aligned again; a refused
            # REQUEST gets its normal error envelope, the connection
            # stays alive
            header = getattr(refusal, 'header_envelope', None)
            if isinstance(header, dict) and \
                    header.get('type') == protocol.REQUEST:
                self.send_response(int(header.get('id') or 0), False, None,
                                   'RuntimeError', str(refusal))
                return
            self.abort()
            return
        for envelope, parts in frames:
            self._on_frame(envelope, parts)

    def _on_frame(self, envelope, parts):
        kind = envelope.get('type')
        if kind == protocol.HELLO:
            if self._handshaked:
                return
            self._handshaked = True
            self._send_raw(protocol.encode_frame(
                {'type': protocol.WELCOME,
                 'version': protocol.PROTOCOL_VERSION}))
            return
        if not self._handshaked:
            self.abort()  # no request before the handshake
            return
        if kind == protocol.REQUEST:
            try:
                params = protocol.decode_value(envelope.get('params') or {},
                                               parts)
            except Exception:
                self.send_response(int(envelope.get('id') or 0), False, None,
                                   'ValueError',
                                   'The request parameters could not be '
                                   'decoded.')
                return
            request_id = int(envelope.get('id') or 0)
            if self._cancelled and request_id in self._cancelled:
                self._cancelled.discard(request_id)
                self.send_response(request_id, False, None, 'RuntimeError',
                                   'Operation cancelled by user.')
                return
            self._pending.append((request_id,
                                  str(envelope.get('method') or ''),
                                  params))
            self._maybe_execute_next()
            return
        if kind == protocol.CANCEL:
            # the app gave up on a request: either in flight (the next
            # cancel checkpoint aborts the handler) or queued (dropped
            # with a cancelled response when it reaches the front)
            self._cancelled.add(int(envelope.get('id') or 0))
            return

    # -- request execution (one at a time, on the GUI thread) ----------------

    def _maybe_execute_next(self):
        if self._busy or not self._pending:
            return
        request_id, method, params = self._pending.pop(0)
        self._execute(request_id, method, params)

    def _execute(self, request_id, method, params):
        if request_id in self._cancelled:
            # the app gave up while this request waited in the queue
            self._cancelled.discard(request_id)
            self.send_response(request_id, False, None, 'RuntimeError',
                               'Operation cancelled by user.')
            QTimer.singleShot(0, self._maybe_execute_next)
            return
        self._busy = True
        self._current_id = request_id
        handler = self._service.handlers.get(method)
        progress = _BridgeProgress(self, request_id)
        payload = None
        error = None
        try:
            if handler is None:
                raise RuntimeError(
                    'The bridge has no handler for {!r}.'.format(method))
            payload = handler(params, progress)
        except BaseException as exc:  # everything crosses as an envelope
            error = (type(exc).__name__, str(exc))
        response_ok = error is None
        delivered = self.send_response(request_id, response_ok, payload,
                                       *(error or (None, None)))
        if not delivered:
            self._service.discard_payload(payload)
        self._busy = False
        self._current_id = None
        if not self._pending or not self._handshaked:
            return
        # the next request goes through the event loop, never re-entrantly:
        # between two handlers every queued event (repaints, timers, the
        # peer's next frames) gets its turn
        QTimer.singleShot(0, self._maybe_execute_next)

    def send_response(self, request_id, ok, payload, error_class=None,
                      message=None):
        """Encodes and queues the response through the chunked frame
        writer; returns False when the peer is already gone (the payload
        then needs the discard hook). A successful payload is recorded
        until it has fully drained, so a disconnect can clean it up. The
        send-side RAM feasibility gate runs BEFORE anything is queued: a
        refusal (or an unencodable payload) becomes a NORMAL
        ``ok:False`` error envelope, so the client re-raises the clean
        RuntimeError and the connection stays healthy."""
        parts = []
        if ok:
            try:
                marker = protocol.encode_value(payload or {}, parts)
                protocol.check_parts_feasibility(parts,
                                                 'the response payload')
            except (ValueError, RuntimeError) as exc:
                return self._send_error_envelope(request_id, exc)
            envelope = {'type': protocol.RESPONSE, 'id': request_id,
                        'ok': True, 'payload': marker}
            self._service.record_payload(self, payload)
            self._send_stream(envelope, parts)
            return self._socket_connected
        return self._send_error_envelope(
            request_id, _ResponseError(str(error_class or 'Exception'),
                                       str(message or '')))

    def _send_error_envelope(self, request_id, exc):
        """One ``ok:False`` response frame carrying the exception CLASS
        NAME plus its message. A :class:`_ResponseError` carries its
        class name verbatim (the handler-failure path, unchanged); a
        ValueError / RuntimeError keeps its class (the RAM refusal's
        clean RuntimeError must survive the trip as a RuntimeError);
        anything else travels under its own class name."""
        if isinstance(exc, _ResponseError):
            error_class = exc.error_class
        else:
            error_class = type(exc).__name__
        self._send_raw(protocol.encode_frame(
            {'type': protocol.RESPONSE, 'id': request_id, 'ok': False,
             'error_class': error_class, 'message': str(exc)}))
        return self._socket_connected

    # -- progress + cancellation ----------------------------------------------

    def send_progress(self, request_id, caption, step, of):
        self._send_raw(protocol.encode_frame(
            {'type': protocol.PROGRESS, 'id': request_id, 'caption': caption,
             'step': int(step), 'of': int(of)}))

    def is_cancelled(self, request_id):
        """The handler's cancel checkpoint: pulls the socket's pending bytes
        SYNCHRONOUSLY (no event loop -- the round-6 rule). Probed on the
        shipped Qt 6.7.1: ``readAll`` alone never sees peer bytes without an
        event-loop pass, but a timed-out ``waitForReadyRead`` moves pending
        pipe bytes into the socket's buffer WITHOUT running the event loop
        and WITHOUT delivering queued slots or timers -- so a cancel frame
        that arrived mid-handler is honored at the very next checkpoint and
        no nested handler can fire mid-pump."""
        socket = self._socket
        if socket is None:
            return True  # the connection is gone: the handler must cancel
        if socket.bytesAvailable() == 0:
            try:
                socket.waitForReadyRead(0)
            except Exception:
                pass  # a dead socket surfaces through the state check below
        data = bytes(socket.readAll().data())
        if data:
            try:
                for envelope, parts in self._reader.feed(data):
                    kind = envelope.get('type')
                    if kind == protocol.CANCEL:
                        self._cancelled.add(int(envelope.get('id') or 0))
                    elif kind == protocol.REQUEST:
                        self._pending.append(
                            (int(envelope.get('id') or 0),
                             str(envelope.get('method') or ''),
                             protocol.decode_value(envelope.get('params')
                                                   or {}, parts)))
            except Exception:
                pass  # a corrupt stream surfaces at the next readyRead
        return request_id in self._cancelled or \
            socket.state() != QLocalSocket.LocalSocketState.ConnectedState

    @property
    def _socket_connected(self):
        # None-safe: every send path reports this after the fact, and an
        # aborted connection (self._socket is None) is simply "not
        # connected" -- raising here would send an AttributeError up the
        # Qt slot the send was called from
        socket = self._socket
        return socket is not None and \
            socket.state() == QLocalSocket.LocalSocketState.ConnectedState

    # -- outbound (chunked) ----------------------------------------------------

    def _send_raw(self, frame):
        self._out_queue.append(('frame', frame))
        self._flush_outgoing()

    def _send_stream(self, envelope, parts=None):
        """Queues one logical message's chunked frame sequence: the frames
        are PULLED one at a time, as SEGMENT LISTS whose bytes reference
        the parts' own buffers, so a multi-gigabyte response streams
        without ever existing as a second whole-wire copy."""
        self._out_queue.append(('stream',
                                protocol.iter_frame_segments(envelope,
                                                             parts)))
        self._flush_outgoing()

    def _flush_outgoing(self):
        """Drains the whole outbound FIFO synchronously, in bounded
        ``_WRITE_CHUNK`` slices, each pushed to the OS pipe with a bounded
        ``waitForBytesWritten``. This deliberately does NOT depend on the
        peer's event loop delivering ``bytesWritten``: a reader that sits
        inside ``waitForReadyRead`` would otherwise advance the stream at
        one slice per lucky pump (the round-25 hang). The cost is that the
        handler thread stays inside this drain until the response is out,
        which is the documented GUI pause for large transfers."""
        if self._writing or self._socket is None:
            return
        self._writing = True
        try:
            while self._socket is not None:
                # the parked remainder of an oversized in-flight frame
                # drains FIRST (the queue resumes only after the whole
                # frame is out)
                if self._current_segments is not None:
                    segments = self._current_segments
                    index = self._current_index
                    offset = self._current_offset
                    segment = segments[index]
                    piece = segment[offset:offset + _WRITE_CHUNK]
                    offset += len(piece)
                    if offset >= len(segment):
                        index += 1
                        offset = 0
                        if index >= len(segments):
                            self._current_segments = None
                            self._current_index = 0
                            self._current_offset = 0
                    self._current_index = index
                    self._current_offset = offset
                    self._start_write(piece)
                    continue
                if not self._out_queue:
                    break
                kind, item = self._out_queue[0]
                if kind == 'stream':
                    # the generator stays queued until it is exhausted:
                    # each pulled frame is written, then the NEXT pull
                    # resumes the same stream
                    segments = next(item, None)
                    if segments is None:
                        self._out_queue.popleft()
                        continue
                else:
                    segments = [item]
                    self._out_queue.popleft()
                total = sum(len(segment) for segment in segments)
                if total <= _WRITE_CHUNK:
                    for segment in segments:
                        self._start_write(segment)
                    continue
                # bounded slices keep the handler's pause granular; the
                # frame's remainder parks outside the queue (which still
                # holds the stream, if this frame came from one)
                self._current_segments = segments
                self._current_index = 0
                self._current_offset = 0
        except _WriteTimeout:
            # the peer stopped reading for a whole wait window: drop the
            # connection cleanly (the teardown discards the recorded
            # payload) rather than hanging the handler thread forever
            self.abort()
        except Exception:
            # defensive: NOTHING raised by the writer (a stream pull
            # gone wrong, the writer's own mismatch self-check, an
            # unexpected encoding error mid-transfer) may ever reach the
            # Qt slot it was called from -- that aborts the process. The
            # stream is mid-flight, so an error envelope cannot repair
            # it; the clean outcome is a connection abort, whose
            # teardown discards the recorded payload.
            self.abort()
        finally:
            self._writing = False
        # The bounded waits consume the write completions themselves
        # (probed: bytesWritten fires INSIDE waitForBytesWritten, and a
        # response that finishes inside a synchronous drain leaves NO
        # completion for the event loop to deliver later), so
        # _on_bytes_written's clear_payload would never run for such a
        # response and the recorded publish payload would be held until
        # disconnect. Clear it here when the drain ended with a fully
        # empty socket.
        if self._socket is not None and not self._out_queue \
                and self._current_segments is None \
                and self._socket.bytesToWrite() == 0:
            self._service.clear_payload(self)

    def _start_write(self, piece):
        self._socket.write(bytes(piece))
        # a re-entrant handler during the bounded wait may abort() (the
        # socket goes away); the drain loop notices and stops cleanly
        while self._socket is not None and self._socket.bytesToWrite() > 0:
            if not self._socket.waitForBytesWritten(_WRITE_WAIT_MS):
                raise _WriteTimeout(
                    'dfnb bridge write stalled for {} ms'.format(
                        _WRITE_WAIT_MS))

    def _on_bytes_written(self, _count):
        if self._writing:
            return  # the synchronous drain in _flush_outgoing owns the flow
        if self._out_queue or self._current_segments is not None:
            self._flush_outgoing()
        else:
            # everything encoded has drained to the peer: the payload is
            # delivered as far as this side can tell
            self._service.clear_payload(self)

    # -- teardown ---------------------------------------------------------------

    def _on_disconnected(self, *args):
        self.abort()

    def abort(self):
        """Drops the connection: pending requests are discarded and the
        last recorded response payload (an abandoned publish) is deleted
        through the discard hook -- disconnect cleanup."""
        self._cancelled.clear()
        self._pending = []
        self._out_queue.clear()
        self._current_segments = None
        self._current_index = 0
        self._current_offset = 0
        self._writing = False
        recorded = self._service.take_payload(self)
        if recorded is not None:
            self._service.discard_payload(recorded)
        socket = self._socket
        self._socket = None
        if socket is not None:
            try:
                socket.disconnectFromServer()
            except Exception:
                pass
            socket.deleteLater()
        self.gone.emit(self)
        self.deleteLater()


class NotebookBridgeService(QObject):
    """The Dragonfly-side server: one notebook application per connection,
    requests serialized on the GUI thread."""

    #: a connection completed the handshake (the glue logs / counts it)
    client_arrived = pyqtSignal()
    #: a connection is fully gone
    client_gone = pyqtSignal()

    def __init__(self, handlers, pipe_name=None, discard_hook=None,
                 parent=None):
        super().__init__(parent)
        self.handlers = dict(handlers)
        self._discard_hook = discard_hook
        self._pipe_name = pipe_name or protocol.bridge_pipe_name()
        self._connections = []
        self._last_payloads = {}  # id(_BridgeConnection) -> last payload
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        QLocalServer.removeServer(self._pipe_name)
        self._listening = self._server.listen(self._pipe_name)

    @property
    def pipe_name(self):
        return self._pipe_name

    @property
    def is_listening(self):
        return self._listening

    @property
    def connection_count(self):
        return len(self._connections)

    def record_payload(self, connection, payload):
        """Remembers the most recent successful response payload for a
        connection until it has drained (or the connection dies)."""
        if isinstance(payload, dict) and payload.get('published'):
            self._last_payloads[id(connection)] = payload

    def clear_payload(self, connection):
        """The connection's outgoing buffer fully drained -- the payload was
        delivered as far as this side can tell."""
        self._last_payloads.pop(id(connection), None)

    def take_payload(self, connection):
        """Pops the recorded payload (disconnect cleanup)."""
        return self._last_payloads.pop(id(connection), None)

    def discard_payload(self, payload):
        """Runs the discard hook over a payload's published handle (the
        disconnect cleanup of an abandoned publish)."""
        published = (payload or {}).get('published') if isinstance(
            payload, dict) else None
        guid = (published or {}).get('guid') if isinstance(published,
                                                           dict) else None
        if guid and self._discard_hook is not None:
            try:
                self._discard_hook(str(guid))
            except Exception:
                pass

    def _on_new_connection(self):
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            connection = _BridgeConnection(socket, self, parent=self)
            connection.gone.connect(self._on_connection_gone)
            self._connections.append(connection)
            self.client_arrived.emit()
            # A fast client can connect AND write its HELLO in the window
            # before this side processes the accept; readyRead does not
            # re-fire for bytes that were already buffered when the
            # socket object was created. Feed whatever is buffered now --
            # a plain read of arrived bytes, no event loop -- so the
            # handshake never waits on a notification for old bytes.
            if socket.bytesAvailable() > 0:
                connection._on_ready_read()

    def _on_connection_gone(self, connection):
        if connection in self._connections:
            self._connections.remove(connection)
        self._last_payloads.pop(id(connection), None)
        self.client_gone.emit()

    def close(self):
        """Stops listening and tears every connection down."""
        for connection in list(self._connections):
            connection.abort()
        self._connections = []
        if self._server is not None:
            self._server.close()
            self._listening = False
