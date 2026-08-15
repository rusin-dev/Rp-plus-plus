from __future__ import annotations

import difflib
import html
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from openai.types.chat import (
    ChatCompletionFunctionToolParam,
    ChatCompletionToolUnionParam,
)

from ..config import Config
from ..core.event_bus import Event, EventBus, EventTypes

_MAX_READ_BYTES = 512 * 1024  # 512 KB
_MAX_READ_LINES_DEFAULT = 200
_MAX_GREP_RESULTS = 50
_SHELL_TIMEOUT_SECONDS = 120
_WEB_TIMEOUT_SECONDS = 20
_WEB_FETCH_MAX_BYTES = 1_000_000  # 1 MB
_WEB_FETCH_DEFAULT_CHARS = 8000
_WEB_SEARCH_MAX_RESULTS = 10
_WEB_USER_AGENT = "Mozilla/5.0 (compatible; rp-co-pilot/0.1; +https://github.com/)"
_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_BING_SEARCH_URL = "https://www.bing.com/search"
_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "log",
    "dist",
    "build",
    ".ruff_cache",
    ".pytest_cache",
    ".idea",
    ".vscode",
}


# ---------- 基础工具 ----------


def _ask(bus: EventBus, question: str) -> str:
    """通过事件总线向用户提问并等待回答。"""
    bus.publish(Event(EventTypes.ASK_QUESTION, question))
    reply = bus.await_event(EventTypes.USER_ANSWER)
    return str(reply.data)


def _run_shell(bus: EventBus, command: str) -> str:
    """执行 shell 命令并返回输出（注意：存在任意命令执行风险）。"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SHELL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"error: 命令执行超时（{_SHELL_TIMEOUT_SECONDS}s）"
    return f"exit code: {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def _read_file(
    bus: EventBus,
    file_path: str,
    offset: int = 1,
    limit: int | None = None,
    state: set[str] | None = None,
) -> str:
    """读取工作区内的文本文件（按行，可指定 offset/limit 分页）。"""
    try:
        target = _resolve_workspace_path(file_path)
    except ValueError as exc:
        return f"error: {exc}"
    if not target.is_file():
        return f"error: 文件不存在: {target}"
    if target.stat().st_size > _MAX_READ_BYTES:
        return f"error: 文件过大（>{_MAX_READ_BYTES} 字节）"
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, int(offset or 1))
    limit = limit if limit is not None else _MAX_READ_LINES_DEFAULT
    end = start - 1 + int(limit)
    selected = lines[start - 1 : end]
    body = "\n".join(selected)
    total = len(lines)
    suffix = (
        f"\n…（已截断：共 {total} 行，仅显示第 {start}~{start + len(selected) - 1} 行，"
        f"可继续用 offset={start + len(selected)} 读取下一页）"
        if end < total
        else ""
    )
    if state is not None:
        state.add(str(target))
    return body + suffix


def _write_file(
    bus: EventBus,
    file_path: str,
    content: str,
    offset: int | None = None,
    limit: int | None = None,
    state: set[str] | None = None,
) -> str:
    """写入工作区内的文件，自动创建父目录。

    已存在的文件必须先 read 才能写；offset/limit 用于按行区间替换，
    仅替换 [offset, offset+limit) 区间内的行，其余内容保留。
    """
    try:
        target = _resolve_workspace_path(file_path)
    except ValueError as exc:
        return f"error: {exc}"
    if state is None:
        state = set()
    if offset is not None:
        if not target.is_file():
            return (
                f"error: 区间替换（offset/limit）只适用于已存在的文件，"
                f"新建文件请直接 write 完整内容: {target}"
            )
        if str(target) not in state:
            return f"error: 写入 {target.name} 前必须先 read 该文件"
        original = target.read_text(encoding="utf-8", errors="replace")
        lines = original.splitlines()
        had_trailing_newline = original.endswith("\n")
        start = max(1, int(offset))
        count = int(limit) if limit is not None else len(lines) - start + 1
        if count < 0:
            return "error: limit 不能为负数"
        end = start - 1 + count
        replacement = content.splitlines()
        new_lines = lines[: start - 1] + replacement + lines[end:]
        new_text = "\n".join(new_lines)
        if had_trailing_newline:
            new_text += "\n"
        target.write_text(new_text, encoding="utf-8")
        if state is not None:
            state.add(str(target))
        bus.publish(Event(EventTypes.FILE_WRITTEN, {"path": str(target), "content": new_text}))
        return (
            f"已写入 {target}（区间替换第 {start}~{end} 行，"
            f"共 {len(new_text.encode('utf-8'))} 字节）"
        )
    if target.is_file() and str(target) not in state:
        return f"error: 写入 {target.name} 前必须先 read 该文件"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    if state is not None:
        state.add(str(target))
    bus.publish(Event(EventTypes.FILE_WRITTEN, {"path": str(target), "content": content}))
    return f"已写入 {target}（{len(content.encode('utf-8'))} 字节）"


def _edit_file(
    bus: EventBus,
    file_path: str,
    old_string: str,
    new_string: str,
    state: set[str] | None = None,
) -> str:
    """在已存在的文件中精确替换文本（old_string → new_string）。

    必须先 read 才能 edit；只替换第一处匹配，修改结果以 git 风格 diff 展示。
    """
    try:
        target = _resolve_workspace_path(file_path)
    except ValueError as exc:
        return f"error: {exc}"
    if not target.is_file():
        return f"error: 文件不存在: {target}"
    if state is not None and str(target) not in state:
        return f"error: 编辑 {target.name} 前必须先 read 该文件"
    original = target.read_text(encoding="utf-8", errors="replace")
    if not old_string:
        return "error: old_string 不能为空"
    if old_string not in original:
        return f"error: 在 {target.name} 中未找到要替换的文本"
    new_text = original.replace(old_string, new_string, 1)
    target.write_text(new_text, encoding="utf-8")
    if state is not None:
        state.add(str(target))
    diff = _make_unified_diff(str(target), original, new_text)
    if diff:
        bus.publish(Event(EventTypes.FILE_DIFF, {"path": str(target), "diff": diff}))
    return f"已编辑 {target}（替换 1 处，共 {len(new_text.encode('utf-8'))} 字节）"


def _make_unified_diff(path: str, old_text: str, new_text: str) -> str:
    """生成 git 风格 unified diff 文本（--- / +++ / @@ / - / +）。"""
    diff = difflib.unified_diff(
        old_text.splitlines(),
        new_text.splitlines(),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    )
    return "\n".join(diff)


def _grep(
    bus: EventBus,
    pattern: str,
    path: str = ".",
    include: str | None = None,
) -> str:
    """在目录内递归搜索文本内容，返回 文件:行号: 行内容。"""
    try:
        base = _resolve_workspace_path(path)
        regex = re.compile(pattern)
    except ValueError as exc:
        return f"error: {exc}"
    except re.error as exc:
        return f"error: 非法正则表达式: {exc}"
    if not base.is_dir():
        return f"error: {path} 不是目录"

    results: list[str] = []
    for p in base.rglob("*"):
        if p.is_dir() or _skipped(p, base):
            continue
        if include and not p.match(include):
            continue
        if p.stat().st_size > _MAX_READ_BYTES:
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, 1):
            if regex.search(line):
                results.append(f"{p}:{lineno}: {line.strip()[:200]}")
                if len(results) >= _MAX_GREP_RESULTS:
                    return f"匹配结果过多，仅显示前 {_MAX_GREP_RESULTS} 条:\n" + "\n".join(results)
    return "\n".join(results) if results else "无匹配"


# ---------- 网络工具 ----------


def _http_get(url: str, max_bytes: int | None = None, timeout: float = _WEB_TIMEOUT_SECONDS) -> str:
    """发起 GET 请求并返回解码后的文本。"""
    request = urllib.request.Request(url, headers={"User-Agent": _WEB_USER_AGENT})
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason or exc)) from exc
    except (OSError, ValueError) as exc:
        raise RuntimeError(str(exc)) from exc
    try:
        with response:
            data = response.read(max_bytes) if max_bytes else response.read()
            charset = response.headers.get_content_charset() or "utf-8"
    except OSError as exc:
        raise RuntimeError(f"读取响应失败: {exc}") from exc
    return data.decode(charset, errors="replace")


def _web_fetch(bus: EventBus, url: str, max_chars: int = 8000) -> str:
    """抓取网页内容并转为纯文本。"""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return f"error: 仅支持 http/https 协议: {url}"
    if not parsed.netloc:
        return f"error: 非法 URL: {url}"
    try:
        html_text = _http_get(url, _WEB_FETCH_MAX_BYTES)
    except RuntimeError as exc:
        return f"error: 抓取失败: {exc}"
    text = _html_to_text(html_text)
    if not text.strip():
        return "页面无文本内容"
    limit = max(500, int(max_chars or _WEB_FETCH_DEFAULT_CHARS))
    if len(text) > limit:
        text = text[:limit] + f"…（已截断，全文共 {len(text)} 字符）"
    return text


def _delegate(bus: EventBus, agent: str, task: str, context: str | None = None) -> str:
    """把任务委派给指定子 Agent 执行，返回其最终结果。"""
    from .agents import AgentRegistry, SubAgentRunner

    registry = AgentRegistry()
    subagent = registry.get(agent)
    if subagent is None:
        available = "、".join(registry.names()) or "无"
        return f"error: 未知子 Agent: {agent}，可用：{available}"
    tools = ToolRegistry().filtered(set(subagent.tools))
    runner = SubAgentRunner(Config, bus, tools, subagent, task, context)
    return runner.run()


def _web_search(bus: EventBus, query: str, count: int = 5) -> str:
    """网页搜索：默认 Bing，可通过 SEARCH_BACKEND 切换（bing/ddg/auto）。"""
    backend = (Config.SEARCH_BACKEND or "bing").lower()
    if backend not in {"auto", "bing", "ddg"}:
        backend = "bing"
    try:
        if backend == "bing":
            return _search_bing(query, count)
        try:
            return _search_ddg(query, count)
        except RuntimeError:
            if backend == "ddg":
                raise
            return _search_bing(query, count)
    except RuntimeError as exc:
        return f"error: 搜索失败: {exc}"


def _search_ddg(query: str, count: int = 5) -> str:
    """通过 DuckDuckGo HTML 端点搜索。"""
    params = urllib.parse.urlencode({"q": query})
    html_text = _http_get(f"{_DDG_HTML_URL}?{params}", 2_000_000)
    return _format_results(_parse_ddg_results(html_text), count)


def _search_bing(query: str, count: int = 5) -> str:
    """通过 Bing 搜索结果页搜索。"""
    params = urllib.parse.urlencode({"q": query})
    html_text = _http_get(f"{_BING_SEARCH_URL}?{params}", 2_000_000)
    return _format_results(_parse_bing_results(html_text), count)


def _format_results(results: list[dict[str, str]], count: int = 5) -> str:
    if not results:
        return "无搜索结果"
    limit = max(1, min(int(count or 5), _WEB_SEARCH_MAX_RESULTS))
    lines: list[str] = []
    for index, result in enumerate(results[:limit], 1):
        lines.append(f"{index}. {result['title']}\n   {result['url']}\n   {result['snippet']}")
    return "\n".join(lines)


def _parse_ddg_results(html_text: str) -> list[dict[str, str]]:
    """从 DuckDuckGo HTML 页面中提取搜索结果。"""
    results: list[dict[str, str]] = []
    blocks = re.split(r'<div[^>]*class="result[ "\t]', html_text)[1:]
    for block in blocks:
        href_match = re.search(r'class="result__a" href="([^"]+)"', block)
        title_match = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, re.S)
        if not (href_match and title_match):
            continue
        snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        results.append(
            {
                "title": _strip_html_tags(title_match.group(1)),
                "url": _decode_ddg_url(href_match.group(1)),
                "snippet": (_strip_html_tags(snippet_match.group(1)) if snippet_match else ""),
            }
        )
    return results


def _parse_bing_results(html_text: str) -> list[dict[str, str]]:
    """从 Bing 搜索结果页（<li class=\"b_algo\">）中提取搜索结果。"""
    results: list[dict[str, str]] = []
    blocks = re.split(r'<li[^>]*class="b_algo"', html_text)[1:]
    for block in blocks:
        anchor = re.search(r'<h2[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not anchor:
            continue
        snippet_match = re.search(r'class="b_caption"[^>]*>.*?<p[^>]*>(.*?)</p>', block, re.S)
        results.append(
            {
                "title": _strip_html_tags(anchor.group(2)),
                "url": anchor.group(1),
                "snippet": (_strip_html_tags(snippet_match.group(1)) if snippet_match else ""),
            }
        )
    return results


def _decode_ddg_url(href: str) -> str:
    """解码 DuckDuckGo 的跳转链接，解析真实目标 URL。"""
    href = href.strip()
    uddg = re.search(r"uddg=([^&]+)", href)
    if uddg:
        href = urllib.parse.unquote(uddg.group(1))
    if href.startswith("//"):
        href = "https:" + href
    return href


def _html_to_text(html_text: str) -> str:
    """粗略地把 HTML 转为纯文本。"""
    text = re.sub(r"<script\b[^>]*>.*?</script\b[^>]*>", " ", html_text, flags=re.S | re.I)
    text = re.sub(r"<style\b[^>]*>.*?</style\b[^>]*>", " ", text, flags=re.S | re.I)
    return _strip_html_tags(text)


def _strip_html_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


# ---------- 工具 schema ----------

ASK_TOOL_SCHEMA: ChatCompletionFunctionToolParam = {
    "type": "function",
    "function": {
        "name": "ask",
        "description": "向用户提问，用于澄清需求、请求确认或收集缺失信息",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "要问用户的问题",
                }
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    },
}

SHELL_TOOL_SCHEMA: ChatCompletionFunctionToolParam = {
    "type": "function",
    "function": {
        "name": "shell",
        "description": "在用户本机执行 shell 命令并返回输出，用于验证、构建、运行等操作性任务",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令",
                }
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
}

READ_TOOL_SCHEMA: ChatCompletionFunctionToolParam = {
    "type": "function",
    "function": {
        "name": "read",
        "description": (
            "读取工作区内的文本文件（最大 512KB），默认按行分页（每页 200 行）。"
            "通过 offset/limit 可定位到指定行区间读取。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "文件路径，绝对路径或相对工作区根目录的路径",
                },
                "offset": {
                    "type": "integer",
                    "description": "起始行号（从 1 开始，默认 1）",
                },
                "limit": {
                    "type": "integer",
                    "description": "要读取的行数（默认 200）",
                },
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
    },
}

WRITE_TOOL_SCHEMA: ChatCompletionFunctionToolParam = {
    "type": "function",
    "function": {
        "name": "write",
        "description": (
            "写入或覆盖工作区内的文件，自动创建父目录。"
            "已存在的文件必须先 read 过才能写。"
            "提供 offset/limit 时按行区间替换：offset 为起始行（从 1 开始），"
            "limit 为要替换的行数，其余内容保留；不传 offset 则整文件覆盖。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "文件路径，绝对路径或相对工作区根目录的路径",
                },
                "content": {
                    "type": "string",
                    "description": "要写入的内容；区间替换时仅替换目标区间",
                },
                "offset": {
                    "type": "integer",
                    "description": "区间替换的起始行（从 1 开始，仅已存在文件可用）",
                },
                "limit": {
                    "type": "integer",
                    "description": "区间替换的行数（默认替换到文件末尾）",
                },
            },
            "required": ["file_path", "content"],
            "additionalProperties": False,
        },
    },
}

EDIT_TOOL_SCHEMA: ChatCompletionFunctionToolParam = {
    "type": "function",
    "function": {
        "name": "edit",
        "description": (
            "对已存在的文件做精确替换：把 old_string 替换成 new_string。"
            "必须先 read 过该文件；只替换第一处匹配。修改会以 git 风格 diff 展示。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "文件路径，绝对路径或相对工作区根目录的路径",
                },
                "old_string": {
                    "type": "string",
                    "description": "要替换的原始文本，必须与文件内容完全一致",
                },
                "new_string": {
                    "type": "string",
                    "description": "替换后的新文本",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
            "additionalProperties": False,
        },
    },
}

GREP_TOOL_SCHEMA: ChatCompletionFunctionToolParam = {
    "type": "function",
    "function": {
        "name": "grep",
        "description": "在目录内按正则表达式递归搜索文本内容，返回 文件:行号: 行内容",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "正则表达式",
                },
                "path": {
                    "type": "string",
                    "description": "搜索的起始目录（默认工作区根目录）",
                },
                "include": {
                    "type": "string",
                    "description": "文件名 glob 过滤，如 *.py",
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    },
}

WEB_SEARCH_TOOL_SCHEMA: ChatCompletionFunctionToolParam = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "搜索网页并返回 标题/链接/摘要（默认 Bing，可用 SEARCH_BACKEND 切换），用于获取最新信息或外部资料",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词",
                },
                "count": {
                    "type": "integer",
                    "description": "返回的结果数量（默认 5，最多 10）",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

WEB_FETCH_TOOL_SCHEMA: ChatCompletionFunctionToolParam = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": "抓取网页内容并转为纯文本（默认最多 8000 字符），用于阅读具体页面",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "网页地址（http/https）",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "返回的最大字符数（默认 8000）",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
}

DELEGATE_TOOL_SCHEMA: ChatCompletionFunctionToolParam = {
    "type": "function",
    "function": {
        "name": "delegate",
        "description": (
            "把需要领域专长的任务委派给子 Agent 执行并返回其最终结果。"
            "可用子 Agent：librarian（知识检索）、frontend_builder（前端实现）、"
            "backend_builder（后端实现）、ui_ux_designer（UI/UX 设计）、"
            "reviewer（代码评审）。简单任务不要委派。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "子 Agent 名称"},
                "task": {
                    "type": "string",
                    "description": "要委派的具体任务（清晰、自足）",
                },
                "context": {
                    "type": "string",
                    "description": "可选：对任务必要的上下文/资料",
                },
            },
            "required": ["agent", "task"],
            "additionalProperties": False,
        },
    },
}

_TOOL_HANDLERS: dict[str, Callable[..., str]] = {
    "ask": _ask,
    "shell": _run_shell,
    "read": _read_file,
    "write": _write_file,
    "edit": _edit_file,
    "grep": _grep,
    "web_search": _web_search,
    "web_fetch": _web_fetch,
    "delegate": _delegate,
}


# ---------- 注册表 ----------


class ToolRegistry:
    """工具 schema 与执行器的注册表。"""

    def __init__(self) -> None:
        self._schemas: list[ChatCompletionToolUnionParam] = [
            ASK_TOOL_SCHEMA,
            SHELL_TOOL_SCHEMA,
            READ_TOOL_SCHEMA,
            WRITE_TOOL_SCHEMA,
            EDIT_TOOL_SCHEMA,
            GREP_TOOL_SCHEMA,
            WEB_SEARCH_TOOL_SCHEMA,
            WEB_FETCH_TOOL_SCHEMA,
            DELEGATE_TOOL_SCHEMA,
        ]
        self._handlers: dict[str, Callable[..., str]] = dict(_TOOL_HANDLERS)
        self._read_files: set[str] = set()

    @property
    def schemas(self) -> list[ChatCompletionToolUnionParam]:
        return list(self._schemas)

    def schemas_for_mode(self, mode: str) -> list[ChatCompletionToolUnionParam]:
        """返回指定模式下可见的工具 schema（plan 模式排除写/执行类工具）。"""
        blocked = Config.mode_tool_exclusions(mode)
        if not blocked:
            return list(self._schemas)
        visible: list[ChatCompletionToolUnionParam] = []
        for schema in self._schemas:
            function = schema.get("function")
            if isinstance(function, dict) and function.get("name") in blocked:
                continue
            visible.append(schema)
        return visible

    def filtered(self, names: set[str]) -> ToolRegistry:
        """返回仅包含指定工具名的注册表副本（不修改自身）。"""
        new = ToolRegistry.__new__(ToolRegistry)
        new._schemas = [schema for schema in self._schemas if _schema_name(schema) in names]
        new._handlers = {name: handler for name, handler in self._handlers.items() if name in names}
        new._read_files = self._read_files
        return new

    def register(
        self, schema: ChatCompletionFunctionToolParam, handler: Callable[..., str]
    ) -> None:
        name = schema["function"]["name"]
        self._schemas.append(schema)
        self._handlers[name] = handler

    def execute(self, name: str, arguments: str, bus: EventBus) -> str:
        handler = self._handlers.get(name)
        if handler is None:
            return f"error: 未知工具 {name}"

        try:
            params = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            return f"error: 工具 {name} 的参数不是合法 JSON"

        try:
            if name in {"read", "write", "edit"}:
                return handler(bus, state=self._read_files, **params)
            return handler(bus, **params)
        except Exception as exc:
            return f"error: 工具 {name} 执行失败: {exc}"


# ---------- 辅助函数 ----------


def _schema_name(schema: ChatCompletionToolUnionParam) -> str | None:
    function = schema.get("function")
    if isinstance(function, dict):
        return function.get("name")
    return None


def _resolve_workspace_path(path: str) -> Path:
    """解析路径并确保其位于工作区（ROOT_DIR）内。"""
    root = Config.ROOT_DIR.resolve()
    candidate = Path(path.replace("\\", "/")).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError(f"路径 {path} 超出工作区 {root}")
    return candidate


def _skipped(path: Path, base: Path) -> bool:
    return any(part in _SKIP_DIRS for part in path.relative_to(base).parts)
