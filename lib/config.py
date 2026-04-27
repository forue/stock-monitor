#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置管理模块 - 加载/热加载/默认配置 / .env 环境变量
"""

import json
import os
from pathlib import Path
from lib.logger import log

# 默认配置
DEFAULT_CONFIG = {
    'stocks': [],
    'l1_thresholds': {
        'price_change_rate': 0.007,
        'price_change_pct': 2.5,
        'volume_ratio': 4.5,
        'amplitude': 0.02,
    },
    'l2_thresholds': {
        'price_change_pct': 3.0,
        'volume_ratio': 5.0,
    },
    'time_strategy': {
        'intervals': {
            'high_volatility': 2,
            'normal': 5,
            'off_hours': 300,
        }
    },
    'notification': {'enabled': True, 'channel': 'qqbot_c2c'},
}

# 配置文件修改时间缓存
_config_mtime = 0


def load_config(config_path: Path) -> dict:
    """加载 JSON 配置文件，失败时返回默认配置"""
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        log(f"配置加载成功：{config_path}")
        return config
    except Exception as e:
        log(f"配置加载失败：{e}，使用默认配置", level="ERROR")
        return DEFAULT_CONFIG.copy()


def reload_config_if_changed(config_path: Path, current_config: dict) -> dict:
    """检测配置文件变更，自动热加载"""
    global _config_mtime
    try:
        mtime = config_path.stat().st_mtime
        if mtime != _config_mtime:
            _config_mtime = mtime
            new_config = load_config(config_path)
            if new_config.get('stocks'):
                log("配置文件已变更，热加载成功")
                return new_config
            else:
                log("新配置无效（stocks 为空），保持当前配置", level="WARNING")
    except Exception as e:
        log(f"检测配置变更失败：{e}", level="WARNING")
    return current_config


def init_config_mtime(config_path: Path):
    """初始化配置文件修改时间记录"""
    global _config_mtime
    try:
        _config_mtime = config_path.stat().st_mtime
    except Exception:
        pass


# ============ .env 环境变量加载 ============

def load_env_file(env_path: Path = None):
    """
    从 .env 文件加载环境变量（仅设置未定义变量，已设置的不覆盖）

    用法：在程序入口（monitor-daemon.py）调用一次即可
    """
    if env_path is None:
        env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return

    try:
        for line in env_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, sep, val = line.partition('=')
            key = key.strip()
            if not key or not sep:
                continue
            val = val.strip().strip('"').strip("'")
            if key not in os.environ:
                os.environ[key] = val
        log(f".env 加载成功：{env_path}")
    except Exception as e:
        log(f".env 加载失败：{e}", level="WARNING")
