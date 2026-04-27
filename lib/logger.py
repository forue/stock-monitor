#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日志模块 - 初始化/全局 log 函数
"""

import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logger(log_file: Path) -> logging.Logger:
    """初始化日志系统（RotatingFileHandler + 控制台输出）"""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    _logger = logging.getLogger("stock-monitor")
    _logger.setLevel(logging.INFO)
    if not _logger.handlers:
        fmt = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s')
        # 文件 handler
        file_handler = RotatingFileHandler(
            log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'
        )
        file_handler.setFormatter(fmt)
        _logger.addHandler(file_handler)
        # 控制台 handler (Docker 环境需要 stdout 输出)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(fmt)
        _logger.addHandler(console_handler)
    return _logger


# 延迟初始化：由 monitor-daemon.py 调用 setup_logger 后赋值
_logger: logging.Logger = None


def init_logger(log_file: Path):
    """初始化全局 logger 实例"""
    global _logger
    _logger = setup_logger(log_file)


def log(message: str, level: str = "INFO"):
    """日志记录（兼容接口）"""
    if _logger is None:
        print(f"[{level}] {message}")
        return
    level_map = {
        "DEBUG": logging.DEBUG, "INFO": logging.INFO, "WARNING": logging.WARNING,
        "ERROR": logging.ERROR, "CRITICAL": logging.CRITICAL,
    }
    _logger.log(level_map.get(level, logging.INFO), message)
