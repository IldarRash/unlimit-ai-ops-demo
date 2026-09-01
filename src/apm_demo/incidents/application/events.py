from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from apm_demo.incidents.domain import IncidentRecord


class IncidentEventBus:
    """Best-effort local SSE fan-out; incident truth remains in SQLite."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[IncidentRecord]] = set()
        self._lock = asyncio.Lock()

    async def publish(self, incident: IncidentRecord) -> None:
        async with self._lock:
            subscribers = tuple(self._subscribers)
        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(incident.model_copy(deep=True))

    async def subscribe(self) -> AsyncIterator[IncidentRecord]:
        queue: asyncio.Queue[IncidentRecord] = asyncio.Queue(maxsize=50)
        async with self._lock:
            self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            async with self._lock:
                self._subscribers.discard(queue)
