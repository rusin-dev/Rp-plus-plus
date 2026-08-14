from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_FROZEN = (
    bool(getattr(sys, "frozen", False))
    or "__compiled__" in globals()
    or hasattr(sys, "_MEIPASS")
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
    """显式加载 .env：打包后从 exe 所在目录读取，源码运行时从项目根读取。

    修复 Nuitka 打包后 .env 不被识别的问题——默认 load_dotenv() 从进程
    当前工作目录（CWD）向上查找，而打包产物的 CWD 往往不是 exe 所在目录。
    """
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
    key_env: str
    url_env: str
    models_env: str = ""


class Config:
    # 通用设置
    ROOT_DIR = _resolve_root()

    # 兼容旧版单供应商配置（未配置 PROVIDER_* 时作为 fallback）
    CUSTOM_API_KEY = os.getenv("CUSTOM_API_KEY")
    CUSTOM_API_URL = os.getenv("CUSTOM_API_URL", "https://api.deepseek.com/v1")
    RP_MODEL = os.getenv("RP_MODEL", "deepseek-v4-flash")

    # 当前选择的供应商与模型（运行时可通过 /connect /models 修改）
    ACTIVE_PROVIDER: str | None = os.getenv("PROVIDER")
    ACTIVE_MODEL: str | None = os.getenv("MODEL")

    # 思考强度（fast / default / deep），运行时可通过 /variants 修改
    ACTIVE_VARIANT: str = os.getenv("RP_VARIANT", "default")
    VARIANT_PARAMS: dict[str, dict] = {
        "fast": {"temperature": 0.9},
        "default": {},
        "deep": {"temperature": 0.1},
    }
    VARIANT_DESCRIPTIONS: dict[str, str] = {
        "fast": "快速响应，适合简单任务",
        "default": "默认平衡模式",
        "deep": "深度思考，适合复杂任务",
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

    # 会话存储目录
    SESSION_DIR = _get_path("SESSION_DIR", ROOT_DIR / ".rp" / "sessions")

    # 终端输出选项
    RICH_COLOR_SYSTEM = os.getenv("RICH_COLOR_SYSTEM", "auto")
    RICH_THEME = os.getenv("RICH_THEME")
    TAB_SIZE = _get_int("TAB_SIZE", 8)

    # 网页搜索后端（bing / ddg / auto）
    SEARCH_BACKEND = os.getenv("SEARCH_BACKEND", "bing")

    # ---------- Provider ----------

    @classmethod
    def providers(cls) -> dict[str, Provider]:
        """解析环境变量中的全部 provider；无 PROVIDER_* 时回退到旧版单供应商。"""
        found: dict[str, Provider] = {}
        for key in os.environ:
            if key.startswith("PROVIDER_") and key.endswith("_API_KEY"):
                name = key[len("PROVIDER_") : -len("_API_KEY")].lower()
                if name and name not in found:
                    found[name] = cls._provider_from_env(name)
        if found:
            return found
        legacy = cls._legacy_provider()
        return {"custom": legacy} if legacy is not None else {}

    @classmethod
    def get_provider(cls, name: str) -> Provider | None:
        return cls.providers().get(name.lower())

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
        return cls.RP_MODEL or ""

    @classmethod
    def set_provider(cls, name: str) -> Provider:
        """切换到指定 provider，并重置到其默认模型。"""
        provider = cls.get_provider(name)
        if provider is None:
            raise ValueError(f"未知的 provider：{name}，可用：{', '.join(cls.providers()) or '无'}")
        cls.ACTIVE_PROVIDER = provider.name
        cls.CUSTOM_API_KEY = provider.api_key
        cls.CUSTOM_API_URL = provider.api_url
        cls.ACTIVE_MODEL = provider.default_model
        cls.RP_MODEL = provider.default_model
        return provider

    @classmethod
    def set_model(cls, model: str) -> None:
        """切换当前 provider 的模型。"""
        provider = cls.active_provider()
        if provider is not None and provider.models and model not in provider.models:
            raise ValueError(
                f"模型 {model} 不在 {provider.name} 的可用列表：{', '.join(provider.models)}"
            )
        cls.ACTIVE_MODEL = model
        cls.RP_MODEL = model

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
    def _provider_from_env(cls, name: str) -> Provider:
        up = name.upper()
        api_key = os.getenv(f"PROVIDER_{up}_API_KEY", "").strip()
        api_url = os.getenv(f"PROVIDER_{up}_API_URL", "").strip()
        models = [m.strip() for m in os.getenv(f"PROVIDER_{up}_MODELS", "").split(",") if m.strip()]
        default_model = os.getenv(f"PROVIDER_{up}_DEFAULT_MODEL", "").strip()
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
            key_env=f"PROVIDER_{up}_API_KEY",
            url_env=f"PROVIDER_{up}_API_URL",
            models_env=f"PROVIDER_{up}_MODELS",
        )

    @classmethod
    def _legacy_provider(cls) -> Provider | None:
        api_key = cls.CUSTOM_API_KEY or ""
        api_url = cls.CUSTOM_API_URL or ""
        model = cls.RP_MODEL or ""
        if not api_key:
            return None
        return Provider(
            name="custom",
            api_key=api_key,
            api_url=api_url,
            models=[model] if model else [],
            default_model=model,
            key_env="CUSTOM_API_KEY",
            url_env="CUSTOM_API_URL",
        )

    @classmethod
    def validate(cls) -> None:
        """运行时校验关键配置，未配置时抛出明确的错误信息。"""
        provider = cls.active_provider()
        if provider is None:
            raise ValueError(
                "未配置任何 API provider，请设置 CUSTOM_API_KEY 或 "
                "PROVIDER_<NAME>_API_KEY（参考 .env.example）"
            )
        if not provider.api_key:
            raise ValueError(
                f"缺少 {provider.name} 的 API key（{provider.key_env}），请在 .env 中配置"
            )
        if not provider.api_url.startswith(("http://", "https://")):
            raise ValueError(f"非法的 {provider.url_env}: {provider.api_url}")
        if not provider.default_model and not provider.models:
            raise ValueError(
                f"provider {provider.name} 未配置任何模型"
                f"（{provider.models_env or 'CUSTOM_API_MODELS'}）"
            )
