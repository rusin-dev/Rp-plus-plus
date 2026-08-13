from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from ..config import Config

_FILE_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_CONSOLE_FORMAT = "%(levelname)s | %(name)s | %(message)s"


def get_logger(name: str, include_console: bool = True) -> logging.Logger:
    """获取配置好的 logger：文件滚动日志 + 可选控制台输出。"""
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(Config.LOG_LEVEL.upper())
    logger.propagate = False

    Config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        Config.LOG_DIR / "log.log",
        maxBytes=4 * 1024 * 1024,  # 4 MB
        backupCount=5,
        encoding=Config.LOG_ENCODING,
    )
    file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
    logger.addHandler(file_handler)

    if include_console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
        logger.addHandler(console_handler)

    return logger
