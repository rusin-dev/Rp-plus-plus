import os

import pytest

from src.config import Config


def _clear_provider_env(monkeypatch) -> None:
    for key in list(os.environ):
        if key.startswith("PROVIDER_"):
            monkeypatch.delenv(key, raising=False)


def _set_provider_env(
    monkeypatch,
    name: str,
    api_key: str,
    api_url: str,
    models: str = "",
    default: str = "",
) -> None:
    up = name.upper()
    monkeypatch.setenv(f"PROVIDER_{up}_API_KEY", api_key)
    monkeypatch.setenv(f"PROVIDER_{up}_API_URL", api_url)
    if models:
        monkeypatch.setenv(f"PROVIDER_{up}_MODELS", models)
    if default:
        monkeypatch.setenv(f"PROVIDER_{up}_DEFAULT_MODEL", default)


def test_providers_parses_named_providers(monkeypatch):
    _clear_provider_env(monkeypatch)
    _set_provider_env(
        monkeypatch,
        "deepseek",
        "k1",
        "https://api.deepseek.com",
        "deepseek-chat,deepseek-reasoner",
        "deepseek-chat",
    )
    _set_provider_env(monkeypatch, "openai", "k2", "https://api.openai.com/v1", "gpt-4o")
    providers = Config.providers()
    assert set(providers) == {"deepseek", "openai"}
    assert providers["deepseek"].api_key == "k1"
    assert providers["deepseek"].models == ["deepseek-chat", "deepseek-reasoner"]
    assert providers["deepseek"].default_model == "deepseek-chat"
    assert providers["openai"].default_model == "gpt-4o"


def test_providers_case_insensitive_name(monkeypatch):
    _clear_provider_env(monkeypatch)
    _set_provider_env(monkeypatch, "OpenAI", "k", "https://api.openai.com/v1", "gpt-4o")
    providers = Config.providers()
    assert set(providers) == {"openai"}
    assert Config.get_provider("OPENAI") is not None


def test_legacy_provider_fallback(monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setattr(Config, "CUSTOM_API_KEY", "legacy-key")
    monkeypatch.setattr(Config, "CUSTOM_API_URL", "https://legacy.example.com")
    monkeypatch.setattr(Config, "RP_MODEL", "legacy-model")
    providers = Config.providers()
    assert set(providers) == {"custom"}
    assert providers["custom"].default_model == "legacy-model"
    assert providers["custom"].key_env == "CUSTOM_API_KEY"


def test_no_provider_returns_empty(monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setattr(Config, "CUSTOM_API_KEY", None)
    assert Config.providers() == {}
    assert Config.active_provider() is None
    assert Config.active_model() == Config.RP_MODEL


def test_active_provider_uses_selection(monkeypatch):
    _clear_provider_env(monkeypatch)
    _set_provider_env(monkeypatch, "deepseek", "k1", "https://a.example.com", "m1", "m1")
    _set_provider_env(monkeypatch, "openai", "k2", "https://b.example.com", "m2", "m2")
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "openai")
    provider = Config.active_provider()
    assert provider is not None
    assert provider.name == "openai"


def test_set_provider_switches_credentials(monkeypatch):
    _clear_provider_env(monkeypatch)
    _set_provider_env(monkeypatch, "deepseek", "k1", "https://a.example.com", "d1", "d1")
    _set_provider_env(monkeypatch, "openai", "k2", "https://b.example.com", "o1,o2", "o2")
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "deepseek")
    Config.set_provider("openai")
    assert Config.ACTIVE_PROVIDER == "openai"
    assert Config.CUSTOM_API_KEY == "k2"
    assert Config.CUSTOM_API_URL == "https://b.example.com"
    assert Config.ACTIVE_MODEL == "o2"
    assert Config.active_model() == "o2"


def test_set_provider_unknown_raises(monkeypatch):
    _clear_provider_env(monkeypatch)
    _set_provider_env(monkeypatch, "deepseek", "k1", "https://a.example.com", "d1", "d1")
    with pytest.raises(ValueError):
        Config.set_provider("nope")


def test_set_model_switches_and_validates(monkeypatch):
    _clear_provider_env(monkeypatch)
    _set_provider_env(monkeypatch, "deepseek", "k1", "https://a.example.com", "m1,m2", "m1")
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "deepseek")
    Config.set_model("m2")
    assert Config.ACTIVE_MODEL == "m2"
    assert Config.active_model() == "m2"
    with pytest.raises(ValueError):
        Config.set_model("nope")


def test_set_model_allows_any_when_models_empty(monkeypatch):
    _clear_provider_env(monkeypatch)
    _set_provider_env(monkeypatch, "deepseek", "k1", "https://a.example.com")
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "deepseek")
    Config.set_model("anything")
    assert Config.active_model() == "anything"


def test_active_model_prefers_env_override(monkeypatch):
    _clear_provider_env(monkeypatch)
    _set_provider_env(monkeypatch, "deepseek", "k1", "https://a.example.com", "m1", "m1")
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "deepseek")
    monkeypatch.setattr(Config, "ACTIVE_MODEL", "override")
    assert Config.active_model() == "override"


def test_variants_and_switching(monkeypatch):
    monkeypatch.setattr(Config, "ACTIVE_VARIANT", "default")
    assert Config.active_variant_params() == {}
    Config.set_variant("deep")
    assert Config.ACTIVE_VARIANT == "deep"
    assert Config.active_variant_params() == {"temperature": 0.1}
    with pytest.raises(ValueError):
        Config.set_variant("nope")
    assert Config.ACTIVE_VARIANT == "deep"


def test_validate_requires_model(monkeypatch):
    _clear_provider_env(monkeypatch)
    _set_provider_env(monkeypatch, "bare", "k1", "https://a.example.com")
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "bare")
    with pytest.raises(ValueError, match="模型"):
        Config.validate()


def test_validate_named_provider_ok(monkeypatch):
    _clear_provider_env(monkeypatch)
    _set_provider_env(monkeypatch, "deepseek", "k1", "https://a.example.com", "m1", "m1")
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "deepseek")
    Config.validate()


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
    assert Config.mode_tool_exclusions("plan") == {"shell", "write"}
    assert Config.mode_tool_exclusions("build") == set()
    assert Config.mode_tool_exclusions("auto") == set()
    assert Config.mode_tool_exclusions("nope") == set()
