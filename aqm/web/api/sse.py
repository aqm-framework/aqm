"""Server-Sent Events infrastructure for real-time pipeline progress."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

# In-memory event bus: task_id -> list of asyncio.Queue
_subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)


def broadcast_event(task_id: str, event_type: str, data: dict) -> None:
    """Push an event to all subscribers of a task.

    Thread-safe: can be called from background threads.
    """
    msg = {"event": event_type, "data": json.dumps(data)}
    queues = _subscribers.get(task_id, [])
    for q in queues:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            logger.debug("SSE queue full for task %s, dropping event", task_id)


async def subscribe(task_id: str) -> AsyncGenerator[str, None]:
    """SSE generator for a specific task. Yields formatted SSE strings."""
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers[task_id].append(q)
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30)
                yield f"event: {event['event']}\ndata: {event['data']}\n\n"
                # If task is done, close
                if event["event"] in ("task_complete", "task_failed", "task_cancelled"):
                    return
            except asyncio.TimeoutError:
                # Send keepalive to prevent timeout
                yield ": keepalive\n\n"
    finally:
        _subscribers[task_id].remove(q)
        if not _subscribers[task_id]:
            del _subscribers[task_id]
