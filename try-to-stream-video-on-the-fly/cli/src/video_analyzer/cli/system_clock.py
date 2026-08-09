from __future__ import annotations

import time

from video_analyzer import kernel


class SystemClock(kernel.Clock):
    def now(self) -> float:
        return time.monotonic()
