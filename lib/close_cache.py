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

# 缓存文件路径
CACHE_FILE = Path(__file__).parent.parent / "data" / "close_prices.json"

# 缓存过期天数（保留最近3天）
CACHE_EXPIRE_DAYS = 3


def _load_cache() -> dict:
    """加载缓存文件"""
    if not CACHE_FILE.exists():
        return {}
    
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log(f"收盘价缓存加载失败: {e}", level="WARNING")
        return {}


def _save_cache(cache: dict):
    """保存缓存文件"""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
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
    
    优先级：
    1. 缓存中最近一个交易日的收盘价
    2. 如果没有缓存，返回 None（由调用方使用数据源的close_price）
    
    Returns:
        前收盘价，如果无缓存返回 None
    """
    cache = _load_cache()
    
    if stock_code not in cache:
        return None
    
    entries = cache[stock_code]
    today = date.today()
    
    # 找到最近一个非今天的日期
    best_date = None
    best_close = None
    
    for date_str, data in entries.items():
        try:
            entry_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            # 必须是今天之前的日期
            if entry_date < today:
                if best_date is None or entry_date > best_date:
                    best_date = entry_date
                    best_close = data.get('close')
        except ValueError:
            continue
    
    if best_close:
        log(f"使用缓存收盘价: {stock_code} = {best_close:.2f} ({best_date})", level="DEBUG")
    
    return best_close


def clear_cache():
    """清空缓存（调试用）"""
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()
        log("收盘价缓存已清空")
