"""In-process event bus. IPC connections subscribe by topic."""

from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger(__name__)

# Topic = the event name's first segment ("run.updated" → "run").
TOPICS = ("system", "project", "cortex", "agent", "plan", "run")


class EventBus:
    def __init__(self) -> None:
        self._subs: list[Callable[[str, dict], None]] = []

    def subscribe(self, fn: Callable[[str, dict], None]) -> Callable[[], None]:
        self._subs.append(fn)
        return lambda: self._subs.remove(fn) if fn in self._subs else None

    def emit(self, name: str, data: dict) -> None:
        for fn in list(self._subs):
            try:
                fn(name, data)
            except Exception:  # a broken subscriber must not break emitters
                log.exception("event subscriber failed for %s", name)
