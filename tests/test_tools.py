from urllib.parse import urlparse

from src.api.tools import ToolRegistry
from src.config import Config
from src.core.event_bus import Event, EventBus, EventTypes


def test_registry_builtin_schemas():
    registry = ToolRegistry()
    names = [s["function"]["name"] for s in registry.schemas if "function" in s]
    assert names == [
        "ask",
        "shell",
        "read",
        "write",
        "edit",
        "grep",
        "web_search",
        "web_fetch",
        "delegate",
        "create_todo_list",
        "todos_read",
        "todos_update",
    ]


def test_execute_unknown_tool():
    registry = ToolRegistry()
    result = registry.execute("nope", "{}", EventBus())
    assert result.startswith("error:")


def test_execute_invalid_json():
    registry = ToolRegistry()
    result = registry.execute("ask", "not-json", EventBus())
    assert result.startswith("error:")


def test_execute_shell():
    registry = ToolRegistry()
    result = registry.execute("shell", '{"command": "echo rp-test"}', EventBus())
    assert "rp-test" in result
    assert "exit code: 0" in result


def test_execute_shell_decodes_utf8_without_crash(monkeypatch):
    import json
    import sys as _sys

    registry = ToolRegistry()
    code = "import sys;sys.stdout.buffer.write(bytes([0xe4,0xbd,0xa0,0xe5,0xa5,0xbd]))"
    command = f'"{_sys.executable}" -c "{code}"'
    result = registry.execute("shell", json.dumps({"command": command}), EventBus())
    assert "error:" not in result
    assert "stdout:\n" in result
    assert "None" not in result
    assert "你好" in result


def test_execute_shell_timeout(monkeypatch):
    import subprocess

    def fake_run(*args, **kwargs):
        timeout: float = kwargs.get("timeout") or 0
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=timeout)

    monkeypatch.setattr(subprocess, "run", fake_run)
    registry = ToolRegistry()
    result = registry.execute("shell", '{"command": "sleep 100"}', EventBus())
    assert "超时" in result


def test_execute_ask_requires_user(monkeypatch):
    registry = ToolRegistry()
    bus = EventBus()

    def fake_await(event_type, timeout=None):
        return Event(event_type, "42")

    monkeypatch.setattr(bus, "await_event", fake_await)
    result = registry.execute("ask", '{"question": "1+1?"}', bus)
    assert result == "42"


def test_execute_read(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    (tmp_path / "note.txt").write_text("hello rp", encoding="utf-8")
    registry = ToolRegistry()
    result = registry.execute("read", '{"file_path": "note.txt"}', EventBus())
    assert result == "hello rp"


def test_execute_read_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    registry = ToolRegistry()
    result = registry.execute("read", '{"file_path": "nope.txt"}', EventBus())
    assert "error:" in result


def test_execute_read_outside_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    registry = ToolRegistry()
    result = registry.execute("read", '{"file_path": "../secret.txt"}', EventBus())
    assert "error:" in result
    assert "工作区" in result


def test_execute_write(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    registry = ToolRegistry()
    result = registry.execute(
        "write",
        '{"file_path": "src/a.py", "content": "print(1)\\n"}',
        EventBus(),
    )
    assert "已写入" in result
    assert (tmp_path / "src" / "a.py").read_text(encoding="utf-8") == "print(1)\n"


def test_execute_write_outside_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    registry = ToolRegistry()
    result = registry.execute(
        "write",
        '{"file_path": "..\\\\evil.txt", "content": "x"}',
        EventBus(),
    )
    assert "error:" in result
    assert "工作区" in result


def test_execute_read_offset_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    (tmp_path / "f.txt").write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    registry = ToolRegistry()
    result = registry.execute("read", '{"file_path": "f.txt", "offset": 2, "limit": 2}', EventBus())
    assert result.startswith("b\nc")
    assert "已截断" in result


def test_execute_read_default_limit_truncates(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    (tmp_path / "big.txt").write_text("\n".join(f"line{i}" for i in range(300)), encoding="utf-8")
    registry = ToolRegistry()
    result = registry.execute("read", '{"file_path": "big.txt"}', EventBus())
    assert result.startswith("line0")
    assert "已截断" in result


def test_execute_write_new_file_no_read_needed(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    registry = ToolRegistry()
    result = registry.execute("write", '{"file_path": "new.py", "content": "x"}', EventBus())
    assert "已写入" in result
    assert (tmp_path / "new.py").read_text(encoding="utf-8") == "x"


def test_execute_write_requires_read_first(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    (tmp_path / "a.py").write_text("old\n", encoding="utf-8")
    registry = ToolRegistry()
    result = registry.execute("write", '{"file_path": "a.py", "content": "new"}', EventBus())
    assert "error:" in result
    assert "read" in result
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "old\n"


def test_execute_write_after_read_allowed(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    (tmp_path / "a.py").write_text("old\n", encoding="utf-8")
    registry = ToolRegistry()
    registry.execute("read", '{"file_path": "a.py"}', EventBus())
    result = registry.execute("write", '{"file_path": "a.py", "content": "new"}', EventBus())
    assert "已写入" in result
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "new"


def test_execute_write_offset_replace(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    (tmp_path / "f.txt").write_text("a\nb\nc\nd\n", encoding="utf-8")
    registry = ToolRegistry()
    registry.execute("read", '{"file_path": "f.txt"}', EventBus())
    result = registry.execute(
        "write",
        '{"file_path": "f.txt", "content": "B1\\nB2", "offset": 2, "limit": 1}',
        EventBus(),
    )
    assert "已写入" in result
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "a\nB1\nB2\nc\nd\n"


def test_execute_write_publishes_file_written(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    bus = EventBus()
    registry = ToolRegistry()
    registry.execute("write", '{"file_path": "new.py", "content": "x = 1\\n"}', bus)
    event = bus.await_event(EventTypes.FILE_WRITTEN, timeout=1)
    assert event.data["path"].endswith("new.py")
    assert event.data["content"] == "x = 1\n"


# ---------- edit 工具 ----------


def test_execute_edit_requires_read_first(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    (tmp_path / "a.py").write_text("old = 1\n", encoding="utf-8")
    registry = ToolRegistry()
    result = registry.execute(
        "edit",
        '{"file_path": "a.py", "old_string": "old = 1", "new_string": "new = 2"}',
        EventBus(),
    )
    assert "error:" in result
    assert "read" in result
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "old = 1\n"


def test_execute_edit_replaces_first_occurrence(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    (tmp_path / "a.py").write_text("x = 1\nx = 2\n", encoding="utf-8")
    registry = ToolRegistry()
    registry.execute("read", '{"file_path": "a.py"}', EventBus())
    result = registry.execute(
        "edit",
        '{"file_path": "a.py", "old_string": "x = ", "new_string": "y = "}',
        EventBus(),
    )
    assert "已编辑" in result
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "y = 1\nx = 2\n"


def test_execute_edit_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    (tmp_path / "a.py").write_text("hello\n", encoding="utf-8")
    registry = ToolRegistry()
    registry.execute("read", '{"file_path": "a.py"}', EventBus())
    result = registry.execute(
        "edit",
        '{"file_path": "a.py", "old_string": "nope", "new_string": "x"}',
        EventBus(),
    )
    assert "error:" in result
    assert "未找到" in result


def test_execute_edit_publishes_file_diff(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    (tmp_path / "a.py").write_text("old = 1\n", encoding="utf-8")
    bus = EventBus()
    registry = ToolRegistry()
    registry.execute("read", '{"file_path": "a.py"}', EventBus())
    registry.execute(
        "edit",
        '{"file_path": "a.py", "old_string": "old", "new_string": "new"}',
        bus,
    )
    event = bus.await_event(EventTypes.FILE_DIFF, timeout=1)
    diff = event.data["diff"]
    assert event.data["path"].endswith("a.py")
    assert "--- a/" in diff and "+++ b/" in diff
    assert "-old = 1" in diff and "+new = 1" in diff


def test_execute_edit_empty_old_string(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
    registry = ToolRegistry()
    registry.execute("read", '{"file_path": "a.py"}', EventBus())
    result = registry.execute(
        "edit",
        '{"file_path": "a.py", "old_string": "", "new_string": "y"}',
        EventBus(),
    )
    assert "error:" in result
    assert "old_string" in result


def test_execute_grep(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("x = 2\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "skip.py").write_text("foo\n", encoding="utf-8")

    registry = ToolRegistry()
    result = registry.execute("grep", '{"pattern": "foo"}', EventBus())
    assert "a.py:1" in result
    assert ".venv" not in result
    assert "b.py" not in result


def test_execute_grep_include_filter(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    (tmp_path / "a.py").write_text("foo\n", encoding="utf-8")
    (tmp_path / "a.md").write_text("foo\n", encoding="utf-8")
    registry = ToolRegistry()
    result = registry.execute("grep", '{"pattern": "foo", "include": "*.md"}', EventBus())
    assert "a.md" in result
    assert "a.py" not in result


def test_execute_grep_invalid_regex(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    registry = ToolRegistry()
    result = registry.execute("grep", '{"pattern": "["}', EventBus())
    assert "error:" in result


def test_execute_grep_no_match(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    (tmp_path / "a.py").write_text("nothing here\n", encoding="utf-8")
    registry = ToolRegistry()
    result = registry.execute("grep", '{"pattern": "zzz"}', EventBus())
    assert "无匹配" in result


def test_register_custom_tool():
    registry = ToolRegistry()

    def handler(bus, value: str) -> str:
        return f"got:{value}"

    registry.register(
        {
            "type": "function",
            "function": {"name": "echo_tool", "parameters": {"type": "object"}},
        },
        handler,
    )
    result = registry.execute("echo_tool", '{"value": "x"}', EventBus())
    assert result == "got:x"


# ---------- todo 工具 ----------


def test_create_todo_list_sets_up_list(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    registry = ToolRegistry()
    result = registry.execute(
        "create_todo_list",
        '{"todos": [{"content": "写测试", "status": "pending"}, {"content": "实现功能", "status": "pending"}]}',
        EventBus(),
    )
    assert "写测试" in result
    assert "实现功能" in result


def test_todos_read_returns_empty_when_no_list(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    registry = ToolRegistry()
    result = registry.execute("todos_read", "{}", EventBus())
    assert "暂无" in result or "空" in result


def test_todos_read_after_create(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    registry = ToolRegistry()
    registry.execute(
        "create_todo_list",
        '{"todos": [{"content": "任务A", "status": "pending"}]}',
        EventBus(),
    )
    result = registry.execute("todos_read", "{}", EventBus())
    assert "任务A" in result


def test_todos_update_status(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    registry = ToolRegistry()
    registry.execute(
        "create_todo_list",
        '{"todos": [{"content": "任务A"}, {"content": "任务B"}]}',
        EventBus(),
    )
    result = registry.execute("todos_update", '{"todo_id": 2, "status": "completed"}', EventBus())
    assert "已更新待办 #2" in result
    read = registry.execute("todos_read", "{}", EventBus())
    assert "任务B" in read
    assert "[x]" in read


def test_todos_update_invalid_id(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    registry = ToolRegistry()
    registry.execute(
        "create_todo_list",
        '{"todos": [{"content": "任务A"}]}',
        EventBus(),
    )
    result = registry.execute("todos_update", '{"todo_id": 99, "status": "completed"}', EventBus())
    assert "error:" in result


def test_todos_update_invalid_status(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    registry = ToolRegistry()
    registry.execute(
        "create_todo_list",
        '{"todos": [{"content": "任务A"}]}',
        EventBus(),
    )
    result = registry.execute("todos_update", '{"todo_id": 1, "status": "bogus"}', EventBus())
    assert "error:" in result


def test_create_todo_list_replaces_previous(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    registry = ToolRegistry()
    registry.execute(
        "create_todo_list",
        '{"todos": [{"content": "旧任务"}]}',
        EventBus(),
    )
    registry.execute(
        "create_todo_list",
        '{"todos": [{"content": "新任务"}]}',
        EventBus(),
    )
    read = registry.execute("todos_read", "{}", EventBus())
    assert "新任务" in read
    assert "旧任务" not in read


def test_todo_tools_shared_across_filtered_registry(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    registry = ToolRegistry()
    registry.execute(
        "create_todo_list",
        '{"todos": [{"content": "共享任务"}]}',
        EventBus(),
    )
    filtered = registry.filtered({"todos_read"})
    result = filtered.execute("todos_read", "{}", EventBus())
    assert "共享任务" in result


def test_todo_items_returns_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    registry = ToolRegistry()
    registry.execute(
        "create_todo_list",
        '{"todos": [{"content": "任务A", "status": "in_progress"}]}',
        EventBus(),
    )
    items = registry.todo_items()
    assert items == [{"id": 1, "content": "任务A", "status": "in_progress", "priority": "medium"}]
    items.append({"id": 2, "content": "外部污染", "status": "pending", "priority": "low"})
    assert registry.todo_items() == [
        {"id": 1, "content": "任务A", "status": "in_progress", "priority": "medium"}
    ]


# ---------- 网络工具 ----------

_SAMPLE_DDG_HTML = """
<div class="result results_links results_links_deep web-result ">
  <div class="links_main links_deep result__body">
    <div class="result__a">
      <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&amp;rut=x">Example <b>Page</b></a>
    </div>
    <div class="result__snippet">
      <a rel="nofollow" class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&amp;rut=x">This is a snippet &amp; more.</a>
    </div>
  </div>
</div>
<div class="result results_links results_links_deep web-result ">
  <div class="links_main links_deep result__body">
    <div class="result__a">
      <a rel="nofollow" class="result__a" href="https://other.org/">Other Site</a>
    </div>
  </div>
</div>
"""

_SAMPLE_BING_HTML = """
<li class="b_algo" data-id iid=SERP.5322>
  <h2 class=""><a target="_blank" href="https://example.com/page" h="ID=SERP,5119.2"><strong>Example Page</strong> - 测试</a></h2>
  <div class="b_caption"><p class="b_lineclamp2">This is a snippet &amp; more.</p></div>
</li>
<li class="b_algo">
  <h2><a href="https://other.org/">Other Site</a></h2>
  <div class="b_caption"><p>Another snippet.</p></div>
</li>
"""


def _mock_http(monkeypatch, html_text):
    from src.api import tools as tools_module

    monkeypatch.setattr(
        tools_module, "_http_get", lambda url, max_bytes=None, timeout=20: html_text
    )


def test_execute_web_search_ddg(monkeypatch):
    monkeypatch.setattr(Config, "SEARCH_BACKEND", "ddg")
    _mock_http(monkeypatch, _SAMPLE_DDG_HTML)
    registry = ToolRegistry()
    result = registry.execute("web_search", '{"query": "python", "count": 2}', EventBus())
    assert "Example Page" in result
    assert "https://example.com/page" in result
    assert "snippet" in result
    assert "Other Site" in result


def test_execute_web_search_bing(monkeypatch):
    monkeypatch.setattr(Config, "SEARCH_BACKEND", "bing")
    _mock_http(monkeypatch, _SAMPLE_BING_HTML)
    registry = ToolRegistry()
    result = registry.execute("web_search", '{"query": "python"}', EventBus())
    assert "Example Page" in result
    assert "https://example.com/page" in result
    assert "snippet" in result
    assert "Other Site" in result


def test_execute_web_search_auto_falls_back(monkeypatch):
    from src.api import tools as tools_module

    monkeypatch.setattr(Config, "SEARCH_BACKEND", "auto")
    calls = []

    def fake_get(url, max_bytes=None, timeout=20):
        calls.append(url)
        if "duckduckgo" in url:
            raise RuntimeError("blocked")
        return _SAMPLE_BING_HTML

    monkeypatch.setattr(tools_module, "_http_get", fake_get)
    registry = ToolRegistry()
    result = registry.execute("web_search", '{"query": "python"}', EventBus())
    assert "Example Page" in result
    assert any((urlparse(url).hostname or "").endswith(".duckduckgo.com") for url in calls)
    assert any((urlparse(url).hostname or "").endswith(".bing.com") for url in calls)


def test_execute_web_search_error(monkeypatch):
    from src.api import tools as tools_module

    def boom(url, max_bytes=None, timeout=20):
        raise RuntimeError("网络不可用")

    monkeypatch.setattr(tools_module, "_http_get", boom)
    registry = ToolRegistry()
    result = registry.execute("web_search", '{"query": "x"}', EventBus())
    assert "error:" in result


def test_execute_web_fetch(monkeypatch):
    html_text = "<html><body><h1>Hello</h1><p>This is <b>rich</b> text.</p></body></html>"
    _mock_http(monkeypatch, html_text)
    registry = ToolRegistry()
    result = registry.execute("web_fetch", '{"url": "https://example.com"}', EventBus())
    assert "Hello" in result
    assert "rich text" in result


def test_execute_web_fetch_rejects_non_http(monkeypatch):
    registry = ToolRegistry()
    result = registry.execute("web_fetch", '{"url": "file:///etc/passwd"}', EventBus())
    assert "error:" in result


def test_execute_web_fetch_truncates(monkeypatch):
    long_text = "<p>" + "a" * 5000 + "</p>"
    _mock_http(monkeypatch, long_text)
    registry = ToolRegistry()
    result = registry.execute(
        "web_fetch", '{"url": "https://example.com", "max_chars": 1000}', EventBus()
    )
    assert len(result) < 1100
    assert "已截断" in result


def test_delegate_unknown_agent(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    registry = ToolRegistry()
    result = registry.execute("delegate", '{"agent": "nope", "task": "x"}', EventBus())
    assert result.startswith("error: 未知子 Agent: nope")


def test_delegate_runs_subagent(monkeypatch, tmp_path):
    from pathlib import Path

    from src.api import agents as agents_module
    from src.api.agents import SubAgent

    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    agent = SubAgent(
        name="librarian",
        description="d",
        tools=["read", "grep"],
        prompt="p",
        path=Path("x.md"),
    )
    monkeypatch.setattr(agents_module, "load_agents", lambda: [agent])

    captured = {}

    class _FakeRunner:
        def __init__(self, config, bus, tools, subagent, task, context=None):
            captured["tools"] = tools
            captured["task"] = task
            captured["context"] = context
            captured["subagent"] = subagent

        def run(self):
            return "委派结果"

    monkeypatch.setattr(agents_module, "SubAgentRunner", _FakeRunner)
    registry = ToolRegistry()
    result = registry.execute(
        "delegate",
        '{"agent": "librarian", "task": "任务", "context": "背景"}',
        EventBus(),
    )
    assert result == "委派结果"
    assert captured["task"] == "任务"
    assert captured["context"] == "背景"
    assert captured["subagent"] is agent
    names = [s["function"]["name"] for s in captured["tools"].schemas]
    assert names == ["read", "grep"]


def test_registry_filtered():
    registry = ToolRegistry()
    filtered = registry.filtered({"read", "grep"})
    assert set(filtered._handlers) == {"read", "grep"}
    result = filtered.execute("write", '{"file_path": "a", "content": "b"}', EventBus())
    assert result.startswith("error:")
