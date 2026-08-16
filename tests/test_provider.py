import json

import pytest

from src.config import Config


def _write_provider(
    tmp_path,
    name: str,
    api_key: str,
    api_url: str,
    models: list[str] | None = None,
    default: str = "",
    directory: str = "providers",
) -> None:
    dir_path = tmp_path / directory
    dir_path.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {
        "name": name,
        "api_key": api_key,
        "api_url": api_url,
    }
    if models is not None:
        data["models"] = models
    if default:
        data["default_model"] = default
    (dir_path / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _setup(tmp_path, monkeypatch, preset_names=("deepseek", "openai")):
    monkeypatch.setattr(Config, "PRESET_DIR", tmp_path / "preset")
    monkeypatch.setattr(Config, "PROVIDER_DIR", tmp_path / "providers")
    monkeypatch.setattr(Config, "RUNTIME_STATE_FILE", tmp_path / "state" / "config.json")
    for name in preset_names:
        _write_provider(
            tmp_path, name, "", f"https://{name}.example.com", ["m1", "m2"], "m1", "preset"
        )


def test_providers_parses_named_providers(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _write_provider(
        tmp_path,
        "deepseek",
        "k1",
        "https://api.deepseek.com",
        ["deepseek-chat", "deepseek-reasoner"],
        "deepseek-chat",
    )
    _write_provider(tmp_path, "openai", "k2", "https://api.openai.com/v1", ["gpt-4o"], "")
    providers = Config.providers()
    assert set(providers) == {"deepseek", "openai"}
    assert providers["deepseek"].api_key == "k1"
    assert providers["deepseek"].models == ["deepseek-chat", "deepseek-reasoner"]
    assert providers["deepseek"].default_model == "deepseek-chat"
    assert providers["openai"].default_model == "gpt-4o"


def test_providers_uses_filename_when_name_missing(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, preset_names=())
    dir_path = tmp_path / "providers"
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "my_provider.json").write_text(
        json.dumps({"api_key": "k", "api_url": "https://x.example.com"}), encoding="utf-8"
    )
    providers = Config.providers()
    assert set(providers) == {"my_provider"}
    assert providers["my_provider"].default_model == ""


def test_providers_ignores_broken_files(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    dir_path = tmp_path / "providers"
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert Config.providers() == {}


def test_presets_parsed_separately(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    presets = Config.presets()
    assert set(presets) == {"deepseek", "openai"}
    assert presets["deepseek"].api_key == ""
    assert presets["deepseek"].api_url == "https://deepseek.example.com"
    assert Config.get_preset("OPENAI") is not None
    assert Config.get_preset("nope") is None


def test_use_preset_writes_config_and_switches(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    provider = Config.use_preset("deepseek", "sk-123")
    assert provider.name == "deepseek"
    assert provider.api_key == "sk-123"
    assert Config.ACTIVE_PROVIDER == "deepseek"
    assert Config.ACTIVE_MODEL == "m1"
    target = tmp_path / "providers" / "deepseek.json"
    assert target.is_file()
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["api_key"] == "sk-123"
    assert saved["default_model"] == "m1"
    configured = Config.get_provider("deepseek")
    assert configured is not None
    assert configured.api_key == "sk-123"
    state = json.loads((tmp_path / "state" / "config.json").read_text(encoding="utf-8"))
    assert state == {"active_provider": "deepseek", "active_model": "m1"}


def test_use_preset_unknown_raises(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        Config.use_preset("nope", "sk-x")


def test_set_api_key_updates_existing_provider(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _write_provider(tmp_path, "deepseek", "old-key", "https://a.example.com", ["m1"], "m1")
    provider = Config.set_api_key("deepseek", "sk-new")
    assert provider.api_key == "sk-new"
    assert provider.models == ["m1"]
    saved = json.loads((tmp_path / "providers" / "deepseek.json").read_text(encoding="utf-8"))
    assert saved["api_key"] == "sk-new"
    assert saved["default_model"] == "m1"
    assert Config.get_provider("deepseek").api_key == "sk-new"


def test_set_api_key_creates_from_preset(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    provider = Config.set_api_key("openai", "sk-123")
    assert provider.name == "openai"
    assert provider.api_key == "sk-123"
    target = tmp_path / "providers" / "openai.json"
    assert target.is_file()
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["api_key"] == "sk-123"
    assert saved["default_model"] == "m1"


def test_set_api_key_unknown_raises(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        Config.set_api_key("nope", "sk-x")


def test_set_api_key_blank_raises(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _write_provider(tmp_path, "deepseek", "k1", "https://a.example.com", ["m1"], "m1")
    with pytest.raises(ValueError):
        Config.set_api_key("deepseek", "   ")


def test_set_provider_switches_and_persists(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _write_provider(tmp_path, "deepseek", "k1", "https://a.example.com", ["d1"], "d1")
    _write_provider(tmp_path, "openai", "k2", "https://b.example.com", ["o1", "o2"], "o2")
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "deepseek")
    Config.set_provider("openai")
    assert Config.ACTIVE_PROVIDER == "openai"
    assert Config.ACTIVE_MODEL == "o2"
    assert Config.active_model() == "o2"
    state = json.loads((tmp_path / "state" / "config.json").read_text(encoding="utf-8"))
    assert state["active_provider"] == "openai"


def test_set_provider_unknown_raises(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _write_provider(tmp_path, "deepseek", "k1", "https://a.example.com", ["d1"], "d1")
    with pytest.raises(ValueError):
        Config.set_provider("nope")


def test_set_model_switches_and_validates(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _write_provider(tmp_path, "deepseek", "k1", "https://a.example.com", ["m1", "m2"], "m1")
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "deepseek")
    Config.set_model("m2")
    assert Config.ACTIVE_MODEL == "m2"
    assert Config.active_model() == "m2"
    with pytest.raises(ValueError):
        Config.set_model("nope")


def test_set_model_allows_any_when_models_empty(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _write_provider(tmp_path, "deepseek", "k1", "https://a.example.com", [], "")
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "deepseek")
    Config.set_model("anything")
    assert Config.active_model() == "anything"


def test_active_provider_uses_selection(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _write_provider(tmp_path, "deepseek", "k1", "https://a.example.com", ["m1"], "m1")
    _write_provider(tmp_path, "openai", "k2", "https://b.example.com", ["m2"], "m2")
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "openai")
    provider = Config.active_provider()
    assert provider is not None
    assert provider.name == "openai"


def test_active_provider_falls_back_to_first(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _write_provider(tmp_path, "openai", "k2", "https://b.example.com", ["m2"], "m2")
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", None)
    provider = Config.active_provider()
    assert provider is not None
    assert provider.name == "openai"


def test_no_provider_returns_empty(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, preset_names=())
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", None)
    monkeypatch.setattr(Config, "ACTIVE_MODEL", None)
    assert Config.providers() == {}
    assert Config.active_provider() is None
    assert Config.active_model() == ""


def test_active_model_prefers_runtime_override(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _write_provider(tmp_path, "deepseek", "k1", "https://a.example.com", ["m1"], "m1")
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "deepseek")
    monkeypatch.setattr(Config, "ACTIVE_MODEL", "override")
    assert Config.active_model() == "override"


def test_load_runtime_state_restores_selection(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "config.json").write_text(
        json.dumps({"active_provider": "openai", "active_model": "gpt-x"}), encoding="utf-8"
    )
    Config.load_runtime_state()
    assert Config.ACTIVE_PROVIDER == "openai"
    assert Config.ACTIVE_MODEL == "gpt-x"


def test_load_runtime_state_missing_resets(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "x")
    monkeypatch.setattr(Config, "ACTIVE_MODEL", "y")
    Config.load_runtime_state()
    assert Config.ACTIVE_PROVIDER is None
    assert Config.ACTIVE_MODEL is None


def test_validate_requires_provider(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, preset_names=())
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", None)
    with pytest.raises(ValueError, match="provider"):
        Config.validate()


def test_validate_requires_api_key(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _write_provider(tmp_path, "bare", "", "https://a.example.com", ["m1"], "m1")
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "bare")
    with pytest.raises(ValueError, match="API key"):
        Config.validate()


def test_validate_requires_model(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _write_provider(tmp_path, "bare", "k1", "https://a.example.com", [], "")
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "bare")
    with pytest.raises(ValueError, match="模型"):
        Config.validate()


def test_validate_named_provider_ok(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _write_provider(tmp_path, "deepseek", "k1", "https://a.example.com", ["m1"], "m1")
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "deepseek")
    Config.validate()


def test_validate_invalid_url_raises(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _write_provider(tmp_path, "bad", "k1", "not-a-url", ["m1"], "m1")
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "bad")
    with pytest.raises(ValueError, match="API 地址"):
        Config.validate()


def test_variants_and_switching(monkeypatch):
    monkeypatch.setattr(Config, "ACTIVE_VARIANT", "medium")
    assert Config.active_variant_params() == {"reasoning_effort": "medium"}
    Config.set_variant("high")
    assert Config.ACTIVE_VARIANT == "high"
    assert Config.active_variant_params() == {"reasoning_effort": "high"}
    with pytest.raises(ValueError):
        Config.set_variant("nope")
    assert Config.ACTIVE_VARIANT == "high"


def test_context_window_known_model():
    assert Config.context_window("gpt-4o") == 128000
    assert Config.context_window("deepseek-chat") == 128000


def test_context_window_default_fallback():
    assert Config.context_window("unknown-model") == Config.DEFAULT_CONTEXT_WINDOW


def test_modes_and_switching(monkeypatch):
    monkeypatch.setattr(Config, "ACTIVE_MODE", "auto")
    assert set(Config.modes()) == {"plan", "build", "auto"}
    assert Config.mode_descriptions()["plan"]
    Config.set_mode("plan")
    assert Config.ACTIVE_MODE == "plan"
    Config.set_mode("BUILD")
    assert Config.ACTIVE_MODE == "build"
    with pytest.raises(ValueError, match="模式"):
        Config.set_mode("nope")
    assert Config.ACTIVE_MODE == "build"


def test_mode_instructions(monkeypatch):
    monkeypatch.setattr(Config, "ACTIVE_MODE", "plan")
    instructions = Config.mode_instructions("plan")
    assert "Plan" in instructions
    assert "Plan" in Config.mode_instructions(Config.ACTIVE_MODE)
    assert Config.mode_instructions("nope") == ""


def test_mode_tool_exclusions():
    assert Config.mode_tool_exclusions("plan") == {"shell", "write", "edit"}
    assert Config.mode_tool_exclusions("build") == set()
    assert Config.mode_tool_exclusions("auto") == set()
    assert Config.mode_tool_exclusions("nope") == set()
