"""In-memory registry bridging a running pipeline to its SSE stream.

Each active run gets an asyncio.Queue. The pipeline's emit callback puts
ProgressEvents on the queue; the SSE endpoint awaits and forwards them to
the browser. A sentinel (None) signals the stream to close.

This is intentionally in-memory only — it holds live streaming state, not
durable data. Completed runs are persisted separately via RunStore.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import AsyncIterator, Optional

from .schemas import ProgressEvent

_SENTINEL = None


class RunManager:
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue] = {}

    def new_run_id(self) -> str:
        return f"run_{uuid.uuid4().hex[:12]}"

    def register(self, run_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._queues[run_id] = q
        return q

    async def emit(self, event: ProgressEvent) -> None:
        q = self._queues.get(event.run_id)
        if q is not None:
            await q.put(event)

    async def close(self, run_id: str) -> None:
        q = self._queues.get(run_id)
        if q is not None:
            await q.put(_SENTINEL)

    async def stream(self, run_id: str) -> AsyncIterator[ProgressEvent]:
        q = self._queues.get(run_id)
        if q is None:
            return
        try:
            while True:
                item = await q.get()
                if item is _SENTINEL:
                    break
                yield item
        finally:
            self._queues.pop(run_id, None)


run_manager = RunManager()
