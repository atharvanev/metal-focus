"""Exit when the menu bar app that started this job goes away.

The app (the only launch item) runs the background jobs as child processes with a pipe on stdin
and ``MF_SUPERVISED=1``. A child isn't killed when its parent crashes or gets ``kill -9``, but the
pipe closes then, so a thread blocked on stdin sees EOF and exits the job. Without this, the next
app launch would start a second runner / check-in scheduler next to the orphan.
"""

from __future__ import annotations

import os
import threading


def exit_with_parent() -> None:
    """No-op unless started by the app (``MF_SUPERVISED=1``), so CLI runs are unaffected."""
    if os.environ.get("MF_SUPERVISED") != "1":
        return

    def wait() -> None:
        try:
            # raw fd, not sys.stdin: a thread parked in BufferedReader.read holds its lock and
            # makes interpreter shutdown abort ("could not acquire lock for <stdin>") on normal exit
            while os.read(0, 4096):
                pass
        except (OSError, ValueError):
            pass
        os._exit(0)

    threading.Thread(target=wait, name="exit-with-parent", daemon=True).start()
