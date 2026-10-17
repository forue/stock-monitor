#!/usr/bin/env python3
"""破板检测逻辑测试 - 精确涨停价 + 回封场景"""
import sys
sys.path.insert(0, '/home/fayou/.openclaw/workspace/stock-monitor')

import time
from lib.notifier import (
    _should_escalate, _limit_state,
    _calc_limit_price, _is_at_limit_price, _get_limit_pct,
    _get_limit_detection, set_limit_detection_config
)

# 设置默认检测参数
set_limit_detection_config({})

MIN_LIMIT_DURATION = _get_limit_detection('min_limit_duration')
BREAK_CONFIRM_INTERVAL = _get_limit_detection('break_confirm_interval')
MIN_RESEAL_DURATION = _get_limit_detection('min_reseal_duration')

PASS = 0
FAIL = 0

def test(name, result, expect):
    global PASS, FAIL
    ok = result == expect
    status = "✅" if ok else "❌"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"{status} {name}: {result} (expect {expect})")

print("=== 涨停价精确计算测试 ===")
print(f"主板涨停价(昨收10.15): {_calc_limit_price(10.15, 10.0, 'up')} (expect 11.17)")
print(f"主板涨停价(昨收12.34): {_calc_limit_price(12.34, 10.0, 'up')} (expect 13.57)")
print(f"创业板涨停价(昨收100): {_calc_limit_price(100.0, 20.0, 'up')} (expect 120.0)")
print(f"主板跌停价(昨收10.15): {_calc_limit_price(10.15, 10.0, 'down')} (expect 9.14)")
print()

print("=== 涨跌停幅度判断测试（2026年7月后规则） ===")
# 主板（ST已统一为10%）
test("主板普通股票", _get_limit_pct('600104', '上汽集团'), 10.0)
test("主板普通股票(00开头)", _get_limit_pct('002475', '立讯精密'), 10.0)
test("主板ST股票(已统一为10%)", _get_limit_pct('600104', 'ST上气'), 10.0)
test("主板*ST股票(已统一为10%)", _get_limit_pct('600104', '*ST上气'), 10.0)
# 创业板
test("创业板普通", _get_limit_pct('300001', '特锐德'), 20.0)
test("创业板ST", _get_limit_pct('300001', 'ST特锐德'), 20.0)
# 科创板
test("科创板普通", _get_limit_pct('688001', '华兴源创'), 20.0)
test("科创板ST", _get_limit_pct('688001', 'ST华兴'), 20.0)
# 北交所
test("北交所普通", _get_limit_pct('830799', '艾融软件'), 30.0)
test("北交所ST", _get_limit_pct('830799', 'ST艾融'), 30.0)
print()

print("=== 涨跌停判定测试 ===")
# 主板，昨收10.15，涨停价11.17
test("现价11.17 = 涨停", _is_at_limit_price(11.17, 11.17, 'up'), True)
test("现价11.16 = 非涨停(低于1分)", _is_at_limit_price(11.16, 11.17, 'up'), False)
test("现价11.15 = 非涨停", _is_at_limit_price(11.15, 11.17, 'up'), False)
test("现价11.18 = 涨停", _is_at_limit_price(11.18, 11.17, 'up'), True)
# 跌停测试
test("现价9.13 = 跌停", _is_at_limit_price(9.13, 9.13, 'down'), True)
test("现价9.14 = 非跌停(高于1分)", _is_at_limit_price(9.14, 9.13, 'down'), False)
test("现价9.12 = 跌停", _is_at_limit_price(9.12, 9.13, 'down'), True)
print()

print("=== 破板检测测试 ===")
now = time.time()

# 清理缓存干扰
from lib.close_cache import clear_cache
clear_cache()

# 清理涨跌停状态（避免跨测试干扰）
from lib.notifier import _reset_limit_state, _limit_state_date
import lib.notifier as notifier
notifier._limit_state_date = None  # 重置日期，确保下次调用不会清空
_reset_limit_state()  # 设置日期为今天

# 1. 涨停后破板
_limit_state.clear()
_limit_state['600104'] = {
    'at_limit': True, 'limit_type': 'up', 'limit_price': 11.17,
    'first_limit_time': now - 60, 'below_limit_since': now - 15,
    'reseal_time': None, 'break_count': 0
}
r = _should_escalate('600104', 'test', 10.5, 5.0, {}, {}, close_price=10.15)
test("涨停后破板", r, 'limit_break_up')

# 2. 回封后再破板
_limit_state.clear()
_limit_state['600104'] = {
    'at_limit': True, 'limit_type': 'up', 'limit_price': 11.17,
    'first_limit_time': now - 120, 'below_limit_since': now - 15,
    'reseal_time': now - 90,  # 90秒前回封
    'break_count': 1  # 已经破过1次
}
r = _should_escalate('600104', 'test', 10.5, 5.0, {}, {}, close_price=10.15)
test("回封后再破板(回封90s>60s)", r, 'limit_break_up')

# 3. 回封时间太短，视为抖动
_limit_state.clear()
_limit_state['600104'] = {
    'at_limit': True, 'limit_type': 'up', 'limit_price': 11.17,
    'first_limit_time': now - 60, 'below_limit_since': None,
    'reseal_time': now - 30,  # 仅30秒前回封
    'break_count': 1
}
r = _should_escalate('600104', 'test', 10.5, 5.0, {}, {}, close_price=10.15)
test("回封仅30s抖动", r, None)

# 4. 假封板(封板时间太短)
_limit_state.clear()
_limit_state['600104'] = {
    'at_limit': True, 'limit_type': 'up', 'limit_price': 11.17,
    'first_limit_time': now - 10, 'below_limit_since': now - 15,
    'reseal_time': None, 'break_count': 0
}
r = _should_escalate('600104', 'test', 10.5, 5.0, {}, {}, close_price=10.15)
test("假封板(封板10s)", r, None)

# 5. 跌停破板
_limit_state.clear()
_limit_state['600104'] = {
    'at_limit': True, 'limit_type': 'down', 'limit_price': 9.14,
    'first_limit_time': now - 60, 'below_limit_since': now - 15,
    'reseal_time': None, 'break_count': 0
}
r = _should_escalate('600104', 'test', 9.5, -6.0, {}, {}, close_price=10.15)
test("跌停破板", r, 'limit_break_down')

# 6. 创业板20%涨停破板
_limit_state.clear()
_limit_state['300001'] = {
    'at_limit': True, 'limit_type': 'up', 'limit_price': 120.0,
    'first_limit_time': now - 60, 'below_limit_since': now - 15,
    'reseal_time': None, 'break_count': 0
}
r = _should_escalate('300001', 'test', 115.0, 15.0, {}, {}, close_price=100.0)
test("创业板破板", r, 'limit_break_up')

print(f"\n=== 结果: {PASS} 通过, {FAIL} 失败 ===")
