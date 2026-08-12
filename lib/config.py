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
        'price_change_rate': 0.01,
        'price_change_pct': 2.5,
        'volume_ratio': 2.0,
        'amplitude': 0.02,
        'min_conditions_required': 2,
        'min_consecutive_hits': 2,
        'volume_ratio_by_session': {
            'opening': 3.0, 'morning': 2.0, 'afternoon': 2.0, 'closing': 2.5,
        },
    },
    'l2_thresholds': {
        'price_change_pct': 3.0,
        'volume_ratio': 3.0,
        'extreme_price_change_pct': 5.0,
        'extreme_volume_ratio': 6.0,
        'min_price_for_volume': 1.0,
        'min_consecutive_hits': 2,
        'volume_ratio_by_session': {
            'opening': 5.0, 'morning': 3.0, 'afternoon': 3.0, 'closing': 4.0,
        },
    },
    'escalation': {
        'cooldown_seconds': 180,
        'trend_deviation': 0.02,
        'reversal_deviation': 0.015,
        'time_decay_seconds': 1800,
        'time_decay_deviation': 0.01,
        'extreme_pct_threshold': 6.0,
        'max_daily_normal': 8,
        'max_daily_total': 15,
    },
    'time_strategy': {
        'intervals': {
            'high_volatility': 3,
            'normal': 8,
            'off_hours': 300,
        }
    },
    'tech_analysis_defaults': {
        'enabled': False,
        'ma_fast': 8,
        'ma_slow': 20,
        'ma_filter': 60,
        'ma_filter_pct': 0.80,
        'rsi_period': 14,
        'rsi_max': 70,
        'check_interval': 300,
        'min_signal_interval': 3600,
        'max_daily_signals': 3,
    },
    'notification': {
        'enabled': True,
        'channel': 'qqbot_c2c',
        'min_alert_interval': 600,
        'max_daily_alerts_per_stock': 8,
    },
    'real_time_features': {
        'fund_flow': {
            'enabled': False,
            'check_interval': 300,
            'net_inflow_th': 1000000,
            'net_outflow_th': -1000000,
            'ratio_th': 0.05,
        },
        'order_book': {
            'enabled': False,
            'check_interval': 120,
            'vi_ratio_high': 0.6,
            'vi_ratio_low': -0.6,
            'seal_qty_th': 50000,
        },
        'divergence': {
            'enabled': False,
            'check_interval': 300,
            'window': 5,
        },
    },
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
