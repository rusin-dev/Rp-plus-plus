from src.ui.formatters import extract_read_path, format_grep_status, format_tool_call


def test_positional_file_path():
    assert format_tool_call("read", '{"file_path": "src/foo.py"}') == "read(src/foo.py)"


def test_write_hides_content_inline():
    assert (
        format_tool_call("write", '{"file_path": "src/a.py", "content": "x = 1\\n"}')
        == "write(src/a.py)"
    )


def test_edit_hides_strings_inline():
    assert (
        format_tool_call(
            "edit",
            '{"file_path": "src/a.py", "old_string": "x", "new_string": "y"}',
        )
        == "edit(src/a.py)"
    )


def test_shell_command_positional():
    assert format_tool_call("shell", '{"command": "pip install rich"}') == 'shell("pip install rich")'


def test_grep_pattern_and_path():
    assert (
        format_tool_call("grep", '{"pattern": "class Foo", "path": "src"}')
        == 'grep("class Foo", src)'
    )


def test_delegate_positional():
    assert (
        format_tool_call("delegate", '{"agent": "librarian", "task": "检索资料"}')
        == "delegate(librarian, 检索资料)"
    )


def test_web_search_query():
    assert (
        format_tool_call("web_search", '{"query": "python", "count": 3}')
        == "web_search(python, count=3)"
    )


def test_empty_arguments():
    assert format_tool_call("grep", "{}") == "grep()"


def test_invalid_json_falls_back():
    assert format_tool_call("grep", "not-json") == "grep(not-json)"


def test_long_value_truncated():
    long_str = "a" * 200
    formatted = format_tool_call("shell", f'{{"command": "{long_str}"}}')
    assert "…" in formatted
    assert "a" * 61 not in formatted


def test_extract_read_path():
    assert extract_read_path('{"file_path": "src/foo.py"}') == "src/foo.py"


def test_extract_read_path_none_on_missing():
    assert extract_read_path("{}") is None
    assert extract_read_path("not-json") is None
    assert extract_read_path("") is None
    assert extract_read_path('{"file_path": 123}') is None


def test_format_grep_status_counts_matches():
    assert format_grep_status("a.py:1: foo\nb.py:2: foo") == "匹配 2 处"


def test_format_grep_status_no_match():
    assert format_grep_status("无匹配") == "无匹配"


def test_format_grep_status_too_many():
    assert format_grep_status("匹配结果过多，仅显示前 50 条:\nx") == "匹配结果过多"


def test_format_grep_status_error():
    assert format_grep_status("error: 非法正则表达式: [") == "✖ 非法正则表达式: ["
