"""Single-instance coordination for the desktop application."""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


ACTIVATE_MESSAGE = b"activate"


class SingleInstanceGuard(QObject):
    """Own an application lock and relay activation requests from later launches."""

    activation_requested = Signal()

    def __init__(
        self,
        key: str,
        *,
        lock_directory: str | Path | None = None,
    ) -> None:
        super().__init__()
        if not key.strip():
            raise ValueError("单实例标识不能为空。")

        directory = (
            Path(lock_directory)
            if lock_directory is not None
            else Path(tempfile.gettempdir())
        )
        directory.mkdir(parents=True, exist_ok=True)
        self._server_name = key
        self._lock = QLockFile(str(directory / f"{key}.lock"))
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._accept_connections)
        self._notification_socket: QLocalSocket | None = None
        self._started = False

    def start(self) -> bool:
        """Become the primary instance, or notify the primary and return ``False``."""

        if self._started:
            return True
        if not self._lock.tryLock(0):
            self._notify_primary()
            return False

        # A crashed process can leave its local-server endpoint behind even after
        # QLockFile has established that no primary instance is still alive.
        QLocalServer.removeServer(self._server_name)
        if not self._server.listen(self._server_name):
            self._lock.unlock()
            raise RuntimeError(
                f"无法建立 LineRead 单实例通道：{self._server.errorString()}"
            )
        self._started = True
        return True

    def close(self) -> None:
        """Release the local server and process lock if this is the primary."""

        if self._notification_socket is not None:
            self._notification_socket.abort()
            self._notification_socket.deleteLater()
            self._notification_socket = None
        if self._server.isListening():
            self._server.close()
            QLocalServer.removeServer(self._server_name)
        if self._lock.isLocked():
            self._lock.unlock()
        self._started = False

    def _notify_primary(self) -> bool:
        socket = QLocalSocket(self)
        self._notification_socket = socket
        socket.connectToServer(self._server_name)
        if not socket.waitForConnected(1000):
            return False
        socket.write(ACTIVATE_MESSAGE)
        socket.flush()
        if socket.bytesToWrite() == 0:
            return True
        return socket.waitForBytesWritten(1000)

    def _accept_connections(self) -> None:
        requested = False
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            if socket.bytesAvailable() == 0:
                socket.waitForReadyRead(100)
            requested = requested or bytes(socket.readAll()) == ACTIVATE_MESSAGE
            socket.disconnectFromServer()
            socket.deleteLater()
        if requested:
            self.activation_requested.emit()
