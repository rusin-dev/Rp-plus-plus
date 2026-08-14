from src.core.event_bus import EventBus, EventTypes
from src.ui.cancel_watcher import ESC, EscCancelWatcher


def _watcher(bus: EventBus) -> EscCancelWatcher:
    return EscCancelWatcher(bus)


def test_single_esc_does_not_cancel(monkeypatch):
    bus = EventBus()
    watcher = _watcher(bus)
    watcher.handle_key(ESC)
    assert bus.drain() == []


def test_double_esc_within_window_cancels(monkeypatch):
    bus = EventBus()
    watcher = _watcher(bus)
    ticks = iter([100.0, 100.2])
    monkeypatch.setattr("src.ui.cancel_watcher.time.monotonic", lambda: next(ticks))
    watcher.handle_key(ESC)
    watcher.handle_key(ESC)
    events = bus.drain()
    assert [e.type for e in events] == [EventTypes.CANCEL]


def test_double_esc_outside_window_ignored(monkeypatch):
    bus = EventBus()
    watcher = _watcher(bus)
    ticks = iter([100.0, 101.5])
    monkeypatch.setattr("src.ui.cancel_watcher.time.monotonic", lambda: next(ticks))
    watcher.handle_key(ESC)
    watcher.handle_key(ESC)
    assert bus.drain() == []


def test_non_esc_resets_previous_esc(monkeypatch):
    bus = EventBus()
    watcher = _watcher(bus)
    ticks = iter([100.0, 100.1, 100.2])
    monkeypatch.setattr("src.ui.cancel_watcher.time.monotonic", lambda: next(ticks))
    watcher.handle_key(ESC)
    watcher.handle_key("a")
    watcher.handle_key(ESC)
    assert bus.drain() == []


def test_triple_esc_cancels_once(monkeypatch):
    bus = EventBus()
    watcher = _watcher(bus)
    ticks = iter([100.0, 100.1, 100.2])
    monkeypatch.setattr("src.ui.cancel_watcher.time.monotonic", lambda: next(ticks))
    watcher.handle_key(ESC)
    watcher.handle_key(ESC)
    watcher.handle_key(ESC)
    events = bus.drain()
    assert [e.type for e in events] == [EventTypes.CANCEL]
