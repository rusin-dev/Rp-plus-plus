import io
import re

from rich.console import Console

from src.ui.mascot import _MASCOT_ART, mascot_text, print_mascot

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _capture(force_terminal: bool) -> str:
    buffer = io.StringIO()
    console = Console(force_terminal=force_terminal, width=80, file=buffer)
    print_mascot(console)
    return _ANSI_RE.sub("", buffer.getvalue())


def test_mascot_art_has_expected_rows():
    assert len(_MASCOT_ART) == 6
    assert _MASCOT_ART[0].strip() == "▬"
    assert _MASCOT_ART[-1].strip() == "▮▮▮▬▬▬▬▬▮▮▮"


def test_mascot_text_contains_all_rows():
    text = mascot_text()
    plain = text.plain
    for row in _MASCOT_ART:
        assert row in plain


def test_print_mascot_includes_tagline():
    output = _capture(force_terminal=True)
    assert "你的编程副驾驶" in output
    assert "v0.1.0" in output
    assert "▮" in output
    assert "▬" in output


def test_print_mascot_works_without_terminal():
    output = _capture(force_terminal=False)
    assert "你的编程副驾驶" in output
