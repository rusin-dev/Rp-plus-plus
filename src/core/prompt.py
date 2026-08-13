from __future__ import annotations

from pathlib import Path

from ..config import Config

_SUPPORTED_EXTENSIONS = {".md", ".txt"}


def get_prompt(filename: str, level: str = "general") -> str:
    """读取指定 level 目录下的提示词文件，返回其内容。"""
    data_dir = (Config.DATA_DIR / level).resolve()
    if not data_dir.is_dir():
        raise FileNotFoundError(f"提示词目录不存在: {data_dir}")

    target = (data_dir / filename).resolve()
    if target.suffix not in _SUPPORTED_EXTENSIONS or data_dir not in target.parents:
        raise ValueError(f"非法的提示词文件路径: {filename}")

    return target.read_text(encoding="utf-8")


def list_prompts(level: str = "general") -> list[Path]:
    """列出指定 level 目录下所有可用的提示词文件。"""
    data_dir = Config.DATA_DIR / level
    if not data_dir.is_dir():
        return []
    return sorted(
        p for p in data_dir.iterdir() if p.suffix in _SUPPORTED_EXTENSIONS
    )
