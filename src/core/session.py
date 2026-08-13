from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..config import Config

_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass
class Session:
    """一个可恢复的对话会话。"""

    session_id: str
    model: str
    system_prompt: str
    mode: str = ""
    messages: list[dict] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    @property
    def message_count(self) -> int:
        return sum(1 for m in self.messages if m["role"] in {"user", "assistant"})

    @property
    def summary(self) -> str:
        for message in self.messages:
            if message["role"] == "user":
                return " ".join(message["content"].split())[:30]
        return ""

    def touch(self) -> None:
        now = datetime.now().strftime(_TIME_FORMAT)
        if not self.created_at:
            self.created_at = now
        self.updated_at = now


class SessionStore:
    """会话的持久化：保存 / 加载 / 列表 / 删除（JSON 文件）。"""

    def __init__(self, config: type[Config]) -> None:
        self._dir = config.SESSION_DIR

    def save(self, session: Session) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        session.touch()
        payload = {
            "session_id": session.session_id,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "model": session.model,
            "system_prompt": session.system_prompt,
            "mode": session.mode,
            "messages": session.messages,
        }
        (self._dir / f"{session.session_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load(self, session_id: str) -> Session | None:
        path = self._path(session_id)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return Session(
            session_id=data["session_id"],
            model=data["model"],
            system_prompt=data["system_prompt"],
            mode=data.get("mode", ""),
            messages=data.get("messages", []),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )

    def list(self) -> list[Session]:
        if not self._dir.is_dir():
            return []
        sessions: list[Session] = []
        for path in sorted(self._dir.glob("*.json"), reverse=True):
            session = self.load(path.stem)
            if session is not None:
                sessions.append(session)
        return sessions

    def delete(self, session_id: str) -> None:
        path = self._path(session_id)
        if path.is_file():
            path.unlink()

    def _path(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.json"
