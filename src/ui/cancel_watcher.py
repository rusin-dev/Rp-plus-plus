from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any

from ..core.event_bus import Event, EventBus, EventTypes

ESC = "\x1b"
DOUBLE_ESC_WINDOW = 0.8
POLL_INTERVAL = 0.05


class EscCancelWatcher(threading.Thread):
    """后台监听连按两次 ESC，触发 CANCEL 事件以中断当前回答。

    仅在回答进行期间由 UI 启动；空闲时停止，避免与 prompt_toolkit 抢键盘。
    """

    def __init__(
        self,
        bus: EventBus,
        stdin=None,
        window: float = DOUBLE_ESC_WINDOW,
        poll: float = POLL_INTERVAL,
    ) -> None:
        super().__init__(daemon=True, name="esc-cancel-watcher")
        self._bus = bus
        self._stdin = stdin if stdin is not None else sys.stdin
        self._window = window
        self._poll = poll
        self._stop_evt = threading.Event()
        self._last_esc_at: float | None = None
        self._fd: int | None = None
        self._old_term: Any | None = None
        self._termios: Any | None = None

    def stop(self) -> None:
        self._stop_evt.set()

    def handle_key(self, key: str) -> None:
        """处理一次按键：窗口期内连按两次 ESC 触发中断。"""
        if key != ESC:
            self._last_esc_at = None
            return
        now = time.monotonic()
        if self._last_esc_at is not None and now - self._last_esc_at <= self._window:
            self._last_esc_at = None
            self._bus.publish(Event(EventTypes.CANCEL))
        else:
            self._last_esc_at = now

    def run(self) -> None:
        self._enter()
        try:
            while not self._stop_evt.is_set():
                key = self._read_key()
                if key:
                    self.handle_key(key)
        finally:
            self._leave()

    # ---------- 底层按键读取 ----------

    def _read_key(self) -> str | None:
        if not self._stdin.isatty():
            time.sleep(self._poll)
            return None
        if sys.platform == "win32":
            return self._read_key_windows()
        return self._read_key_posix()

    def _read_key_windows(self) -> str | None:
        import msvcrt

        deadline = time.monotonic() + self._poll
        while time.monotonic() < deadline:
            if msvcrt.kbhit():
                return msvcrt.getwch()
            time.sleep(0.01)
        return None

    def _read_key_posix(self) -> str | None:
        import select

        fd = self._stdin.fileno()
        readable, _, _ = select.select([fd], [], [], self._poll)
        if not readable:
            return None
        data = os.read(fd, 1)
        return data.decode("utf-8", errors="ignore") or None

    def _enter(self) -> None:
        if sys.platform == "win32" or not self._stdin.isatty():
            return
        try:
            import termios
            import tty

            fd = self._stdin.fileno()
            self._old_term = termios.tcgetattr(fd)
            self._fd = fd
            self._termios = termios
            tty.setcbreak(fd)
        except Exception:
            self._old_term = None
            self._fd = None
            self._termios = None

    def _leave(self) -> None:
        if self._old_term is not None and self._fd is not None and self._termios is not None:
            try:
                self._termios.tcsetattr(self._fd, self._termios.TCSADRAIN, self._old_term)
            except Exception:
                pass
            self._old_term = None
            self._fd = None
            self._termios = None
