"""Progress sinks and thread-safe progress reporting."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from upset_profiler.logging_config import get_logger
from upset_profiler.models.progress import ProgressEvent, ProgressStage, ProgressStatus


class ProgressSink(Protocol):
    """Destination for ordered progress events."""

    def emit(self, event: ProgressEvent) -> None:
        """Deliver one progress event."""
        ...


class NullProgressSink:
    """Sink that discards events."""

    def emit(self, event: ProgressEvent) -> None:
        """Discard the event."""


class LoggingProgressSink:
    """Sink that writes progress events to the application logger."""

    def __init__(self) -> None:
        self._logger = get_logger("upset_profiler.progress")

    def emit(self, event: ProgressEvent) -> None:
        """Log one event with its run id attached."""
        self._logger.info(
            "%s %s [%s] %s",
            event.stage.value,
            event.status.value,
            event.agent,
            event.message,
            extra={"run_id": str(event.run_id)},
        )


class CallbackProgressSink:
    """Sink that forwards events to a callback."""

    def __init__(self, callback: Callable[[ProgressEvent], None]) -> None:
        self._callback = callback

    def emit(self, event: ProgressEvent) -> None:
        """Invoke the callback with the event."""
        self._callback(event)


class QueueProgressSink:
    """Thread-safe FIFO queue, suitable for a Streamlit polling loop."""

    def __init__(self) -> None:
        self._queue: queue.Queue[ProgressEvent] = queue.Queue()

    def emit(self, event: ProgressEvent) -> None:
        """Put an event onto the queue."""
        self._queue.put(event)

    def get(self, timeout: float | None = None) -> ProgressEvent:
        """Block until an event is available and return it."""
        return self._queue.get(timeout=timeout)

    def get_nowait(self) -> ProgressEvent:
        """Return the next event without blocking."""
        return self._queue.get_nowait()

    def drain(self) -> list[ProgressEvent]:
        """Return all currently queued events without blocking."""
        events: list[ProgressEvent] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                return events

    def empty(self) -> bool:
        """Return whether the queue is empty."""
        return self._queue.empty()


class InMemoryProgressSink:
    """Thread-safe sink that retains events for tests and diagnostics."""

    def __init__(self) -> None:
        self._events: list[ProgressEvent] = []
        self._lock = threading.Lock()

    def emit(self, event: ProgressEvent) -> None:
        """Append an event to the stored list."""
        with self._lock:
            self._events.append(event)

    @property
    def events(self) -> tuple[ProgressEvent, ...]:
        """Return an immutable snapshot of emitted events."""
        with self._lock:
            return tuple(self._events)


class ProgressReporter:
    """Assign ordered indices to events and forward them to a sink.

    A reporter is safe to share across threads. Event indices are assigned and
    delivered while holding a lock so sinks observe a total order.
    """

    def __init__(
        self,
        *,
        run_id: UUID,
        sink: ProgressSink,
        stage: ProgressStage,
        agent: str,
        total: int | None = None,
    ) -> None:
        self._run_id = run_id
        self._sink = sink
        self._stage = stage
        self._agent = agent
        self._total = total
        self._completed = 0
        self._event_index = 0
        self._lock = threading.Lock()

    def emit(
        self,
        message: str,
        *,
        status: ProgressStatus = ProgressStatus.RUNNING,
        stage: ProgressStage | None = None,
        agent: str | None = None,
        game_id: str | None = None,
        completed: int | None = None,
        total: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ProgressEvent:
        """Emit an event using the reporter's defaults."""
        with self._lock:
            return self._emit_locked(
                message,
                status=status,
                stage=stage,
                agent=agent,
                game_id=game_id,
                completed=completed,
                total=total,
                metadata=metadata,
            )

    def advance(
        self,
        message: str,
        *,
        step: int = 1,
        stage: ProgressStage | None = None,
        agent: str | None = None,
        game_id: str | None = None,
        total: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ProgressEvent:
        """Increment the completed count and emit a running event."""
        if step < 0:
            raise ValueError("step must be non-negative")
        with self._lock:
            return self._emit_locked(
                message,
                status=ProgressStatus.RUNNING,
                stage=stage,
                agent=agent,
                game_id=game_id,
                completed=self._completed + step,
                total=total,
                metadata=metadata,
            )

    def started(
        self,
        message: str,
        *,
        total: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ProgressEvent:
        """Emit a started event at zero completed work."""
        return self.emit(
            message,
            status=ProgressStatus.STARTED,
            completed=0,
            total=total,
            metadata=metadata,
        )

    def completed(
        self,
        message: str,
        *,
        metadata: dict[str, object] | None = None,
    ) -> ProgressEvent:
        """Emit a completed event at the reporter's total."""
        with self._lock:
            completed = self._total if self._total is not None else self._completed
            return self._emit_locked(
                message,
                status=ProgressStatus.COMPLETED,
                stage=None,
                agent=None,
                game_id=None,
                completed=completed,
                total=None,
                metadata=metadata,
            )

    def failed(
        self,
        message: str,
        *,
        error: BaseException | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ProgressEvent:
        """Emit a failed event with optional exception metadata."""
        merged = dict(metadata or {})
        if error is not None:
            merged.setdefault("error_type", type(error).__name__)
            merged.setdefault("error", str(error))
        return self.emit(message, status=ProgressStatus.FAILED, metadata=merged)

    def skipped(
        self,
        message: str,
        *,
        metadata: dict[str, object] | None = None,
    ) -> ProgressEvent:
        """Emit a skipped event."""
        return self.emit(message, status=ProgressStatus.SKIPPED, metadata=metadata)

    def _emit_locked(
        self,
        message: str,
        *,
        status: ProgressStatus,
        stage: ProgressStage | None,
        agent: str | None,
        game_id: str | None,
        completed: int | None,
        total: int | None,
        metadata: dict[str, object] | None,
    ) -> ProgressEvent:
        total_value = self._total if total is None else total
        completed_value = self._completed if completed is None else completed
        if total_value is not None:
            completed_value = min(completed_value, total_value)
        self._completed = completed_value
        event = ProgressEvent(
            run_id=self._run_id,
            event_index=self._event_index,
            stage=stage or self._stage,
            status=status,
            agent=agent or self._agent,
            message=message,
            game_id=game_id,
            completed=completed_value,
            total=total_value,
            metadata=dict(metadata or {}),
        )
        self._event_index += 1
        self._sink.emit(event)
        return event
