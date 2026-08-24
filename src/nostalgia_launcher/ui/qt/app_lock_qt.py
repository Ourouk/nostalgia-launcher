"""Qt transport for the single-instance guard (QLocalServer/QLocalSocket).

The guard key comes from `core.app_lock.state_key(active_profile.
state_path())`, so a second launch of the SAME profile defers to the
running one while DIFFERENT profiles run in parallel (distinct keys,
distinct store locks).

Protocol: exactly ONE JSON line per connection, e.g. ``{"op": "raise"}``
(focus the existing window). Unknown ops are ignored with a log line.
A future headless CLI reuses this guard with ``{"op": "busy"}``
semantics — exiting with code 7 instead of raising a GUI.

Stale-server cleanup: when a crashed instance left its socket behind,
``listen()`` reports AddressInUse; ``removeServer(key)`` clears it and
listening is retried ONCE.

All Qt imports stay INSIDE functions so importing this module never
requires PySide6 (core stays GUI-free; the CLI degrades to lock-only
protection when Qt is absent).
"""

import json

# Servers WE started in this process, keyed by guard key. Keeps the
# QLocalServer referenced (a dropped server stops listening) and lets
# teardown/tests close them deterministically.
_SERVERS = {}


def stop_server(key):
    """Close our server for ``key`` and remove its socket file."""
    from PySide6.QtNetwork import QLocalServer

    server = _SERVERS.pop(key, None)
    if server is not None:
        server.close()
    QLocalServer.removeServer(key)


def stop_all():
    """Close every server this process started (test/teardown helper)."""
    for key in list(_SERVERS):
        stop_server(key)


def try_connect_existing(key, timeout_ms=0):
    """Attempt a connection to an already-running instance. Returns the
    connected QLocalSocket, or None when nobody is listening."""
    from PySide6.QtNetwork import QLocalSocket

    sock = QLocalSocket()
    sock.connectToServer(key)
    if sock.waitForConnected(timeout_ms):
        return sock
    sock.disconnectFromServer()
    return None


def send_json_line(sock, payload):
    """Send one JSON line and detach (fire-and-forget second instance)."""
    data = (json.dumps(payload) + "\n").encode("utf-8")
    sock.write(data)
    sock.flush()
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()


def serve(key, on_message=None):
    """Listen on ``key``, dispatching each incoming JSON line as a dict.

    Messages are re-emitted through a QObject signal (`relay.message`)
    so handlers always run on the thread owning the server's event loop
    (the GUI main thread) — the same marshaling pattern the controller
    bridge uses for worker events. Returns ``(server, relay)``.
    Raises RuntimeError when listening fails even after stale cleanup.
    """
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtNetwork import QAbstractSocket, QLocalServer

    class _Relay(QObject):
        message = Signal(object)

    server = QLocalServer()
    if not server.listen(key):
        # Crashed instance left its socket behind → AddressInUse: clear
        # it and retry ONCE.
        if (
            server.serverError()
            == QAbstractSocket.SocketError.AddressInUseError
        ):
            QLocalServer.removeServer(key)
            if not server.listen(key):
                raise RuntimeError(
                    f"single-instance server listen failed: "
                    f"{server.errorString()}"
                )
        else:
            raise RuntimeError(
                f"single-instance server listen failed: {server.errorString()}"
            )

    relay = _Relay()
    if on_message is not None:
        relay.message.connect(on_message)
    _SERVERS[key] = server

    def _on_new_connection():
        sock = server.nextPendingConnection()
        if sock is None:
            return

        buffer = bytearray()

        def _on_ready_read():
            buffer.extend(bytes(sock.readAll()))
            while b"\n" in buffer:
                line, _, rest = bytes(buffer).partition(b"\n")
                del buffer[:]
                buffer.extend(rest)
                try:
                    payload = json.loads(line.decode("utf-8"))
                except ValueError:
                    payload = {"op": None}
                relay.message.emit(payload)

        sock.readyRead.connect(_on_ready_read)
        sock.disconnected.connect(sock.deleteLater)

    server.newConnection.connect(_on_new_connection)
    return server, relay
