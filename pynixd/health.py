"""What "healthy" means for pynixd, and the loop measurement behind it.

Two things a probe has to tell apart, and a TCP accept on the data port tells
neither (nixkube#37, #53):

- **Wedged.** The kernel completes `connect()` from the listen backlog whether
  or not the application ever accepts, so a TCP probe passes against a process
  that has stopped serving entirely.
- **Busy.** A transfer that does not yield stalls the loop, and a stalled loop
  answers nothing. The probe then fails against a process that is working
  correctly, and the restart destroys the evidence.

So health here is two questions. Is the loop running callbacks, and is every
interface the configuration asked for actually serving? Neither is answerable
from outside the process.

`EVENT_LOOP_LAG` is exported whether or not it is over the threshold, because
the useful form of this number is a graph that rises before anything restarts.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import anyio
import structlog

from . import metrics

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class HealthReport:
    """The verdict, and why.

    `checks` carries every check by name so a failing probe says which one
    failed in its own response body. A probe that only says "unhealthy" moves
    the diagnosis to a log nobody has yet.
    """

    ok: bool
    checks: Mapping[str, str] = field(default_factory=dict)

    def as_text(self) -> str:
        head = "ok" if self.ok else "unhealthy"
        body = "\n".join(f"{name}: {state}" for name, state in sorted(self.checks.items()))
        return f"{head}\n{body}\n" if body else f"{head}\n"


class LoopLagMonitor:
    """Sample how long the event loop goes without running a ready callback.

    The measurement is the delay a `sleep` overshoots by: the loop was asked to
    wake this task after `interval` and did not, so the difference is time it
    spent not scheduling. That is exactly what a probe experiences.

    `window_max` is what health asks about, and it decays -- a stall a minute
    ago must not hold the process unhealthy once it is serving again.
    `lifetime_max` never decays, because the largest stall a process ever had
    is what an operator wants after the fact.
    """

    def __init__(
        self,
        interval: float = 0.25,
        window: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._interval = interval
        self._window = window
        # Injectable so the decay rule can be tested by moving time rather than
        # by waiting for it. A test that sleeps asserts on the machine.
        self._clock = clock
        self._samples: list[tuple[float, float]] = []
        self.lifetime_max = 0.0
        self._stop = anyio.Event()
        self.started = anyio.Event()
        """Set once a sample exists, so a waiter need not guess at startup."""
        self._sampled = anyio.Event()

    @property
    def window_max(self) -> float:
        cutoff = self._clock() - self._window
        self._samples = [(t, lag) for t, lag in self._samples if t >= cutoff]
        return max((lag for _, lag in self._samples), default=0.0)

    async def wait_for_sample(self) -> None:
        """Return once another sample lands.

        `start_soon` schedules a task; it does not run one. Anything that wants
        the monitor to have observed something has to wait for the observation,
        not for a duration.
        """
        self._sampled = anyio.Event()
        await self._sampled.wait()

    async def run(self) -> None:
        """Sample until stopped. Intended to run as a background task."""
        while not self._stop.is_set():
            t0 = self._clock()
            await anyio.sleep(self._interval)
            lag = self._clock() - t0 - self._interval
            if lag < 0:
                # A clock that went backwards, or a sleep that returned early.
                # Neither is a stall, and recording it would lower the maximum.
                continue
            self._samples.append((self._clock(), lag))
            self.lifetime_max = max(self.lifetime_max, lag)
            metrics.EVENT_LOOP_LAG.set(self.window_max)
            metrics.EVENT_LOOP_LAG_MAX.set(self.lifetime_max)
            self.started.set()
            self._sampled.set()

    def stop(self) -> None:
        self._stop.set()
