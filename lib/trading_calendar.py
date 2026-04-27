#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A 股交易日历模块 - 交易日判断/交易时段/时间占比
复用 scripts/trading_calendar.py 并扩展
"""

from datetime import datetime, date, time as dt_time
from typing import Tuple

# 2026 A 股休市日（周一~周五，不含周末）
HOLIDAYS_2026 = {
    date(2026, 1, 1),   # 元旦
    date(2026, 1, 2),   # 元旦
    date(2026, 2, 16),  # 春节
    date(2026, 2, 17),  # 春节
    date(2026, 2, 18),  # 春节
    date(2026, 2, 19),  # 春节
    date(2026, 2, 20),  # 春节
    date(2026, 2, 23),  # 春节
    date(2026, 4, 6),   # 清明节
    date(2026, 5, 1),   # 劳动节
    date(2026, 5, 4),   # 劳动节
    date(2026, 5, 5),   # 劳动节
    date(2026, 6, 19),  # 端午节
    date(2026, 9, 25),  # 中秋节
    date(2026, 10, 1),  # 国庆节
    date(2026, 10, 2),  # 国庆节
    date(2026, 10, 6),  # 国庆节
    date(2026, 10, 7),  # 国庆节
}

# 周末补班交易日（2026 年无）
TRADING_WEEKENDS_2026 = set()


def is_trading_day(d=None) -> bool:
    """判断给定日期是否为 A 股交易日"""
    if d is None:
        d = date.today()
    elif isinstance(d, datetime):
        d = d.date()

    if d in TRADING_WEEKENDS_2026:
        return True
    if d.weekday() >= 5:
        return False
    if d in HOLIDAYS_2026:
        return False
    return True


def is_trading_session() -> Tuple[bool, str]:
    """
    判断当前是否在 A 股交易时段

    Returns:
        (是否在交易时段, 当前时段名称)
    """
    now = datetime.now()

    if not is_trading_day(now):
        return False, "节假日/周末"

    current_time = now.time()

    # 盘前
    if current_time < dt_time(9, 30):
        return False, "盘前"

    # 早盘连续竞价 (09:30-11:30)
    if dt_time(9, 30) <= current_time <= dt_time(11, 30):
        if current_time <= dt_time(10, 0):
            return True, "开盘高波动期"
        return True, "早盘正常期"

    # 午间休市 (11:30-13:00)
    if dt_time(11, 30) < current_time < dt_time(13, 0):
        return False, "午间休市"

    # 午后连续竞价 (13:00-15:00)
    if dt_time(13, 0) <= current_time <= dt_time(15, 0):
        if current_time >= dt_time(14, 30):
            return True, "收盘高波动期"
        return True, "午后正常期"

    # 盘后
    return False, "盘后"


def get_trading_elapsed_ratio() -> float:
    """
    计算当日已过交易时间占比（用于量比修正）

    A股交易时间：09:30-11:30 (120min) + 13:00-15:00 (120min) = 240min

    注意：开盘集合竞价(9:15-9:25)的成交量在9:30一次性释放，
    若 elapsed_minutes 过小会导致量比虚高，因此设置最小有效时间为 30 分钟。

    Returns:
        0.125~1.0 的占比 (最低 30/240=0.125)，非交易时段返回 1.0
    """
    current_time = datetime.now().time()
    MIN_ELAPSED_MINUTES = 30  # 最小有效交易时间，避免集合竞价导致量比虚高

    if current_time < dt_time(9, 30):
        return 1.0
    elif current_time <= dt_time(11, 30):
        elapsed_minutes = (current_time.hour - 9) * 60 + current_time.minute - 30
        elapsed_minutes = max(elapsed_minutes, MIN_ELAPSED_MINUTES)
        return elapsed_minutes / 240.0
    elif current_time < dt_time(13, 0):
        return 120 / 240.0  # 0.5
    elif current_time <= dt_time(15, 0):
        elapsed_minutes = 120 + (current_time.hour - 13) * 60 + current_time.minute
        return elapsed_minutes / 240.0
    else:
        return 1.0


def get_current_session_key() -> str:
    """
    获取当前交易时段的键名（用于分时段阈值查找）

    Returns:
        "opening"  - 开盘期 (9:30-10:00)，天然放量，需更高阈值
        "morning"  - 早盘正常期 (10:00-11:30)
        "afternoon" - 午后正常期 (13:00-14:30)
        "closing"  - 尾盘期 (14:30-15:00)，放量常见，适当放宽
        "off"      - 非交易时段
    """
    current_time = datetime.now().time()

    if current_time < dt_time(9, 30):
        return "off"
    elif current_time <= dt_time(10, 0):
        return "opening"
    elif current_time <= dt_time(11, 30):
        return "morning"
    elif current_time < dt_time(13, 0):
        return "morning"  # 午间休市，沿用早盘
    elif current_time < dt_time(14, 30):
        return "afternoon"
    elif current_time <= dt_time(15, 0):
        return "closing"
    else:
        return "off"


def get_check_interval(session_name: str, volatility: float) -> int:
    """根据时段名称和波动率返回检查间隔 (秒)"""
    base_intervals = {
        "开盘高波动期": 2,
        "早盘正常期": 5,
        "午后正常期": 5,
        "收盘高波动期": 2,
        "午间休市": 300,
        "盘前": 300,
        "盘后": 300,
        "节假日/周末": 300,
    }
    base = base_intervals.get(session_name, 5)

    if session_name in ["午间休市", "盘前", "盘后", "节假日/周末"]:
        return base

    if volatility >= 0.02:
        return 3
    elif volatility >= 0.01:
        return 2
    elif volatility >= 0.005:
        return max(2, base // 2)
    else:
        return base
