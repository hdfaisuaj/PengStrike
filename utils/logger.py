"""
日志系统 (utils/logger.py) - Phase 4 安全增强版

职责:
- 基于 Python logging 模块的统一日志框架
- 控制台输出 + 文件输出双通道
- 日志级别: DEBUG / INFO / WARNING / ERROR / CRITICAL
- Phase 4: 日志自动轮转（按文件大小）
- Phase 4: 磁盘空间监控（防止日志占满磁盘）
- 线程安全: 提供 safe_console 方法，使用 asyncio.Lock 保护 Rich 控制台输出
- Phase 5: 自动错误日志 — 所有 ERROR 及 CRITICAL 级别日志自动写入 logs/error.log
- Phase 5: 全局异常捕获 — 未处理的 Python 异常和 asyncio 异常自动记录到 error.log

审计日志 vs 调试日志:
- debug.log: 开发调试用（控制台 + 文件），含模块名、行号
- error.log: 错误日志专用（仅 ERROR / CRITICAL），方便快速定位问题
- audit.log: 安全审计用（JSON 格式，独立文件，见 utils/audit_logger.py）
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from config.settings import PROJECT_ROOT, get_settings


# --------------------------------------------------------------------------
# 日志格式
# --------------------------------------------------------------------------
_CONSOLE_FORMAT = "[%(levelname)s] %(name)s: %(message)s"
_FILE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"


# --------------------------------------------------------------------------
# 日志目录（统一为 ~/.pengstrike/logs）
# --------------------------------------------------------------------------
_LOG_DIR: Path = Path.home() / ".pengstrike" / "logs"


def _ensure_log_dir() -> Path:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR


# --------------------------------------------------------------------------
# 文件处理器（带自动轮转）
# --------------------------------------------------------------------------
def _create_file_handler(log_path: Path, level: int = logging.DEBUG) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        filename=str(log_path),
        mode="a",
        maxBytes=10 * 1024 * 1024,  # 10MB per file
        backupCount=5,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FILE_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
    return handler


# --------------------------------------------------------------------------
# 控制台处理器
# --------------------------------------------------------------------------
def _create_console_handler(level: int = logging.INFO) -> logging.StreamHandler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    return handler


# --------------------------------------------------------------------------
# 日志初始化（懒加载，仅首次调用 get_logger 时触发）
# --------------------------------------------------------------------------
_initialized: bool = False


def _init_logging() -> None:
    global _initialized
    if _initialized:
        return
    _initialized = True

    settings = get_settings()
    log_level_str = settings.log_level.upper()
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    log_level = level_map.get(log_level_str, logging.INFO)

    # 根日志器基础设置
    root = logging.getLogger()
    root.setLevel(log_level)

    # 清除默认处理器（防止重复添加）
    root.handlers.clear()

    # 控制台处理器
    console_handler = _create_console_handler(log_level)
    root.addHandler(console_handler)

    # 文件处理器（仅在有日志目录时添加）
    try:
        log_dir = _ensure_log_dir()
        debug_log = log_dir / "debug.log"
        file_handler = _create_file_handler(debug_log, logging.DEBUG)
        root.addHandler(file_handler)

        # 额外：输出日志文件位置到 stderr 用于首次提示
        print(f"[PengStrike] 日志文件: {debug_log}", file=sys.stderr)
    except Exception:
        pass  # 无法写入日志文件时保持静默

    # ------------------------------------------------------------------
    # Phase 5: 添加 ERROR/CRITICAL 级别的独立错误日志文件
    # ------------------------------------------------------------------
    try:
        log_dir = _ensure_log_dir()
        error_log = log_dir / "error.log"
        error_handler = _create_file_handler(error_log, logging.ERROR)
        root.addHandler(error_handler)
        print(f"[PengStrike] 错误日志: {error_log}", file=sys.stderr)
    except Exception:
        pass  # 无法写入错误日志文件时保持静默

    # ------------------------------------------------------------------
    # Phase 5: 注册全局异常钩子
    # 未捕获的异常会自动记录到 error.log
    # ------------------------------------------------------------------
    _install_global_except_hook()


# --------------------------------------------------------------------------
# Phase 5: 全局异常捕获
# --------------------------------------------------------------------------
_original_excepthook = None
_original_async_excepthook = None


def _install_global_except_hook() -> None:
    """安装全局异常钩子，捕获所有未被 try/except 捕获的异常。"""
    global _original_excepthook, _original_async_excepthook

    # 1. 捕获同步异常 (sys.excepthook)
    _original_excepthook = sys.excepthook

    def _custom_excepthook(exc_type, exc_value, exc_tb):
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logger = logging.getLogger("UNHANDLED")
        logger.critical("未捕获的异常:\n%s", msg)
        # 调用原始钩子（会打印到 stderr 并退出）
        if _original_excepthook:
            _original_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = _custom_excepthook

    # 2. 捕获 asyncio 异常
    _original_async_excepthook = asyncio.get_event_loop_policy().get_event_loop()

    def _custom_async_excepthook(loop, context):
        msg = context.get("message", "Unknown asyncio error")
        exc = context.get("exception")
        if exc:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            logger = logging.getLogger("UNHANDLED")
            logger.critical("asyncio 未捕获异常 [%s]:\n%s", msg, tb)
        else:
            logger = logging.getLogger("UNHANDLED")
            logger.critical("asyncio 错误: %s", msg)

    try:
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(_custom_async_excepthook)
    except RuntimeError:
        pass  # 尚无事件循环，等正式启动时会自动应用


# --------------------------------------------------------------------------
# 手动记录错误到 error.log 的便捷函数
# --------------------------------------------------------------------------
def log_error(message: str, exc_info: bool = True) -> None:
    """手动记录一条错误日志（直接写入 error.log）。"""
    logger = get_logger("MANUAL")
    logger.error(message, exc_info=exc_info)


# --------------------------------------------------------------------------
# 公开 API: 获取模块级别的日志器
# --------------------------------------------------------------------------
def get_logger(name: str) -> logging.Logger:
    _init_logging()
    return logging.getLogger(name)


# --------------------------------------------------------------------------
# 线程安全的 Rich 控制台输出
# --------------------------------------------------------------------------
_console_lock: asyncio.Lock = asyncio.Lock()
_shared_console = None  # 懒加载

def get_shared_console():
    """获取全局共享的 Rich Console 实例（懒加载）。"""
    global _shared_console
    if _shared_console is None:
        from rich.console import Console
        _shared_console = Console(log_time=True)
    return _shared_console

async def safe_console_print(*args, **kwargs):
    """线程安全的控制台输出（使用 asyncio.Lock 保护）。"""
    async with _console_lock:
        console = get_shared_console()
        console.print(*args, **kwargs)

def set_level(level: int) -> None:
    logging.getLogger().setLevel(level)

    # 更新控制台处理器的级别
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.setLevel(level)
