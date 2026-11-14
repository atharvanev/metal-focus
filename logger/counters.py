"""App-switch counter fed by NSWorkspace activation notifications.

This is the only thing the logger counts. There is deliberately no keyboard or mouse counter.
"""

import threading


class SwitchCounter:
    def __init__(self) -> None:
        self._n = 0
        self._lock = threading.Lock()

    def on_app_switch(self) -> None:
        with self._lock:
            self._n += 1

    def snapshot_and_reset(self) -> int:
        with self._lock:
            n, self._n = self._n, 0
            return n
