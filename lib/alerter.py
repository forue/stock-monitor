#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
告警决策模块 - 量价组合确认算法 + 多场景分类

核心原则：真正的异动是"价量齐动"，单一指标波动多为噪音。
L1 初筛：至少 2 个条件同时满足（OR → 多条件组合）
L2 确认：量价共振（涨幅+量比同时超标 或 极端单指标）

场景分类：
  volume_price_combo — 量价共振 (涨幅+量比同时达标)
  extreme_surge       — 急速拉升 (涨幅 ≥ 6%)
  extreme_plunge      — 急速下跌 (跌幅 ≤ -6%)
  extreme_volume_up   — 异动放量上涨 (量比 ≥ 6x, 涨 ≥ 1%)
  extreme_volume_down — 异动放量下跌 (量比 ≥ 6x, 跌 ≤ -1%)
  trend_continue_up   — 趋势延续上涨 (同向 + 偏离参考价 ≥ 2%)
  trend_continue_down — 趋势延续下跌
  rebound             — 低位反弹 (跌后转涨, 偏离上次告警价 ≥ 1.5%)
  pullback            — 高位回落 (涨后转跌, 偏离上次告警价 ≥ 1.5%)
"""

import time
from datetime import datetime
from typing import Optional, Tuple

from lib.logger import log
from lib.trading_calendar import get_current_session_key


# ============ 连续触发追踪 ============

_l1_hit_count = {}
_l2_hit_count = {}
_l1_first_hit_time = {}
_l2_first_hit_time = {}


def _reset_tracker(stock_code: str):
    _l1_hit_count.pop(stock_code, None)
    _l2_hit_count.pop(stock_code, None)
    _l1_first_hit_time.pop(stock_code, None)
    _l2_first_hit_time.pop(stock_code, None)


def _l1_consecutive_hits(stock_code: str, triggered: bool) -> int:
    now = time.time()
    prev_hit = _l1_hit_count.get(stock_code, 0)
    if not triggered:
        _l1_hit_count[stock_code] = 0
        return 0
    if prev_hit == 0:
        _l1_hit_count[stock_code] = 1
        _l1_first_hit_time[stock_code] = now
        return 1
    elapsed = now - _l1_first_hit_time.get(stock_code, now)
    if elapsed > 60:
        _l1_hit_count[stock_code] = 1
        _l1_first_hit_time[stock_code] = now
        return 1
    _l1_hit_count[stock_code] = prev_hit + 1
    return prev_hit + 1


def _l2_consecutive_hits(stock_code: str, confirmed: bool) -> int:
    now = time.time()
    prev_hit = _l2_hit_count.get(stock_code, 0)
    if not confirmed:
        _l2_hit_count[stock_code] = 0
        return 0
    if prev_hit == 0:
        _l2_hit_count[stock_code] = 1
        _l2_first_hit_time[stock_code] = now
        return 1
    elapsed = now - _l2_first_hit_time.get(stock_code, now)
    if elapsed > 60:
        _l2_hit_count[stock_code] = 1
        _l2_first_hit_time[stock_code] = now
        return 1
    _l2_hit_count[stock_code] = prev_hit + 1
    return prev_hit + 1


def reset_stock_tracker(stock_code: str):
    _reset_tracker(stock_code)


# ============ 分时段量比阈值 ============

def _get_volume_ratio_threshold(thresholds: dict, session_key: str) -> float:
    by_session = thresholds.get('volume_ratio_by_session', {})
    if session_key in by_session:
        return by_session[session_key]
    return thresholds.get('volume_ratio', 3.0)


# ============ L1 触发判断 ============

def should_trigger_l1(metrics: dict, config: dict,
                      stock_code: str = None) -> list:
    """
    L1 初筛：至少 2 个条件同时满足 + 连续命中验证
    """
    L1 = config.get('l1_thresholds', {
        'price_change_rate': 0.007,
        'price_change_pct': 2.5,
        'volume_ratio': 2.0,
        'amplitude': 0.02,
    })
    min_consecutive = L1.get('min_consecutive_hits', 2)

    session_key = get_current_session_key()
    vr_threshold = _get_volume_ratio_threshold(L1, session_key)

    conditions = []
    if metrics['price_change_rate'] >= L1['price_change_rate']:
        conditions.append(f"价格波动率={metrics['price_change_rate']:.2%}(阈值{L1['price_change_rate']:.1%})")
    if abs(metrics['price_change_pct']) >= L1['price_change_pct']:
        conditions.append(f"涨跌幅={metrics['price_change_pct']:+.2f}%(阈值±{L1['price_change_pct']:.1f}%)")
    if metrics.get('volume_ratio') is not None and metrics['volume_ratio'] >= vr_threshold:
        conditions.append(f"量比={metrics['volume_ratio']:.2f}(阈值{vr_threshold:.1f}[{session_key}])")
    if metrics.get('amplitude') is not None and metrics['amplitude'] >= L1['amplitude']:
        conditions.append(f"盘中振幅={metrics['amplitude']:.2%}(阈值{L1['amplitude']:.1%})")

    min_conditions = L1.get('min_conditions_required', 2)
    if len(conditions) < min_conditions:
        if stock_code:
            _l1_consecutive_hits(stock_code, False)
        return []

    if stock_code:
        consecutive = _l1_consecutive_hits(stock_code, True)
        if consecutive < min_consecutive:
            return []
        if stock_code in _l1_hit_count:
            conditions.insert(0, f"连续命中: {consecutive}/{min_consecutive}")

    return conditions


# ============ L2 确认验证 ============

def confirm_alert(metrics: dict, config: dict,
                  stock_code: str = None) -> list:
    """
    L2 确认：量价共振 + 连续验证

    三种确认路径：
    A. 量价共振：涨幅达标 AND 量比达标（同时）
    B. 极端涨幅：涨幅 >= extreme_price_change_pct
    C. 极端放量：量比 >= extreme_volume_ratio + 涨幅 >= min_price_for_volume
    """
    L2 = config.get('l2_thresholds', {
        'price_change_pct': 3.0,
        'volume_ratio': 3.0,
        'extreme_price_change_pct': 5.0,
        'extreme_volume_ratio': 6.0,
        'min_price_for_volume': 1.0,
    })
    min_consecutive = L2.get('min_consecutive_hits', 2)

    session_key = get_current_session_key()
    vr_threshold = _get_volume_ratio_threshold(L2, session_key)

    pct = abs(metrics['price_change_pct'])
    vol = metrics.get('volume_ratio')
    has_vol = vol is not None

    pct_ok = pct >= L2['price_change_pct']
    vol_ok = has_vol and vol >= vr_threshold
    extreme_pct_ok = pct >= L2.get('extreme_price_change_pct', 5.0)
    extreme_vol_ok = has_vol and vol >= L2.get('extreme_volume_ratio', 6.0)
    min_pct_for_vol_ok = pct >= L2.get('min_price_for_volume', 1.0)

    confirmed = False
    conditions = []

    if pct_ok and vol_ok:
        confirmed = True
        conditions.append(f"量价共振: 涨跌幅={metrics['price_change_pct']:+.2f}%(阈值±{L2['price_change_pct']:.1f}%) + 量比={vol:.2f}(阈值{vr_threshold:.1f})")
    elif extreme_pct_ok:
        confirmed = True
        conditions.append(f"极端涨跌: 涨跌幅={metrics['price_change_pct']:+.2f}%(阈值±{L2.get('extreme_price_change_pct', 5.0):.1f}%)")
    elif extreme_vol_ok and min_pct_for_vol_ok:
        confirmed = True
        conditions.append(f"极端放量: 量比={vol:.2f}(阈值{L2.get('extreme_volume_ratio', 6.0):.1f}) + 涨跌幅={metrics['price_change_pct']:+.2f}%")
    elif pct_ok:
        conditions.append(f"[未确认] 涨幅达标但无量: 涨跌幅={metrics['price_change_pct']:+.2f}%")
    elif vol_ok:
        conditions.append(f"[未确认] 放量但涨幅不足: 量比={vol:.2f} 涨跌幅={metrics['price_change_pct']:+.2f}%")

    if not confirmed:
        if stock_code:
            _l2_consecutive_hits(stock_code, False)
        return []

    if stock_code:
        consecutive = _l2_consecutive_hits(stock_code, True)
        if consecutive < min_consecutive:
            return []
        if stock_code in _l2_hit_count:
            conditions.append(f"连续确认: {consecutive}/{min_consecutive}")

    return conditions


# ============ 场景分类 ============

SCENARIO_META = {
    "volume_price_combo":   {"icon": "📊", "label": "量价共振", "desc": "价量同向异动，主力资金介入信号"},
    "extreme_surge":        {"icon": "🚀", "label": "急速拉升", "desc": "涨幅超阈值，关注冲高持续性"},
    "extreme_plunge":       {"icon": "💥", "label": "急速下跌", "desc": "跌幅超阈值，注意是否有利空或错杀"},
    "extreme_volume_up":    {"icon": "📈", "label": "异动放量上攻", "desc": "异常放量上涨，可能吸筹或出货"},
    "extreme_volume_down":  {"icon": "📉", "label": "异动放量下砸", "desc": "异常放量下跌，可能恐慌或洗盘"},
    "trend_continue_up":    {"icon": "🔥", "label": "涨势加速", "desc": "同向持续扩大，趋势强化"},
    "trend_continue_down":  {"icon": "🧊", "label": "跌势加速", "desc": "同向持续扩大，趋势强化"},
    "rebound":              {"icon": "🔄", "label": "低位反弹", "desc": "跌后反转回升，关注是否为反转信号"},
    "pullback":             {"icon": "⚠️", "label": "高位回落", "desc": "涨后反转下跌，关注是否为见顶信号"},
}


def classify_scenario(metrics: dict,
                      last_alert_info: Optional[dict] = None) -> str:
    """
    根据当前指标和上次告警状态，分类异动场景

    Returns:
        场景 key (volume_price_combo | extreme_surge | ... | rebound | pullback)
    """
    pct = metrics['price_change_pct']
    abs_pct = abs(pct)
    vol = metrics.get('volume_ratio')
    is_up = pct > 0

    if abs_pct >= 6.0:
        return "extreme_surge" if is_up else "extreme_plunge"

    if vol and vol >= 6.0:
        return "extreme_volume_up" if is_up else "extreme_volume_down"

    if last_alert_info:
        last_price = last_alert_info.get("alert_price")
        last_dir = last_alert_info.get("direction")
        if last_price and last_dir:
            current_dir = "up" if is_up else "down"
            if current_dir == last_dir:
                return "trend_continue_up" if is_up else "trend_continue_down"
            else:
                return "rebound" if is_up else "pullback"

    return "volume_price_combo"


def format_scenario_header(scenario: str) -> str:
    """生成场景标题行"""
    meta = SCENARIO_META.get(scenario, SCENARIO_META["volume_price_combo"])
    return f"{meta['icon']} 【{meta['label']}】{meta['desc']}"


# ============ 告警消息构建 ============

def build_alert_message(stock: dict, stock_data: dict, metrics: dict,
                        triggered_l1: list = None, triggered_l2: list = None,
                        scenario: str = "volume_price_combo",
                        last_alert_info: Optional[dict] = None) -> str:
    """构建告警推送消息文本——按场景区分标题"""
    if triggered_l1 is None:
        triggered_l1 = []
    if triggered_l2 is None:
        triggered_l2 = []

    stock_code = stock.get('code', 'UNKNOWN')
    stock_name = stock.get('name', stock_code)
    current_price = stock_data['current_price']
    change_pct = metrics['price_change_pct']

    l1_str = "\n".join([f"  • {t}" for t in triggered_l1])
    l2_str = "\n".join([f"  • {t} ✅" for t in triggered_l2])

    reference_line = ""
    if last_alert_info:
        ref_price = last_alert_info.get("alert_price")
        ref_dir = last_alert_info.get("direction", "")
        if ref_price:
            deviation = (current_price - ref_price) / ref_price * 100
            dir_label = "上涨" if ref_dir == "up" else "下跌"
            reference_line = f"📍 上次告警: ¥{ref_price:.2f} ({dir_label}) → 当前偏离 {deviation:+.2f}%\n"

    scenario_header = format_scenario_header(scenario)
    time_str = datetime.now().strftime('%H:%M:%S')

    message = f"""{scenario_header}

📊 {stock_name} ({stock_code})
💰 当前价：¥{current_price:.2f}
📊 涨跌幅：{change_pct:+.2f}%
📈 量比：{f"{metrics['volume_ratio']:.2f}" if metrics.get('volume_ratio') is not None else 'N/A'}
📊 振幅：{f"{metrics['amplitude']:.2%}" if metrics.get('amplitude') is not None else 'N/A'}
{reference_line}⏰ 时间：{time_str}

🚨 触发条件:
{l1_str}

✅ 确认异动:
{l2_str}

请速速查看！🦐"""

    return message
