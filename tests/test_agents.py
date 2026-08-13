from pathlib import Path
from types import SimpleNamespace

import pytest

from src.api.agents import (
    AgentRegistry,
    SubAgent,
    SubAgentRunner,
    _parse_tools,
    load_agents,
)
from src.api.tools import ToolRegistry
from src.config import Config
from src.core.event_bus import EventBus, EventTypes


def _write_agent(
    tmp_path, name="librarian", tools="[read, grep]", description="资料整理", body="# 角色"
):
    agent_dir = tmp_path / "agents"
    agent_dir.mkdir(exist_ok=True)
    path = agent_dir / f"{name}.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\ntools: {tools}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def test_load_agents_parses_frontmatter(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    _write_agent(tmp_path)
    agents = load_agents()
    assert len(agents) == 1
    agent = agents[0]
    assert agent.name == "librarian"
    assert agent.description == "资料整理"
    assert agent.tools == ["read", "grep"]
    assert agent.prompt == "# 角色"


def test_load_agents_skips_missing_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    agent_dir = tmp_path / "agents"
    agent_dir.mkdir()
    (agent_dir / "bad.md").write_text("---\nname: x\n---\nbody", encoding="utf-8")
    (agent_dir / "ok.md").write_text(
        "---\nname: ok\ndescription: d\ntools: [read]\n---\nbody",
        encoding="utf-8",
    )
    agents = load_agents()
    assert [a.name for a in agents] == ["ok"]


def test_load_agents_skips_unknown_tool(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    agent_dir = tmp_path / "agents"
    agent_dir.mkdir()
    (agent_dir / "bad.md").write_text(
        "---\nname: x\ndescription: d\ntools: [nope]\n---\nbody", encoding="utf-8"
    )
    assert load_agents() == []


def test_load_agents_skips_forbidden_tool(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    agent_dir = tmp_path / "agents"
    agent_dir.mkdir()
    (agent_dir / "bad.md").write_text(
        "---\nname: x\ndescription: d\ntools: [ask]\n---\nbody", encoding="utf-8"
    )
    assert load_agents() == []


def test_parse_tools_rejects_malformed():
    with pytest.raises(ValueError):
        _parse_tools("read, grep", Path("x.md"))


def test_registry_get_and_names(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    _write_agent(tmp_path, name="librarian")
    _write_agent(tmp_path, name="reviewer", tools="[read]")
    registry = AgentRegistry()
    assert registry.get("librarian") is not None
    assert registry.get("nope") is None
    assert registry.names() == ["librarian", "reviewer"]


def test_registry_with_explicit_agents():
    agent = SubAgent(
        name="x", description="d", tools=["read"], prompt="p", path=Path("x.md")
    )
    registry = AgentRegistry([agent])
    assert registry.get("x") is agent


def _chunk(content: str):
    delta = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def test_subagent_runner_streams_and_returns(monkeypatch, tmp_path):
    from pathlib import Path

    from src.api import agents as agents_module

    monkeypatch.setattr(Config, "CUSTOM_API_KEY", "k")
    monkeypatch.setattr(Config, "CUSTOM_API_URL", "https://api.example.com/v1")
    bus = EventBus()
    tools = ToolRegistry().filtered({"read"})
    subagent = SubAgent(
        name="reviewer",
        description="d",
        tools=["read"],
        prompt="# 角色",
        path=Path("x.md"),
    )
    runner = SubAgentRunner(Config, bus, tools, subagent, "审查代码", context="背景")

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: iter([_chunk("审查完成")])
            )
        )
    )
    monkeypatch.setattr(agents_module, "make_client", lambda config: fake_client)

    result = runner.run()
    events = bus.drain()
    assert result == "审查完成"
    types = [e.type for e in events]
    assert types[0] == EventTypes.SUBAGENT_START
    assert EventTypes.SUBAGENT_TOKEN in types
    assert types[-1] == EventTypes.SUBAGENT_DONE
    assert events[0].data["agent_id"] == "reviewer"
    assert events[-1].data["result"] == "审查完成"
