import time

class LoopState:
    """Shared visibility into background pipeline activity so the UI can always
    tell the user what is happening (never an unexplained stall).

    - touch(): called whenever the download/AI loops do any real work.
    - phase(): 'working' while work happened recently, 'idle' when the loops
      are running but the queue is empty, 'stopped' when loops are off.
    - heartbeat_due(): used by the download loop to broadcast a status message
      periodically even when idle, so connected UIs always get a signal.
    """

    def __init__(self):
        self._last_activity = time.time()
        self._last_heartbeat = 0.0

    def touch(self):
        self._last_activity = time.time()

    def activity_age(self) -> float:
        return time.time() - self._last_activity

    def heartbeat_due(self, interval: float = 10.0) -> bool:
        if time.time() - self._last_heartbeat >= interval:
            self._last_heartbeat = time.time()
            return True
        return False

    def phase(self, is_running: bool) -> str:
        if not is_running:
            return "stopped"
        return "working" if self.activity_age() < 20 else "idle"


loop_state = LoopState()
