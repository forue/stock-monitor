#!/usr/bin/env python3
"""收盘价缓存过期校验测试"""
import sys
sys.path.insert(0, '/home/fayou/.openclaw/workspace/stock-monitor')

from datetime import date, timedelta
import json
from pathlib import Path

from lib.trading_calendar import get_prev_trading_day, is_trading_day
import lib.close_cache as cc

PASS = 0
FAIL = 0

def check(name, got, expect):
    global PASS, FAIL
    ok = got == expect
    if ok: PASS += 1
    else: FAIL += 1
    print(f"{'✅' if ok else '❌'} {name}: {got} (expect {expect})")

print("=== 上一交易日计算 ===")
# 2026-10-30 是周五，上一交易日应为 10-29（周四）
check("10-30 -> 上一交易日", get_prev_trading_day(date(2026,10,30)).isoformat(), "2026-10-29")
# 2026-10-26 是周一，上一交易日应为 10-23（周五，跳过周末）
check("10-26 -> 上一交易日", get_prev_trading_day(date(2026,10,26)).isoformat(), "2026-10-23")
# 2026-04-07 是周二，04-06 清明假期 → 上一交易日应为 04-03（周五）
check("04-07 -> 上一交易日", get_prev_trading_day(date(2026,4,7)).isoformat(), "2026-04-03")

print()
print("=== 缓存过期校验 ===")

# 构造：缓存只有 10-28，今天 10-30，缺少 10-29 → 应返回 None
fake_today = date(2026,10,30)
orig_today = cc.date

class FakeDate(date):
    @classmethod
    def today(cls):
        return fake_today

cc.date = FakeDate
cc._memory_cache = {
    'TEST01': {
        '2026-10-27': {'close': 10.0},
        '2026-10-28': {'close': 10.9},   # 缺少 10-29
    }
}
cc._memory_cache_mtime = 0
# 让 _load_cache 直接返回内存缓存
orig_exists = cc.CACHE_FILE
res = cc.get_prev_close('TEST01')
check("缓存缺上一交易日 -> None", res, None)

# 构造：缓存有 10-29 → 应返回 10-29 的收盘价
cc._memory_cache = {
    'TEST02': {
        '2026-10-28': {'close': 10.9},
        '2026-10-29': {'close': 11.5},   # 上一交易日存在
    }
}
res = cc.get_prev_close('TEST02')
check("缓存有上一交易日 -> 11.5", res, 11.5)

# 恢复
cc.date = orig_today

print(f"\n=== 结果: {PASS} 通过, {FAIL} 失败 ===")
