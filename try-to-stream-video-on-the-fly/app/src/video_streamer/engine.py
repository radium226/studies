"""Per-frame transform: timestamp overlay, plus a bucket-gated lag simulation.

Owns the token bucket entirely - callers just hand over a frame and get back
the frame to write, with no awareness of the bucket or the lag simulation.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import cv2
import numpy as np

from video_streamer.overlay import draw_overlay
from video_streamer.token_bucket import TokenBucket

INPUT_LAG_MIN_SECONDS = 0.0
INPUT_LAG_MAX_SECONDS = 0.25
MAX_ACCUMULATED_LAG_SECONDS = 2.0
LAG_BUDGET_REFILL_RATE = 0.05


def process_frame(frame: np.ndarray) -> np.ndarray:
    time.sleep(random.uniform(INPUT_LAG_MIN_SECONDS, INPUT_LAG_MAX_SECONDS))
    height, width = frame.shape[:2]
    return cv2.rectangle(frame.copy(), (0, 0), (width - 1, height - 1), (0, 0, 255), 8)


class Engine:
    def __init__(
        self,
        *,
        simulate_input_lag: bool = False,
        capacity: float = MAX_ACCUMULATED_LAG_SECONDS,
        refill_rate: float = LAG_BUDGET_REFILL_RATE,
        threshold: float = INPUT_LAG_MAX_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._simulate_input_lag = simulate_input_lag
        self._threshold = threshold
        self._clock = clock
        # token bucket, starting full: an initial burst spends it down (the
        # "adapting" stall), then it refills slower than real time so later
        # jitter is isolated single frames spaced seconds apart, not bursts
        self._lag_budget = TokenBucket(capacity, refill_rate, clock=clock)
        self._frame_index = 0
        self._executor = ThreadPoolExecutor(max_workers=1)

    @classmethod
    @asynccontextmanager
    async def start(cls, **kwargs) -> AsyncIterator[Engine]:
        self = cls(**kwargs)
        try:
            yield self
        finally:
            await self.aclose()

    async def process(self, frame: np.ndarray) -> np.ndarray:
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        frame = draw_overlay(frame, f"{timestamp}  frame {self._frame_index}")
        self._frame_index += 1

        if self._simulate_input_lag and self._lag_budget.try_acquire(self._threshold):
            before = self._clock()
            loop = asyncio.get_running_loop()
            frame = await loop.run_in_executor(self._executor, process_frame, frame)
            self._lag_budget.record_spend(self._clock() - before)

        return frame

    async def aclose(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
