from __future__ import annotations

import json

_TOOL_CALL_VALUE_LEN = 60

_POSITIONAL_FIELDS: dict[str, tuple[str, ...]] = {
    "read": ("file_path",),
    "write": ("file_path",),
    "edit": ("file_path",),
    "grep": ("pattern", "path"),
    "shell": ("command",),
    "ask": ("question",),
    "web_search": ("query",),
    "web_fetch": ("url",),
    "delegate": ("agent", "task"),
}

_OMIT_FIELDS = {"content", "old_string", "new_string", "context"}


def _short(value: object) -> str:
    """把参数值压缩为单行、可读的展示文本。"""
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    text = " ".join(text.split())
    if len(text) > _TOOL_CALL_VALUE_LEN:
        text = text[:_TOOL_CALL_VALUE_LEN] + "…"
    if not text or any(c.isspace() for c in text):
        text = f'"{text}"'
    return text


def format_tool_call(name: str, arguments: str) -> str:
    """把工具调用的 JSON 参数压缩为可读的 `name(参数)` 展示文本。

    - 常用参数按位置展示（如 `read(src/foo.py)`）
    - 大段文本（content/old_string/new_string 等）不内联，留给下方代码框展示
    - 无法解析的 JSON 原样回退
    """
    try:
        params = json.loads(arguments) if arguments else {}
    except (json.JSONDecodeError, TypeError):
        return f"{name}({arguments})"
    if not isinstance(params, dict) or not params:
        return f"{name}()"

    shown: list[str] = []
    positional = _POSITIONAL_FIELDS.get(name, ())
    for key in positional:
        if key in params:
            shown.append(_short(params[key]))
    for key, value in params.items():
        if key in positional or key in _OMIT_FIELDS:
            continue
        shown.append(f"{key}={_short(value)}")
    return f"{name}({', '.join(shown)})"


def extract_read_path(arguments: str) -> str | None:
    """从 read 工具参数中提取 file_path；参数非法或缺失时返回 None。"""
    try:
        params = json.loads(arguments) if arguments else {}
    except (json.JSONDecodeError, TypeError):
        return None
    path = params.get("file_path") if isinstance(params, dict) else None
    return path if isinstance(path, str) else None


def format_grep_status(result: str) -> str:
    """把 grep 工具结果压缩为成功/失败状态行。

    成功返回匹配数量（不变色），失败返回错误信息（由调用方标红）。
    """
    if result.startswith("error:"):
        return f"✖ {result[len('error:'):].strip()}"
    if result == "无匹配":
        return "无匹配"
    if result.startswith("匹配结果过多"):
        return "匹配结果过多"
    count = sum(1 for line in result.splitlines() if line.strip())
    return f"匹配 {count} 处"
