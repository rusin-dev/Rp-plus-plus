import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.api.agents import (
    AgentRegistry,
    SubAgent,
    SubAgentRunner,
    _ConfigOverride,
    _parse_overrides,
    _parse_tools,
    load_agents,
)
from src.api.tools import ToolRegistry
from src.config import Config, Provider
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
    agent = SubAgent(name="x", description="d", tools=["read"], prompt="p", path=Path("x.md"))
    registry = AgentRegistry([agent])
    assert registry.get("x") is agent


def _chunk(content: str):
    delta = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def test_subagent_runner_streams_and_returns(monkeypatch, tmp_path):
    from pathlib import Path

    from src.api import agents as agents_module

    monkeypatch.setattr(Config, "AUTO_GIT", False)
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "test")
    monkeypatch.setattr(
        Config,
        "providers",
        lambda: {
            "test": Provider(
                name="test",
                api_key="k",
                api_url="https://api.example.com/v1",
                models=["m"],
                default_model="m",
            )
        },
    )
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
            completions=SimpleNamespace(create=lambda **kwargs: iter([_chunk("审查完成")]))
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


def test_subagent_runner_fails_clearly_without_model(monkeypatch):
    """provider 配置缺失（模型名为空）时，子 Agent 返回明确错误，且不发起 API 请求。

    回归：provider 配置被 git stash 移出工作区后 active_model() 为空串，
    旧实现会向 API 发送空模型名并收到 400 "you passed ."。
    """
    from src.api import agents as agents_module

    monkeypatch.setattr(Config, "AUTO_GIT", False)
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", None)
    monkeypatch.setattr(Config, "ACTIVE_MODEL", None)
    monkeypatch.setattr(Config, "providers", lambda: {})
    bus = EventBus()
    tools = ToolRegistry().filtered({"read"})
    subagent = SubAgent(
        name="reviewer",
        description="d",
        tools=["read"],
        prompt="# 角色",
        path=Path("x.md"),
    )
    runner = SubAgentRunner(Config, bus, tools, subagent, "审查代码")

    def boom(*args, **kwargs):  # pragma: no cover - 不应被调用
        raise AssertionError("模型名为空时不应发起 API 请求")

    monkeypatch.setattr(agents_module, "make_client", boom)

    result = runner.run()
    assert result.startswith("error:")
    assert "模型" in result
    events = bus.drain()
    assert events[0].type == EventTypes.SUBAGENT_ERROR
    assert "reviewer" in events[0].data["agent_id"]


def test_subagent_runner_task_branch_approved(monkeypatch, tmp_path):
    """启用自动 git 时，委派任务在独立分支执行，批准后回到主分支。"""
    if shutil.which("git") is None:
        pytest.skip("git 不可用，跳过任务分支集成测试")
    from src.api import agents as agents_module

    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(Config, "AUTO_GIT", True)
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "test")
    monkeypatch.setattr(
        Config,
        "providers",
        lambda: {
            "test": Provider(
                name="test",
                api_key="k",
                api_url="https://api.example.com/v1",
                models=["m"],
                default_model="m",
            )
        },
    )
    bus = EventBus()
    tools = ToolRegistry().filtered({"read"})
    subagent = SubAgent(
        name="backend_builder",
        description="d",
        tools=["read"],
        prompt="# 角色",
        path=Path("x.md"),
    )
    runner = SubAgentRunner(Config, bus, tools, subagent, "写一个模块")

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: iter([_chunk("模块完成")]))
        )
    )
    monkeypatch.setattr(agents_module, "make_client", lambda config: fake_client)

    result = runner.run(ask=lambda q: "y")
    assert result == "模块完成"

    from src.core.git_ops import current_branch

    main = current_branch(tmp_path)
    assert main is not None
    leftover = subprocess.run(
        ["git", "branch", "--list", "task/*"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert leftover.stdout.strip() == ""
    events = bus.drain()
    types = [e.type for e in events]
    assert EventTypes.SUBAGENT_DONE in types
    assert EventTypes.SUBAGENT_ERROR not in types


def _provider_models(monkeypatch, models=("m1", "m2"), default="m1", name="test"):
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", name)
    monkeypatch.setattr(Config, "ACTIVE_MODEL", default)
    monkeypatch.setattr(
        Config,
        "providers",
        lambda: {
            name: Provider(
                name=name,
                api_key="k",
                api_url="https://api.example.com/v1",
                models=list(models),
                default_model=default,
            )
        },
    )


def test_parse_overrides_accepts_valid_values(monkeypatch):
    _provider_models(monkeypatch)
    data = {"provider": "test", "model": "m2", "variant": "high", "mode": "plan"}
    overrides = _parse_overrides(data, Path("x.md"))
    assert overrides == {
        "provider": "test",
        "model": "m2",
        "variant": "high",
        "mode": "plan",
    }


def test_parse_overrides_allows_missing_fields(monkeypatch):
    _provider_models(monkeypatch)
    assert _parse_overrides({}, Path("x.md")) == {
        "provider": None,
        "model": None,
        "variant": None,
        "mode": None,
    }


def test_parse_overrides_rejects_unknown_provider(monkeypatch):
    _provider_models(monkeypatch)
    with pytest.raises(ValueError, match="provider"):
        _parse_overrides({"provider": "nope"}, Path("x.md"))


def test_parse_overrides_rejects_model_not_in_provider(monkeypatch):
    _provider_models(monkeypatch)
    with pytest.raises(ValueError, match="model"):
        _parse_overrides({"model": "bogus"}, Path("x.md"))


def test_parse_overrides_rejects_unknown_variant(monkeypatch):
    _provider_models(monkeypatch)
    with pytest.raises(ValueError, match="variant"):
        _parse_overrides({"variant": "ultra"}, Path("x.md"))


def test_parse_overrides_rejects_unknown_mode(monkeypatch):
    _provider_models(monkeypatch)
    with pytest.raises(ValueError, match="mode"):
        _parse_overrides({"mode": "chaos"}, Path("x.md"))


def test_load_agents_reads_override_frontmatter(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    _provider_models(monkeypatch)
    agent_dir = tmp_path / "agents"
    agent_dir.mkdir()
    (agent_dir / "reviewer.md").write_text(
        "---\n"
        "name: reviewer\n"
        "description: d\n"
        "tools: [read]\n"
        "provider: test\n"
        "model: m2\n"
        "variant: high\n"
        "mode: plan\n"
        "---\n\n# 角色\n",
        encoding="utf-8",
    )
    agents = load_agents()
    assert len(agents) == 1
    a = agents[0]
    assert a.provider == "test"
    assert a.model == "m2"
    assert a.variant == "high"
    assert a.mode == "plan"


def test_load_agents_skips_agent_with_invalid_override(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    _provider_models(monkeypatch)
    agent_dir = tmp_path / "agents"
    agent_dir.mkdir()
    (agent_dir / "bad.md").write_text(
        "---\nname: bad\ndescription: d\ntools: [read]\nvariant: ultra\n---\nbody",
        encoding="utf-8",
    )
    assert load_agents() == []


def test_config_override_sets_and_restores(monkeypatch):
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "orig")
    monkeypatch.setattr(Config, "ACTIVE_MODEL", "om")
    monkeypatch.setattr(Config, "ACTIVE_VARIANT", "medium")
    monkeypatch.setattr(Config, "ACTIVE_MODE", "auto")
    with _ConfigOverride(provider="new", model="nm", variant="high", mode="plan"):
        assert Config.ACTIVE_PROVIDER == "new"
        assert Config.ACTIVE_MODEL == "nm"
        assert Config.ACTIVE_VARIANT == "high"
        assert Config.ACTIVE_MODE == "plan"
    assert Config.ACTIVE_PROVIDER == "orig"
    assert Config.ACTIVE_MODEL == "om"
    assert Config.ACTIVE_VARIANT == "medium"
    assert Config.ACTIVE_MODE == "auto"


def test_config_override_restores_on_exception(monkeypatch):
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "orig")
    monkeypatch.setattr(Config, "ACTIVE_MODEL", "om")
    with pytest.raises(RuntimeError):
        with _ConfigOverride(provider="new", model="nm"):
            assert Config.ACTIVE_PROVIDER == "new"
            raise RuntimeError("boom")
    assert Config.ACTIVE_PROVIDER == "orig"
    assert Config.ACTIVE_MODEL == "om"


def test_config_override_only_touches_specified_fields(monkeypatch):
    monkeypatch.setattr(Config, "ACTIVE_VARIANT", "medium")
    with _ConfigOverride(provider="new"):
        assert Config.ACTIVE_VARIANT == "medium"


def test_subagent_runner_applies_overrides_during_run(monkeypatch):
    """子 Agent 指定 provider/model 时，Config 在 run 期间被临时覆盖，结束后还原。"""
    from src.api import agents as agents_module

    monkeypatch.setattr(Config, "AUTO_GIT", False)
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "main")
    monkeypatch.setattr(Config, "ACTIVE_MODEL", "main-model")
    monkeypatch.setattr(Config, "ACTIVE_VARIANT", "medium")
    monkeypatch.setattr(Config, "ACTIVE_MODE", "auto")
    monkeypatch.setattr(
        Config,
        "providers",
        lambda: {
            "main": Provider(
                name="main",
                api_key="k",
                api_url="https://api.example.com/v1",
                models=["main-model"],
                default_model="main-model",
            ),
            "alt": Provider(
                name="alt",
                api_key="k",
                api_url="https://api.example.com/v1",
                models=["alt-model"],
                default_model="alt-model",
            ),
        },
    )

    observed = {}

    def fake_stream(config, bus, client, tools, messages, mode, **kwargs):
        observed["model"] = config.active_model()
        observed["variant_params"] = config.active_variant_params()
        observed["mode"] = mode
        return False, "完成"

    monkeypatch.setattr(agents_module, "stream_completion", fake_stream)
    monkeypatch.setattr(agents_module, "make_client", lambda config: SimpleNamespace())

    bus = EventBus()
    tools = ToolRegistry().filtered({"read"})
    subagent = SubAgent(
        name="reviewer",
        description="d",
        tools=["read"],
        prompt="# 角色",
        path=Path("x.md"),
        provider="alt",
        model="alt-model",
        variant="high",
        mode="plan",
    )
    runner = SubAgentRunner(Config, bus, tools, subagent, "task")

    assert runner.run() == "完成"
    assert observed == {
        "model": "alt-model",
        "variant_params": {"reasoning_effort": "high"},
        "mode": "plan",
    }
    assert Config.ACTIVE_PROVIDER == "main"
    assert Config.ACTIVE_MODEL == "main-model"
    assert Config.ACTIVE_VARIANT == "medium"
    assert Config.ACTIVE_MODE == "auto"


def test_subagent_runner_inherits_main_settings_when_no_overrides(monkeypatch):
    """未指定覆盖时，子 Agent 完全沿用主 Agent 的当前设置。"""
    from src.api import agents as agents_module

    monkeypatch.setattr(Config, "AUTO_GIT", False)
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "main")
    monkeypatch.setattr(Config, "ACTIVE_MODEL", "main-model")
    monkeypatch.setattr(Config, "ACTIVE_VARIANT", "high")
    monkeypatch.setattr(Config, "ACTIVE_MODE", "build")
    monkeypatch.setattr(
        Config,
        "providers",
        lambda: {
            "main": Provider(
                name="main",
                api_key="k",
                api_url="https://api.example.com/v1",
                models=["main-model"],
                default_model="main-model",
            )
        },
    )

    observed = {}

    def fake_stream(config, bus, client, tools, messages, mode, **kwargs):
        observed["model"] = config.active_model()
        observed["variant_params"] = config.active_variant_params()
        observed["mode"] = mode
        return False, "ok"

    monkeypatch.setattr(agents_module, "stream_completion", fake_stream)
    monkeypatch.setattr(agents_module, "make_client", lambda config: SimpleNamespace())

    bus = EventBus()
    tools = ToolRegistry().filtered({"read"})
    subagent = SubAgent(
        name="librarian",
        description="d",
        tools=["read"],
        prompt="# 角色",
        path=Path("x.md"),
    )
    runner = SubAgentRunner(Config, bus, tools, subagent, "task")
    assert runner.run() == "ok"
    assert observed == {
        "model": "main-model",
        "variant_params": {"reasoning_effort": "high"},
        "mode": "build",
    }
    assert Config.ACTIVE_VARIANT == "high"
    assert Config.ACTIVE_MODE == "build"
