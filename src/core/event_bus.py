from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

EventType = str


@dataclass(slots=True)
class Event:
    """线程间传递的消息载体。"""

    type: EventType
    data: Any = None


class EventTypes:
    """预定义事件类型。"""

    USER_INPUT = "user_input"
    USER_ANSWER = "user_answer"
    TOKEN = "token"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    FILE_WRITTEN = "file_written"
    FILE_DIFF = "file_diff"
    CANCEL = "cancel"
    ASK_QUESTION = "ask_question"
    ASSISTANT_DONE = "assistant_done"
    ERROR = "error"
    SHUTDOWN = "shutdown"
    SUBAGENT_START = "subagent_start"
    SUBAGENT_TOKEN = "subagent_token"
    SUBAGENT_TOOL_CALL = "subagent_tool_call"
    SUBAGENT_TOOL_RESULT = "subagent_tool_result"
    SUBAGENT_DONE = "subagent_done"
    SUBAGENT_ERROR = "subagent_error"


class EventBus:
    """线程间通信总线：任意线程发布事件，消费线程按类型读取。"""

    def __init__(self) -> None:
        self._queue: queue.Queue[Event] = queue.Queue()
        self._closed = False
        self._lock = threading.Lock()

    def publish(self, event: Event) -> None:
        with self._lock:
            if self._closed:
                return
            self._queue.put(event)

    def get(self, timeout: float | None = None) -> Event | None:
        """取一个事件；超时返回 None。"""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def await_event(self, event_type: EventType, timeout: float | None = None) -> Event:
        """阻塞等待指定类型的事件（跳过其他事件）。"""
        deadline = _deadline(timeout)
        while True:
            remaining = _remaining(deadline)
            if remaining == 0:
                raise TimeoutError(f"等待事件 {event_type} 超时")
            event = self.get(timeout=remaining)
            if event is None:
                continue
            if event.type == event_type:
                return event

    def drain(self) -> list[Event]:
        """取走当前队列中所有事件。"""
        events: list[Event] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                return events

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._queue.put(Event(EventTypes.SHUTDOWN))


def _deadline(timeout: float | None) -> float | None:
    return None if timeout is None else time.monotonic() + timeout


def _remaining(deadline: float | None) -> float:
    if deadline is None:
        return 1.0
    remain = deadline - time.monotonic()
    return remain if remain > 0 else 0.0
