"""
Centralized logging system that streams logs to the frontend via WebSocket.

All Python loggers across all backend modules feed into a single WebSocket
stream so the GUI can display a real-time log panel without needing terminal access.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import List
from fastapi import WebSocket

class LogRecord:
    """A structured log entry ready for JSON serialization."""
    __slots__ = ('timestamp', 'level', 'source', 'message')

    def __init__(self, timestamp: str, level: str, source: str, message: str):
        self.timestamp = timestamp
        self.level = level
        self.source = source
        self.message = message

    def to_dict(self):
        return {
            "type": "log",
            "timestamp": self.timestamp,
            "level": self.level,
            "source": self.source,
            "message": self.message,
        }


class LogBuffer:
    """Thread-safe circular buffer that stores the last N log entries."""

    def __init__(self, max_size: int = 500):
        self._buffer: List[dict] = []
        self._max_size = max_size

    def append(self, entry: dict):
        self._buffer.append(entry)
        if len(self._buffer) > self._max_size:
            self._buffer = self._buffer[-self._max_size:]

    def get_all(self) -> List[dict]:
        return list(self._buffer)

    def clear(self):
        self._buffer.clear()


# Singleton buffer used by the handler and the WebSocket endpoint
log_buffer = LogBuffer(max_size=500)


class WebSocketLogHandler(logging.Handler):
    """
    A Python logging.Handler that:
      1. Writes every log record into the in-memory LogBuffer.
      2. Broadcasts it to all connected log-WebSocket clients.
    """

    def __init__(self):
        super().__init__()
        self.connections: List[WebSocket] = []
        self._loop = None

    def set_loop(self, loop):
        """Must be called once from the running asyncio event loop."""
        self._loop = loop

    def emit(self, record: logging.LogRecord):
        try:
            entry = LogRecord(
                timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                level=record.levelname,
                source=record.name,
                message=self.format(record),
            ).to_dict()

            log_buffer.append(entry)
            self._broadcast(json.dumps(entry))
        except Exception:
            self.handleError(record)

    def _broadcast(self, msg: str):
        import asyncio
        if not self._loop or not self.connections:
            return
        for ws in list(self.connections):
            try:
                asyncio.run_coroutine_threadsafe(ws.send_text(msg), self._loop)
            except Exception:
                pass

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)
        # Send buffered history
        for entry in log_buffer.get_all():
            try:
                await ws.send_text(json.dumps(entry))
            except Exception:
                break

    def disconnect(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)


# Singleton handler — import this and attach it to loggers
ws_log_handler = WebSocketLogHandler()
ws_log_handler.setFormatter(logging.Formatter("%(message)s"))


def install_global_logging():
    """
    Call once at startup to wire *all* loggers through the WebSocket handler.
    This patches the root logger so every module's logger automatically streams.
    """
    root = logging.getLogger()
    root.setLevel(logging.INFO)  # Changed from DEBUG to INFO to avoid library spam

    # Explicitly silence particularly noisy libraries
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Avoid duplicate handler attachment on hot-reload
    for h in root.handlers[:]:
        if isinstance(h, WebSocketLogHandler):
            root.removeHandler(h)

    ws_log_handler.setLevel(logging.DEBUG) # Our app's debug logs will still show
    root.addHandler(ws_log_handler)

    # Also make sure there's a console handler for terminal output
    has_stream = any(isinstance(h, logging.StreamHandler) and not isinstance(h, WebSocketLogHandler) for h in root.handlers)
    if not has_stream:
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter("[%(asctime)s] %(name)s/%(levelname)s: %(message)s", datefmt="%H:%M:%S"))
        root.addHandler(console)
