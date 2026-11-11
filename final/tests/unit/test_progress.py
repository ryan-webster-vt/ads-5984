"""Unit tests for progress events, sinks, and thread-safe reporting."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from upset_profiler.models.progress import ProgressStage, ProgressStatus
from upset_profiler.orchestration import (
    InMemoryProgressSink,
    NullProgressSink,
    ProgressReporter,
    ProgressSink,
    QueueProgressSink,
)


def _reporter(sink: ProgressSink, *, total: int | None = 2) -> ProgressReporter:
    return ProgressReporter(
        run_id=uuid4(),
        sink=sink,
        stage=ProgressStage.MONITOR,
        agent="monitor-worker-1",
        total=total,
    )


@pytest.mark.unit
def test_events_are_ordered_and_counts_advance() -> None:
    sink = InMemoryProgressSink()
    reporter = _reporter(sink)

    reporter.started("starting")
    reporter.advance("first game")
    reporter.advance("second game")
    reporter.completed("done")

    events = sink.events
    assert [event.event_index for event in events] == [0, 1, 2, 3]
    assert events[0].status is ProgressStatus.STARTED
    assert events[-1].status is ProgressStatus.COMPLETED
    assert events[-1].completed == 2
    assert events[-1].fraction == 1.0


@pytest.mark.unit
def test_advance_clamps_to_total() -> None:
    sink = InMemoryProgressSink()
    reporter = _reporter(sink, total=1)

    reporter.advance("one", step=5)

    assert sink.events[-1].completed == 1


@pytest.mark.unit
def test_concurrent_reporters_produce_contiguous_indices() -> None:
    sink = InMemoryProgressSink()
    reporter = _reporter(sink, total=100)

    def advance(index: int) -> None:
        reporter.advance(f"game {index}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(advance, range(100)))

    events = sink.events
    assert len(events) == 100
    assert [event.event_index for event in events] == list(range(100))
    assert [event.completed for event in events] == list(range(1, 101))


@pytest.mark.unit
def test_queue_sink_preserves_fifo_order() -> None:
    sink = QueueProgressSink()
    reporter = _reporter(sink)

    reporter.started("start")
    reporter.advance("one")
    assert not sink.empty()

    drained = sink.drain()
    assert [event.message for event in drained] == ["start", "one"]
    assert sink.empty()


@pytest.mark.unit
def test_failed_event_carries_error_metadata() -> None:
    sink = InMemoryProgressSink()
    reporter = _reporter(sink)

    reporter.failed("boom", error=RuntimeError("exploded"))

    event = sink.events[-1]
    assert event.status is ProgressStatus.FAILED
    assert event.metadata["error_type"] == "RuntimeError"
    assert event.metadata["error"] == "exploded"


@pytest.mark.unit
def test_reporter_defaults_can_be_overridden() -> None:
    sink = InMemoryProgressSink()
    reporter = _reporter(sink)

    event = reporter.emit(
        "custom",
        stage=ProgressStage.SCORE,
        agent="upset-scorer",
        game_id="401000001",
        status=ProgressStatus.RUNNING,
    )

    assert event.stage is ProgressStage.SCORE
    assert event.agent == "upset-scorer"
    assert event.game_id == "401000001"


@pytest.mark.unit
def test_null_sink_discards_events() -> None:
    reporter = _reporter(NullProgressSink())
    event = reporter.started("start")
    assert event.message == "start"


@pytest.mark.unit
def test_negative_advance_step_is_rejected() -> None:
    reporter = _reporter(InMemoryProgressSink())
    with pytest.raises(ValueError):
        reporter.advance("bad", step=-1)
