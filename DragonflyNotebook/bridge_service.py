r"""The Dragonfly-side bridge service (round 8).

``NotebookBridgeService`` owns one ``QLocalServer`` named ``dfnb_bridge_<pid>``
(protocol :func:`~notebook_bridge_protocol.bridge_pipe_name`, so two
Dragonfly instances never collide). Each standalone notebook application
process connects as its own client; the service

* completes the version handshake (``hello`` -> ``welcome``),
* decodes each ``request``'s parameters (arrays and DataFrames arrive
  materialized, via the shared protocol),
* dispatches it to its handler table — ALWAYS on Dragonfly's GUI thread,
  one request at a time (the safety invariant every round since 5 rests
  on: ORS object operations happen only on the GUI thread, and requests
  are serialized, never interleaved),
* drives the handler's progress as ``progress`` frames toward the app and
  honors the app's ``cancel`` frames through a duck progress object the
  handlers already accept (the checkpoint pulls pending bytes with a
  timed-out ``waitForReadyRead`` — probed on the shipped Qt: that moves
  peer bytes into the buffer without running the event loop and without
  delivering queued slots, so no nested handler fires mid-pump),
* writes its responses CHUNKED (``_WRITE_CHUNK`` bytes per event-loop
  turn, driven by ``bytesWritten``) so a multi-megabyte publish response
  never freezes Dragonfly's GUI, and
* cleans up on disconnect: queued requests are dropped, and a response
  that could not be delivered has its freshly published object deleted
  again through the discard hook (the glue injects
  ``dragonflynotebook_lib.discard_bridge_object``).

The service itself is ORS-free: it is constructed with a handler TABLE
(a dict of method name -> ``handler(params, progress) -> payload``), which
keeps it unit-testable in the framework-free tier with fake handlers. The
cancellation path deserves its own note — it is the round-6 lesson in
reverse: ``getIsCancelled`` during a handler pulls the socket's pending
bytes SYNCHRONOUSLY (a timed-out ``waitForReadyRead`` + ``readAll`` — no
event loop, and no queued-slot delivery, probed on the shipped Qt), so a
cancel is seen mid-handler WITHOUT pumping Dragonfly's event loop — the
exact re-entrancy the round-6/7 crash class forbids. The notebook-originated
requests additionally run with the handler's event pump stood down
(``pump=False`` in the lib), so nothing on the notebook path ever opens a
native progress dialog or pumps events.
"""

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from . import notebook_bridge_protocol as protocol

#: bytes written per event-loop turn: bounded slices keep Dragonfly's GUI
#: responsive while a multi-megabyte response crosses the pipe.
_WRITE_CHUNK = 512 * 1024

_HANDSHAKE_TIMEOUT_S = 10.0


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
        self._outgoing = bytearray()
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
        """Encodes and queues the response frame; returns False when the
        peer is already gone (the payload then needs the discard
        hook). A successful payload is recorded until it has fully
        drained, so a disconnect can clean it up."""
        parts = []
        if ok:
            envelope = {'type': protocol.RESPONSE, 'id': request_id,
                        'ok': True,
                        'payload': protocol.encode_value(payload or {}, parts)}
            self._service.record_payload(self, payload)
        else:
            envelope = {'type': protocol.RESPONSE, 'id': request_id,
                        'ok': False,
                        'error_class': str(error_class or 'Exception'),
                        'message': str(message or '')}
        self._send_raw(protocol.encode_frame(envelope, parts))
        return self._socket_connected

    # -- progress + cancellation ----------------------------------------------

    def send_progress(self, request_id, caption, step, of):
        self._send_raw(protocol.encode_frame(
            {'type': protocol.PROGRESS, 'id': request_id, 'caption': caption,
             'step': int(step), 'of': int(of)}))

    def is_cancelled(self, request_id):
        """The handler's cancel checkpoint: pulls the socket's pending bytes
        SYNCHRONOUSLY (no event loop — the round-6 rule). Probed on the
        shipped Qt 6.7.1: ``readAll`` alone never sees peer bytes without an
        event-loop pass, but a timed-out ``waitForReadyRead`` moves pending
        pipe bytes into the socket's buffer WITHOUT running the event loop
        and WITHOUT delivering queued slots or timers — so a cancel frame
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
        return self._socket.state() == \
            QLocalSocket.LocalSocketState.ConnectedState

    # -- outbound (chunked) ----------------------------------------------------

    def _send_raw(self, frame):
        self._outgoing.extend(frame)
        self._flush_outgoing()

    def _flush_outgoing(self):
        if self._writing or not self._outgoing:
            return
        chunk = bytes(self._outgoing[:_WRITE_CHUNK])
        del self._outgoing[:len(chunk)]
        self._writing = True
        self._socket.write(chunk)

    def _on_bytes_written(self, _count):
        self._writing = False
        if self._outgoing:
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
        through the discard hook — disconnect cleanup."""
        self._cancelled.clear()
        self._pending = []
        self._outgoing = bytearray()
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
        """The connection's outgoing buffer fully drained — the payload was
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
            # socket object was created. Feed whatever is buffered now —
            # a plain read of arrived bytes, no event loop — so the
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
