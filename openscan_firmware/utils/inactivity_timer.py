# inactivity_timer.py
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Union

log = logging.getLogger(__name__)

OnTimeout = Union[Callable[[], None], Callable[[], Awaitable[None]]]


@dataclass
class _InactivityTimer:
    timeout_s: float = 0.0
    on_timeout: OnTimeout | None = None
    name: str = "inactivity-timeout"
    enabled: bool = False

    _task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _reset_event: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)
    _stopped: bool = field(default=False, init=False, repr=False)

    _pause_count: int = field(default=0, init=False, repr=False)

    # --- check config ---
    def set_timeout(self, timeout_s: float) -> None:
        self.timeout_s = float(timeout_s)
        if self.timeout_s <= 0:
            self.enabled = False
        self._reset_event.set()  # sveglia il loop

    def enable(self, enabled: bool = True) -> None:
        self.enabled = bool(enabled) and self.timeout_s > 0
        self._reset_event.set()

    def disable(self) -> None:
        self.enable(False)

    # --- lifecycle task ---
    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopped = False
        self._task = asyncio.create_task(self._run(), name=self.name)

    async def _stop_async(self) -> None:
        # Stop the background task cleanly
        self._stopped = True
        self._reset_event.set()

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        self._task = None

    def stop(self) -> None:
        """
        Smart stop:
        - If no event loop is running, stop synchronously (blocking).
        - If an event loop is running, schedule async stop (non-blocking).
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop → safe to block until fully stopped
            asyncio.run(self._stop_async())
        else:
            # Running inside an event loop → cannot block here
            loop.create_task(self._stop_async())


    # --- “activity” ---
    def reset(self) -> None:
        if self._stopped or not self.enabled or self.timeout_s <= 0 or self.is_paused():
            return
        self._reset_event.set()

    # --- pause/resume (nesting-safe) ---
    def pause(self) -> None:
        self._pause_count += 1
        self._reset_event.set()

    def resume(self) -> None:
        if self._pause_count > 0:
            self._pause_count -= 1
        self._reset_event.set()
        if self._pause_count == 0:
            self.reset()

    def is_paused(self) -> bool:
        return self._pause_count > 0

    async def _call_on_timeout(self) -> None:
        if not self.on_timeout:
            return
        try:
            res = self.on_timeout()
            if inspect.isawaitable(res):
                await res
        except Exception:
            log.exception("[%s] error in on_timeout()", self.name)

    async def _run(self) -> None:
        try:
            while not self._stopped:
                # if disabled or paused don't count
                if (not self.enabled) or self.timeout_s <= 0 or self.is_paused():
                    try:
                        await asyncio.wait_for(self._reset_event.wait(), timeout=0.5)
                        self._reset_event.clear()
                    except asyncio.TimeoutError:
                        pass
                    continue

                # count inactivity
                while not self._stopped and self.enabled and self.timeout_s > 0 and not self.is_paused():
                    try:
                        await asyncio.wait_for(self._reset_event.wait(), timeout=self.timeout_s)
                        self._reset_event.clear()
                    except asyncio.TimeoutError:
                        # if in the meantime it paused or was disabled don't timeout
                        if self.is_paused() or (not self.enabled):
                            break
                        await self._call_on_timeout()
                        break
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("[%s] crash inside timer", self.name)


class _InactivityTimerPaused:
    """
    Single context manager (sync + async) for inactivityTimerPaused singleton
    usage :
        with inactivityTimerPaused:
            ...
        async with inactivityTimerPaused:
            ...
    """
    def __init__(self):
        self._depth = 0  # nesting-safe

    def __enter__(self):
        global inactivityTimer
        self._depth += 1
        if self._depth == 1:
            inactivityTimer.pause()
        return self

    def __exit__(self, exc_type, exc, tb):
        global inactivityTimer
        if self._depth > 0:
            self._depth -= 1
        if self._depth == 0:
            inactivityTimer.resume()
            inactivityTimer.reset()
        return False

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, exc_type, exc, tb):
        return self.__exit__(exc_type, exc, tb)


# ==========================================================
# SINGLETONS
# ==========================================================

# Timer singleton (di default spento)
inactivityTimer = _InactivityTimer(timeout_s=0.0, enabled=False, name="motors-inactivity")

# Context manager singleton (senza parentesi)
inactivityTimerPaused = _InactivityTimerPaused()
