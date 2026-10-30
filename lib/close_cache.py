#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
收盘价缓存模块 - 用于精确计算次日涨跌停价

问题背景：
    涨停价 = round(昨收 × (1 ± 10%), 2)
    但最后一次数据采集可能在14:58，此时的current_price不是真正的收盘价

解决方案：
    1. 收盘后(15:00-15:10)额外采集一次数据
    2. 将当日收盘价保存到缓存文件
    3. 次日计算涨跌停价时，优先使用缓存的收盘价
"""

import json
import os
import time
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from lib.logger import log
from lib.trading_calendar import get_prev_trading_day

# 缓存文件路径
CACHE_FILE = Path(__file__).parent.parent / "data" / "close_prices.json"

# 缓存过期天数（保留最近3天）
CACHE_EXPIRE_DAYS = 3

# 内存缓存（避免频繁读文件）
_memory_cache = None
_memory_cache_mtime = 0


def _load_cache() -> dict:
    """加载缓存文件（带内存缓存）"""
    global _memory_cache, _memory_cache_mtime
    
    # 如果文件不存在，返回内存缓存（可能为空）
    if not CACHE_FILE.exists():
        return _memory_cache or {}
    
    try:
        # 检查文件修改时间，避免不必要的读取
        current_mtime = CACHE_FILE.stat().st_mtime
        if _memory_cache is not None and current_mtime == _memory_cache_mtime:
            return _memory_cache
        
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            _memory_cache = json.load(f)
            _memory_cache_mtime = current_mtime
            return _memory_cache
    except (json.JSONDecodeError, IOError) as e:
        log(f"收盘价缓存加载失败: {e}", level="WARNING")
        return _memory_cache or {}


def _save_cache(cache: dict):
    """保存缓存文件"""
    global _memory_cache, _memory_cache_mtime
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        # 更新内存缓存
        _memory_cache = cache
        _memory_cache_mtime = CACHE_FILE.stat().st_mtime
    except IOError as e:
        log(f"收盘价缓存保存失败: {e}", level="WARNING")


def _cleanup_expired(cache: dict) -> dict:
    """清理过期数据"""
    today = date.today()
    cleaned = {}
    
    for code, entries in cache.items():
        valid_entries = {}
        for date_str, data in entries.items():
            try:
                entry_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                if (today - entry_date).days <= CACHE_EXPIRE_DAYS:
                    valid_entries[date_str] = data
            except ValueError:
                continue
        
        if valid_entries:
            cleaned[code] = valid_entries
    
    return cleaned


def save_close_price(stock_code: str, close_price: float, current_price: float = None):
    """
    保存收盘价到缓存
    
    Args:
        stock_code: 股票代码
        close_price: 收盘价（即数据源返回的close_price，可能是昨收）
        current_price: 当前价（收盘时即为当日收盘价）
    """
    # 收盘时 current_price 就是当日收盘价
    actual_close = current_price if current_price else close_price
    
    if not actual_close or actual_close <= 0:
        return
    
    cache = _load_cache()
    today_str = date.today().isoformat()
    
    if stock_code not in cache:
        cache[stock_code] = {}
    
    cache[stock_code][today_str] = {
        'close': round(actual_close, 2),
        'saved_at': time.time(),
    }
    
    # 清理过期数据
    cache = _cleanup_expired(cache)
    
    _save_cache(cache)
    log(f"收盘价已缓存: {stock_code} = {actual_close:.2f}", level="DEBUG")


def get_prev_close(stock_code: str) -> Optional[float]:
    """
    获取前一交易日的收盘价
    
    严格校验：缓存日期必须等于"上一个交易日"才使用，否则返回 None。
    这样可以防止系统漏跑某天后，使用过期的收盘价计算涨跌停价。
    
    例：今天 10-30，上一交易日应为 10-29
        - 缓存有 10-29 → ✅ 使用
        - 缓存只有 10-28（10-29 系统没跑）→ ❌ 返回 None，回退数据源
    
    Returns:
        前收盘价；无有效缓存时返回 None（调用方回退到数据源 close_price）
    """
    cache = _load_cache()
    
    if stock_code not in cache:
        return None
    
    entries = cache[stock_code]
    expected_date = get_prev_trading_day(date.today())
    expected_str = expected_date.isoformat()
    
    data = entries.get(expected_str)
    if not data:
        # 缓存中缺少上一交易日的数据（可能系统漏跑）
        available = sorted(entries.keys())
        log(f"缓存缺少上一交易日({expected_str})数据: {stock_code} "
            f"已有[{','.join(available)}]，回退数据源", level="DEBUG")
        return None
    
    close = data.get('close')
    if close:
        log(f"使用缓存收盘价: {stock_code} = {close:.2f} ({expected_str})", level="DEBUG")
    
    return close


def clear_cache():
    """清空缓存（调试用）"""
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()
        log("收盘价缓存已清空")
