#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A 股交易日历工具
2026 年沪深北交易所休市安排（来源：三大交易所 2025-12-22 通知）
"""

from datetime import date, datetime

# 2026 年 A 股休市日期（周一~周五，不含周末）
# 周末本来就不开盘，无需重复列出
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

# 周末需补班的交易日（调休后需要开市的周六日）
# 2026 年无额外休市调整日（周末本就不开市）
TRADING_WEEKENDS_2026 = set()


def is_trading_day(d=None) -> bool:
    """判断给定日期是否为 A 股交易日
    
    Args:
        d: datetime/date 对象，默认今天
    
    Returns:
        True=交易日, False=休市
    """
    if d is None:
        d = date.today()
    elif isinstance(d, datetime):
        d = d.date()
    
    # 特殊情况：周末调休开市
    if d in TRADING_WEEKENDS_2026:
        return True
    
    # 周末休市
    if d.weekday() >= 5:  # 周六=5, 周日=6
        return False
    
    # 节假日休市
    if d in HOLIDAYS_2026:
        return False
    
    return True


def is_trading_session() -> tuple:
    """判断当前是否在 A 股交易时段
    
    Returns:
        (is_session: bool, session_name: str)
    """
    now = datetime.now()
    
    if not is_trading_day(now.date()):
        return False, "节假日/周末"
    
    t = now.time()
    
    if t.hour < 9 or (t.hour == 9 and t.minute < 30):
        return False, "盘前"
    
    if t.hour < 11 or (t.hour == 11 and t.minute <= 30):
        if t.hour < 10:
            return True, "开盘高波动"
        return True, "早盘"
    
    if t.hour < 13:
        return False, "午间休市"
    
    if t.hour < 15:
        if t.hour >= 14 and t.minute >= 30:
            return True, "收盘高波动"
        return True, "午后"
    
    return False, "盘后"


if __name__ == "__main__":
    # 快速测试：列出 2026 全年所有非交易日
    holidays = sorted(HOLIDAYS_2026)
    print(f"2026 年 A 股休市安排（共 {len(holidays)} 个工作日休市日）")
    for d in holidays:
        names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        print(f"  {d.strftime('%Y-%m-%d')} {names[d.weekday()]}")
