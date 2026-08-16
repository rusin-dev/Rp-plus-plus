from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_FROZEN = (
    bool(getattr(sys, "frozen", False)) or "__compiled__" in globals() or hasattr(sys, "_MEIPASS")
)


def _bundle_base() -> Path:
    """打包运行时的资源根目录：PyInstaller 用 _MEIPASS，Nuitka 用可执行文件所在目录。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(sys.executable).parent


def _executable_dir() -> Path:
    """可执行文件所在目录；打包后 .env / 日志 / 会话等外部文件均锚定到此。

    注意：不能用 _bundle_base()，因为 PyInstaller onefile 下 _MEIPASS 是临时解压目录，
    而 .env 等运行时外部文件始终位于 exe 本体旁边。
    """
    return Path(sys.executable).resolve().parent


def _resolve_root() -> Path:
    """项目根目录；打包运行时锚定到可执行文件所在目录（exe 旁），源码运行时为项目根。"""
    if _FROZEN:
        return _executable_dir()
    return Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """显式加载 .env：打包后从 exe 所在目录读取，源码运行时从项目根读取。"""
    env_path = _executable_dir() / ".env" if _FROZEN else _resolve_root() / ".env"
    load_dotenv(env_path)


_load_dotenv()


def _resolve_data_dir() -> Path:
    """提示词数据目录；打包运行时读取捆绑进可执行程序的数据。"""
    if _FROZEN:
        return _bundle_base() / "src" / "data"

    source = _resolve_root() / "src" / "data"
    if source.is_dir():
        return source
    nearby = Path(sys.executable).parent / "src" / "data"
    if nearby.is_dir():
        return nearby
    return source


def _resolve_provider_dir() -> Path:
    """用户生成的 provider 配置目录（含 api_key 的 JSON 文件）。

    源码运行时直接写入 src/data/providers/；打包运行时捆绑目录只读，
    改写到 exe 旁的 src/data/providers/（可写）。
    """
    if _FROZEN:
        return _executable_dir() / "src" / "data" / "providers"
    return _resolve_root() / "src" / "data" / "providers"


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    return Path(raw) if raw else default


@dataclass(slots=True)
class Provider:
    """一个可切换的 API 供应商及其可用模型。"""

    name: str
    api_key: str
    api_url: str
    models: list[str]
    default_model: str
    config_file: str = ""


class Config:
    # 通用设置
    ROOT_DIR = _resolve_root()

    # 当前选择的供应商与模型（运行时可通过 /connect /models 修改）
    ACTIVE_PROVIDER: str | None = None
    ACTIVE_MODEL: str | None = None

    # 思考强度（low / medium / high / max），运行时可通过 /variants 修改，
    # 档位以 reasoning_effort 形式传入实际的 API 调用
    ACTIVE_VARIANT: str = os.getenv("RP_VARIANT", "medium")
    VARIANT_PARAMS: dict[str, dict] = {
        "low": {"reasoning_effort": "low"},
        "medium": {"reasoning_effort": "medium"},
        "high": {"reasoning_effort": "high"},
        "max": {"reasoning_effort": "max"},
    }
    VARIANT_DESCRIPTIONS: dict[str, str] = {
        "low": "轻度思考，响应最快，适合简单任务",
        "medium": "默认平衡模式",
        "high": "深度思考，适合复杂任务",
        "max": "最高强度思考，适合极复杂任务",
    }

    # 工作模式（plan / build / auto），运行时可通过 /mode 修改
    ACTIVE_MODE: str = os.getenv("RP_MODE", "auto")
    MODES: dict[str, dict] = {
        "plan": {
            "description": "仅规划，不修改任何文件",
            "instructions": (
                "## 当前模式：Plan（仅规划）\n"
                "- 你的任务是分析需求并制定清晰的实施计划，不要直接实现。\n"
                "- 输出 Markdown 计划：目标、具体步骤、涉及或待创建的文件、潜在风险与注意事项。\n"
                "- 禁止修改任何文件、禁止执行 shell 命令或运行代码（相关工具已被禁用）。\n"
                "- 只能进行只读探索（读取文件、搜索代码、查询资料）来支撑你的规划。"
            ),
        },
        "build": {
            "description": "直接实现需求",
            "instructions": (
                "## 当前模式：Build（实现）\n"
                "- 直接动手实现用户需求：阅读相关代码、编辑文件、执行命令进行验证。\n"
                "- 完成后用简短列表总结本次改动。"
            ),
        },
        "auto": {
            "description": "自动规划并实现",
            "instructions": (
                "## 当前模式：Auto（自动规划与实现）\n"
                "- 自主完成任务：先给出简短的执行计划，然后立即实现（阅读代码、编辑文件、运行命令验证）。\n"
                "- 除非遇到关键歧义，不要反复向用户确认。\n"
                "- 完成后用简短列表总结本次改动。"
            ),
        },
    }
    # 各模式下禁用的工具名（防御性限制，plan 模式不允许任何改动）
    MODE_TOOL_EXCLUSIONS: dict[str, set[str]] = {
        "plan": {"shell", "write", "edit"},
    }

    # 常见模型的上下文窗口（tokens）；未收录的模型用默认值
    CONTEXT_WINDOWS: dict[str, int] = {
        "deepseek-chat": 128000,
        "deepseek-reasoner": 128000,
        "deepseek-v4-flash": 128000,
        "gpt-4o": 128000,
        "gpt-4o-mini": 128000,
        "gpt-4-turbo": 128000,
        "gpt-4": 8192,
        "gpt-3.5-turbo": 16385,
        "claude-opus-4-1": 200000,
        "claude-sonnet-4-5": 200000,
        "claude-haiku-4-5": 200000,
    }
    DEFAULT_CONTEXT_WINDOW = 128000

    # 日志存储
    LOG_DIR = _get_path("LOG_DIR", ROOT_DIR / "log")
    LOG_ENCODING = os.getenv("LOG_ENCODING", "utf-8")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # 提示词数据目录
    DATA_DIR = _resolve_data_dir()

    # provider 预设目录（只读模板，不含 api_key）与用户配置目录（含 api_key）
    PRESET_DIR = DATA_DIR / "providers" / "preset"
    PROVIDER_DIR = _resolve_provider_dir()

    # 运行时状态文件（当前选中的 provider / model，跨会话持久化）
    RUNTIME_STATE_FILE = ROOT_DIR / ".rp" / "config.json"

    # 会话存储目录
    SESSION_DIR = _get_path("SESSION_DIR", ROOT_DIR / ".rp" / "sessions")

    # 会话自动 git：启动时在 ROOT_DIR 初始化仓库 + 每轮对话后自动提交
    # （设置 RP_AUTO_GIT=0 可关闭）
    AUTO_GIT = os.getenv("RP_AUTO_GIT", "1") != "0"

    # 终端输出选项
    RICH_COLOR_SYSTEM = os.getenv("RICH_COLOR_SYSTEM", "auto")
    RICH_THEME = os.getenv("RICH_THEME")
    TAB_SIZE = _get_int("TAB_SIZE", 8)

    # 网页搜索后端（bing / ddg / auto）
    SEARCH_BACKEND = os.getenv("SEARCH_BACKEND", "bing")

    # ---------- Provider ----------

    @classmethod
    def providers(cls) -> dict[str, Provider]:
        """读取 src/data/providers/*.json 中的全部 provider 配置。"""
        found: dict[str, Provider] = {}
        if not cls.PROVIDER_DIR.is_dir():
            return found
        for path in sorted(cls.PROVIDER_DIR.glob("*.json")):
            provider = cls._provider_from_file(path)
            if provider is not None:
                found[provider.name] = provider
        return found

    @classmethod
    def presets(cls) -> dict[str, Provider]:
        """读取 src/data/providers/preset/*.json 中的全部预设模板（不含 api_key）。"""
        found: dict[str, Provider] = {}
        if not cls.PRESET_DIR.is_dir():
            return found
        for path in sorted(cls.PRESET_DIR.glob("*.json")):
            provider = cls._provider_from_file(path)
            if provider is not None:
                found[provider.name] = provider
        return found

    @classmethod
    def get_provider(cls, name: str) -> Provider | None:
        return cls.providers().get(name.lower())

    @classmethod
    def get_preset(cls, name: str) -> Provider | None:
        return cls.presets().get(name.lower())

    @classmethod
    def active_provider(cls) -> Provider | None:
        providers = cls.providers()
        if not providers:
            return None
        name = cls.ACTIVE_PROVIDER
        if not name or name not in providers:
            name = next(iter(providers))
        return providers[name]

    @classmethod
    def active_model(cls) -> str:
        """当前生效的模型名。"""
        if cls.ACTIVE_MODEL:
            return cls.ACTIVE_MODEL
        provider = cls.active_provider()
        if provider is not None:
            if provider.default_model:
                return provider.default_model
            if provider.models:
                return provider.models[0]
        return ""

    @classmethod
    def set_provider(cls, name: str) -> Provider:
        """切换到指定 provider，并重置到其默认模型。"""
        provider = cls.get_provider(name)
        if provider is None:
            raise ValueError(f"未知的 provider：{name}，可用：{', '.join(cls.providers()) or '无'}")
        cls.ACTIVE_PROVIDER = provider.name
        cls.ACTIVE_MODEL = provider.default_model
        cls._save_runtime_state()
        return provider

    @classmethod
    def use_preset(cls, name: str, api_key: str) -> Provider:
        """从预设生成 provider 配置文件（含 api_key）并切换到它。"""
        preset = cls.get_preset(name)
        if preset is None:
            raise ValueError(f"未知的预设：{name}，可用：{', '.join(cls.presets()) or '无'}")
        cls.PROVIDER_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "name": preset.name,
            "api_url": preset.api_url,
            "models": preset.models,
            "default_model": preset.default_model,
            "api_key": api_key,
        }
        path = cls.PROVIDER_DIR / f"{preset.name}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        provider = cls._provider_from_file(path)
        assert provider is not None
        cls.ACTIVE_PROVIDER = provider.name
        cls.ACTIVE_MODEL = provider.default_model
        cls._save_runtime_state()
        return provider

    @classmethod
    def set_api_key(cls, name: str, api_key: str) -> Provider:
        """为指定供应商设置 API Key（明文写入其 JSON 配置文件）。

        已配置的供应商直接更新 Key；未配置但存在预设的供应商从预设生成配置文件，
        并切换为该供应商（首次配置后立即可用）。
        """
        api_key = api_key.strip()
        if not api_key:
            raise ValueError("API Key 不能为空")
        provider = cls.get_provider(name)
        if provider is None:
            preset = cls.get_preset(name)
            if preset is None:
                raise ValueError(
                    f"未知的 provider：{name}，可用：{', '.join(cls.providers()) or '无'}"
                )
            provider = preset
        cls.PROVIDER_DIR.mkdir(parents=True, exist_ok=True)
        path = cls.PROVIDER_DIR / f"{provider.name}.json"
        data = {
            "name": provider.name,
            "api_url": provider.api_url,
            "models": provider.models,
            "default_model": provider.default_model,
            "api_key": api_key,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        updated = cls._provider_from_file(path)
        assert updated is not None
        cls.ACTIVE_PROVIDER = updated.name
        cls.ACTIVE_MODEL = updated.default_model
        cls._save_runtime_state()
        return updated

    @classmethod
    def set_model(cls, model: str) -> None:
        """切换当前 provider 的模型。"""
        provider = cls.active_provider()
        if provider is not None and provider.models and model not in provider.models:
            raise ValueError(
                f"模型 {model} 不在 {provider.name} 的可用列表：{', '.join(provider.models)}"
            )
        cls.ACTIVE_MODEL = model
        cls._save_runtime_state()

    # ---------- 运行时状态 ----------

    @classmethod
    def load_runtime_state(cls) -> None:
        """启动时从 .rp/config.json 恢复当前选中的 provider / model。"""
        path = cls.RUNTIME_STATE_FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        provider = data.get("active_provider")
        model = data.get("active_model")
        cls.ACTIVE_PROVIDER = provider if isinstance(provider, str) else None
        cls.ACTIVE_MODEL = model if isinstance(model, str) else None

    @classmethod
    def _save_runtime_state(cls) -> None:
        cls.RUNTIME_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "active_provider": cls.ACTIVE_PROVIDER,
            "active_model": cls.ACTIVE_MODEL,
        }
        cls.RUNTIME_STATE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---------- Variants ----------

    @classmethod
    def variants(cls) -> dict[str, dict]:
        return cls.VARIANT_PARAMS

    @classmethod
    def variant_descriptions(cls) -> dict[str, str]:
        return cls.VARIANT_DESCRIPTIONS

    @classmethod
    def active_variant_params(cls) -> dict:
        return cls.VARIANT_PARAMS.get(cls.ACTIVE_VARIANT, {})

    @classmethod
    def set_variant(cls, variant: str) -> None:
        if variant not in cls.VARIANT_PARAMS:
            raise ValueError(f"未知的思考强度：{variant}，可用：{', '.join(cls.VARIANT_PARAMS)}")
        cls.ACTIVE_VARIANT = variant

    # ---------- Modes ----------

    @classmethod
    def modes(cls) -> dict[str, dict]:
        return cls.MODES

    @classmethod
    def mode_descriptions(cls) -> dict[str, str]:
        return {name: meta["description"] for name, meta in cls.MODES.items()}

    @classmethod
    def mode_instructions(cls, mode: str) -> str:
        meta = cls.MODES.get(mode)
        return meta["instructions"] if meta else ""

    @classmethod
    def mode_tool_exclusions(cls, mode: str) -> set[str]:
        return cls.MODE_TOOL_EXCLUSIONS.get(mode, set())

    @classmethod
    def set_mode(cls, mode: str) -> None:
        mode = mode.lower()
        if mode not in cls.MODES:
            raise ValueError(f"未知的模式：{mode}，可用：{', '.join(cls.MODES)}")
        cls.ACTIVE_MODE = mode

    @classmethod
    def context_window(cls, model: str) -> int:
        """返回模型对应的上下文窗口大小（tokens）。"""
        return cls.CONTEXT_WINDOWS.get(model, cls.DEFAULT_CONTEXT_WINDOW)

    # ---------- 解析 ----------

    @classmethod
    def _provider_from_file(cls, path: Path) -> Provider | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        name = str(data.get("name") or path.stem).strip().lower()
        api_key = str(data.get("api_key") or "").strip()
        api_url = str(data.get("api_url") or "").strip()
        models = [m.strip() for m in data.get("models", []) if isinstance(m, str) and m.strip()]
        default_model = str(data.get("default_model") or "").strip()
        if default_model and default_model not in models:
            models.insert(0, default_model)
        if not default_model and models:
            default_model = models[0]
        return Provider(
            name=name,
            api_key=api_key,
            api_url=api_url,
            models=models,
            default_model=default_model,
            config_file=str(path),
        )

    @classmethod
    def validate(cls) -> None:
        """运行时校验关键配置，未配置时抛出明确的错误信息。"""
        provider = cls.active_provider()
        if provider is None:
            raise ValueError("未配置任何 API provider，请运行 /connect 使用预设并输入 API Key")
        if not provider.api_key:
            raise ValueError(
                f"缺少 {provider.name} 的 API key（{provider.config_file}），"
                f"请运行 /connect 重新配置"
            )
        if not provider.api_url.startswith(("http://", "https://")):
            raise ValueError(f"非法的 API 地址: {provider.api_url}")
        if not provider.default_model and not provider.models:
            raise ValueError(f"provider {provider.name} 未配置任何模型（{provider.config_file}）")


Config.load_runtime_state()
