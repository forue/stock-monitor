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

# ============ 涨停/跌停状态追踪 ============
# {stock_code: {'at_limit': bool, 'limit_type': 'up'|'down', 'limit_price': float, 'last_alert_time': float}}
_limit_state = {}
_limit_state_date = None


def _reset_limit_state():
    """跨天重置涨停状态"""
    global _limit_state_date
    today = datetime.now().date()
    if _limit_state_date != today:
        _limit_state.clear()
        _limit_state_date = today


def _get_limit_state(stock_code: str) -> dict:
    return _limit_state.get(stock_code, {})


def _set_limit_state(stock_code: str, state: dict):
    _limit_state[stock_code] = state


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
    "limit_break_up":       {"icon": "🔓", "label": "涨停破板", "desc": "涨停后开板，关注是否为出货信号"},
    "limit_break_down":     {"icon": "🔓", "label": "跌停破板", "desc": "跌停后开板，关注是否为抄底信号"},
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
                        last_alert_info: Optional[dict] = None,
                        limit_info: Optional[dict] = None) -> str:
    """构建告警推送消息文本——按场景区分标题，含投资参考"""
    if triggered_l1 is None:
        triggered_l1 = []
    if triggered_l2 is None:
        triggered_l2 = []

    stock_code = stock.get('code', 'UNKNOWN')
    stock_name = stock.get('name', stock_code)
    current_price = stock_data['current_price']
    change_pct = metrics['price_change_pct']
    volume_ratio = metrics.get('volume_ratio')

    l1_str = "\n".join([f"  • {t}" for t in triggered_l1])
    l2_str = "\n".join([f"  • {t} ✅" for t in triggered_l2])

    reference_line = ""
    if last_alert_info:
        ref_price = last_alert_info.get("alert_price")
        ref_dir = last_alert_info.get("direction", "")
        if ref_price:
            deviation = (current_price - ref_price) / ref_price * 100
            dir_label = "上涨" if ref_dir == "up" else "下跌"
            reference_line = f"📍 上次告警: ¥{ref_price:.2f} ({dir_label}) → 偏离 {deviation:+.2f}%\n"

    scenario_header = format_scenario_header(scenario)
    time_str = datetime.now().strftime('%H:%M:%S')

    # 破板场景：添加详细破板信息
    limit_detail = ""
    if scenario in ("limit_break_up", "limit_break_down") and limit_info:
        limit_price = limit_info.get('limit_price')
        limit_duration = limit_info.get('limit_duration', 0)
        break_count = limit_info.get('break_count', 1)
        
        if limit_price:
            if scenario == "limit_break_up":
                drop_pct = (limit_price - current_price) / limit_price * 100
                limit_detail = f"🔓 距涨停价：-{drop_pct:.2f}% (涨停价 ¥{limit_price:.2f})\n"
            else:
                rise_pct = (current_price - limit_price) / limit_price * 100
                limit_detail = f"🔓 距跌停价：+{rise_pct:.2f}% (跌停价 ¥{limit_price:.2f})\n"
        
        if limit_duration > 0:
            limit_detail += f"⏱️ 封板时长：{_format_duration(limit_duration)}\n"
        
        if break_count > 1:
            limit_detail += f"🔁 今日第 {break_count} 次破板\n"

    # 量比分析与投资参考
    volume_analysis = ""
    if volume_ratio is not None:
        if scenario == "limit_break_up":
            if volume_ratio >= 3.0:
                volume_analysis = "📊 放量破板：大资金离场，警惕见顶回落"
            elif volume_ratio >= 1.5:
                volume_analysis = "📊 温和放量：多空分歧加大，关注后续走势"
            else:
                volume_analysis = "📊 缩量破板：可能洗盘，关注回封机会"
        elif scenario == "limit_break_down":
            if volume_ratio >= 3.0:
                volume_analysis = "📊 放量开板：恐慌盘出逃，警惕继续下跌"
            elif volume_ratio >= 1.5:
                volume_analysis = "📊 温和放量：承接盘出现，关注是否止跌"
            else:
                volume_analysis = "📊 缩量开板：抛压减轻，可能超跌反弹"
        elif change_pct > 0:
            if volume_ratio >= 3.0:
                volume_analysis = "📊 天量上涨：警惕主力对倒出货"
            elif volume_ratio >= 2.0:
                volume_analysis = "📊 放量上攻：资金积极介入，趋势健康"
            else:
                volume_analysis = "📊 缩量上涨：上攻乏力，关注量能配合"
        else:
            if volume_ratio >= 3.0:
                volume_analysis = "📊 天量下跌：恐慌情绪蔓延，勿急于抄底"
            elif volume_ratio >= 2.0:
                volume_analysis = "📊 放量下跌：空方主导，等待企稳信号"
            else:
                volume_analysis = "📊 缩量下跌：抛压有限，关注支撑位"

    # 操作建议
    action_advice = _get_action_advice(scenario, change_pct, volume_ratio)

    message = f"""{scenario_header}

📊 {stock_name} ({stock_code})
💰 当前价：¥{current_price:.2f}
📈 涨跌幅：{change_pct:+.2f}%
📊 量比：{f"{volume_ratio:.2f}" if volume_ratio is not None else 'N/A'}
📊 振幅：{f"{metrics['amplitude']:.2%}" if metrics.get('amplitude') is not None else 'N/A'}
{reference_line}{limit_detail}⏰ 时间：{time_str}

🚨 触发条件:
{l1_str}

✅ 确认异动:
{l2_str}

{volume_analysis}
💡 {action_advice}"""

    return message


def _format_duration(seconds: float) -> str:
    """格式化时长"""
    if seconds < 60:
        return f"{int(seconds)}秒"
    elif seconds < 3600:
        return f"{int(seconds // 60)}分{int(seconds % 60)}秒"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}小时{minutes}分"


def _get_action_advice(scenario: str, change_pct: float, volume_ratio: Optional[float]) -> str:
    """根据场景生成操作建议"""
    advice_map = {
        "limit_break_up": "涨停打开，若持仓可考虑部分止盈；若空仓观望，勿追高",
        "limit_break_down": "跌停打开，关注是否有资金承接；不宜盲目抄底",
        "extreme_surge": "涨幅过大，追高风险大；持仓者注意设置止盈位",
        "extreme_plunge": "短期超跌，勿恐慌割肉；关注是否有企稳迹象",
        "extreme_volume_up": "放量异动，若持续放量可关注；若冲高回落则减仓",
        "extreme_volume_down": "放量下跌，等待缩量企稳后再考虑介入",
        "trend_continue_up": "趋势强化，顺势而为；注意移动止盈保护利润",
        "trend_continue_down": "跌势加速，勿轻易抄底；等待明确止跌信号",
        "rebound": "低位反弹，关注能否站稳均线；轻仓试探为宜",
        "pullback": "高位回落，注意风险控制；减仓或设好止损",
        "volume_price_combo": "量价异动，关注后续量能能否持续",
    }
    return advice_map.get(scenario, "关注后续走势，做好风险控制")


# ============ 技术指标买卖信号 ===========

from lib.indicators import (
    get_close_history, calc_ma, calc_rsi,
    check_golden_cross, check_death_cross,
    get_prev_ma, save_curr_ma,
    get_price_volume_history, detect_divergence,
)


_trading_signal_state = {}
_trading_signal_date = None

MAX_TRADING_SIGNALS_PER_DAY = 3


def _reset_daily_signal_state():
    """跨天重置交易信号状态"""
    global _trading_signal_date
    today = datetime.now().date()
    if _trading_signal_date != today:
        _trading_signal_state.clear()
        _trading_signal_date = today


def _get_signal_state(stock_code):
    return _trading_signal_state.get(stock_code, {})


def _update_signal_state(stock_code, signal_type):
    state = _get_signal_state(stock_code)
    _trading_signal_state[stock_code] = {
        **state,
        'last_signal': signal_type,
        'last_time': time.time(),
        'signal_count_today': state.get('signal_count_today', 0) + 1,
    }


def _update_check_time(stock_code):
    state = _get_signal_state(stock_code)
    _trading_signal_state[stock_code] = {
        **state,
        'last_check_time': time.time(),
    }


def _trading_signal_daily_ok(stock_code, config):
    """每日交易信号上限检查"""
    max_daily = config.get('tech_analysis_defaults', {}).get(
        'max_daily_signals', MAX_TRADING_SIGNALS_PER_DAY)
    count = _get_signal_state(stock_code).get('signal_count_today', 0)
    if count >= max_daily:
        log(f"{stock_code} 今日交易信号已达上限 ({max_daily}次)，跳过", level="DEBUG")
        return False
    return True


def check_trading_signal(stock_code, current_price,
                          stock_config, config,
                          db_path):
    """
    检查金叉/死叉买卖信号

    Returns:
        None -> 无信号
        dict  -> {'signal': 'buy'|'sell', 'reason': str, ...}
    """
    _reset_daily_signal_state()

    # 合并股票专属配置与全局默认值 (stock_config 即 tech_analysis 字典)
    defaults = config.get('tech_analysis_defaults', {})
    tech = {**defaults, **stock_config} if stock_config else defaults
    if not tech.get('enabled', False):
        return None

    ma_fast_period = tech.get('ma_fast', 8)
    ma_slow_period = tech.get('ma_slow', 20)
    ma_filter_period = tech.get('ma_filter', 60)
    ma_filter_pct = tech.get('ma_filter_pct', 0.80)
    rsi_period = tech.get('rsi_period', 14)
    rsi_max = tech.get('rsi_max', 70)
    min_signal_interval = tech.get('min_signal_interval', 3600)
    check_interval = tech.get('check_interval', 300)

    # 0. 检查节流：上次计算后未满 check_interval 秒则跳过
    state = _get_signal_state(stock_code)
    now = time.time()
    if state.get('last_check_time') and (now - state['last_check_time']) < check_interval:
        return None

    # 1. 获取历史收盘价
    max_days = max(ma_slow_period, ma_filter_period, rsi_period + 1)
    closes = get_close_history(db_path, stock_code, max_days)

    if len(closes) < max(ma_slow_period, rsi_period + 1):
        return None

    # 2. 计算各均线
    ma_fast = calc_ma(closes, ma_fast_period)
    ma_slow = calc_ma(closes, ma_slow_period)
    ma_filter = calc_ma(closes, ma_filter_period)
    rsi = calc_rsi(closes, rsi_period)

    if ma_fast is None or ma_slow is None:
        return None

    # 3. 获取上期 MA 值（save_curr_ma 在交叉判断之后调用，保证状态不被消耗）
    prev_fast, prev_slow = get_prev_ma(stock_code)

    # 4. 更新本次检查时间
    _update_check_time(stock_code)

    # 5. 交叉检测（必须在 save_curr_ma 之前）
    golden = check_golden_cross(ma_fast, ma_slow, prev_fast, prev_slow)
    death = check_death_cross(ma_fast, ma_slow, prev_fast, prev_slow)

    signal = None

    if golden or death:
        # 防重复：同类型信号在 min_signal_interval 秒内延后，不消耗 MA 状态
        if state:
            last_signal = state.get('last_signal')
            last_time = state.get('last_time', 0)
            if last_time and (now - last_time) < min_signal_interval:
                if (last_signal == 'buy' and golden) or (last_signal == 'sell' and death):
                    return None  # 延后：不保存 MA，下轮继续检测

        if golden:
            filter_ok = current_price >= (ma_filter or 0) * ma_filter_pct
            rsi_ok = rsi is None or rsi < rsi_max

            if filter_ok and rsi_ok:
                if not _trading_signal_daily_ok(stock_code, config):
                    save_curr_ma(stock_code, ma_fast, ma_slow)
                    return None

                _update_signal_state(stock_code, 'buy')
                save_curr_ma(stock_code, ma_fast, ma_slow)
                return {
                    'signal': 'buy',
                    'reason': '金叉',
                    'ma_fast': ma_fast, 'ma_slow': ma_slow,
                    'ma_filter': ma_filter, 'ma_filter_pct': ma_filter_pct,
                    'rsi': rsi, 'rsi_max': rsi_max, 'rsi_period': rsi_period,
                    'close': current_price,
                }
            else:
                log(f"金叉触发但附加条件未满足 ({stock_code}): "
                    f"filter={'✅' if filter_ok else '❌'}({current_price:.2f} vs {ma_filter:.2f}*{ma_filter_pct:.0%}), "
                    f"RSI={'✅' if rsi_ok else '❌'}({rsi:.1f} vs {rsi_max})",
                    level="DEBUG")
                return None  # 延后：不消耗 MA 状态，条件达标后可重检

        elif death:
            if not _trading_signal_daily_ok(stock_code, config):
                save_curr_ma(stock_code, ma_fast, ma_slow)
                return None

            _update_signal_state(stock_code, 'sell')
            save_curr_ma(stock_code, ma_fast, ma_slow)
            return {
                'signal': 'sell',
                'reason': '死叉',
                'ma_fast': ma_fast, 'ma_slow': ma_slow,
                'ma_filter': ma_filter,
                'rsi': rsi, 'rsi_period': rsi_period,
                'close': current_price,
            }

    # 无信号：保存本期 MA 供下轮交叉检测对比
    save_curr_ma(stock_code, ma_fast, ma_slow)
    return None


# ============ 交易信号消息构建 ===========

def build_trading_signal_message(stock, signal):
    """构建买卖信号推送消息"""
    stock_code = stock.get('code', 'UNKNOWN')
    stock_name = stock.get('name', stock_code)
    signal_type = signal.get('signal', 'buy')
    ma_fast = signal.get('ma_fast', 0)
    ma_slow = signal.get('ma_slow', 0)
    ma_filter = signal.get('ma_filter', 0)
    ma_filter_pct = signal.get('ma_filter_pct', 0.80)
    rsi = signal.get('rsi')
    rsi_period = signal.get('rsi_period', 14)
    rsi_max = signal.get('rsi_max', 70)
    close = signal.get('close', 0)

    if signal_type == 'buy':
        header = '🔔 【买入信号】均线金叉 + 技术面共振'
        icon = '📈'
        reason_str = f'MA{int(ma_fast)} 上穿 MA{int(ma_slow)}（金叉）✅'
        filter_ok = close >= ma_filter * ma_filter_pct
        filter_str = f'MA{int(ma_filter)}：¥{close:.2f} / ¥{ma_filter:.2f} = {close/ma_filter*100:.1f}%' + \
            f' {"✅" if filter_ok else "❌"} (需≥{ma_filter_pct:.0%})'
        rsi_str = f'RSI{rsi_period}：{rsi:.1f}' + \
            f' {"✅" if rsi and rsi < rsi_max else "❌"} (<{rsi_max} 未超买)'
        
        # 买入建议
        if filter_ok and rsi and rsi < rsi_max:
            advice = "💡 金叉确认+过滤条件通过，可考虑分批建仓"
        elif filter_ok:
            advice = "⚠️ 金叉成立但RSI偏高，建议等待回调再介入"
        else:
            advice = "⚠️ 金叉成立但价格偏低，观察是否有企稳迹象"
    else:
        header = '🔻 【卖出信号】均线死叉'
        icon = '📉'
        reason_str = f'MA{int(ma_fast)} 下穿 MA{int(ma_slow)}（死叉）⚠️'
        filter_str = f'MA{int(ma_filter)}：¥{close:.2f}'
        rsi_str = f'RSI{rsi_period}：{rsi:.1f}' if rsi else 'RSI：N/A'
        
        # 卖出建议
        advice = "💡 死叉形成，持仓者可考虑减仓或设置止损位"

    from datetime import datetime
    time_str = datetime.now().strftime('%H:%M:%S')

    message = f"""{header}

📊 {stock_name} ({stock_code})
💰 当前价：¥{close:.2f}
{icon} {reason_str}
📊 {filter_str}
📉 {rsi_str}
⏰ 时间：{time_str}

{advice}"""
    
    return message


# ============ 实盘辅助功能：主力资金流 / 五档盘口 / 量价背离 ============

_fund_flow_state = {}
_order_book_state = {}
_divergence_state = {}
_rt_daily_date = None

# ============ 全局限流：实盘功能告警 ============
_rt_alert_timestamps = []  # 全局实盘告警时间戳列表
_RT_ALERT_LIMIT = 10  # 每分钟最多实盘告警数
_RT_ALERT_WINDOW = 60  # 滑动窗口（秒）


def _rt_global_throttled() -> bool:
    """全局限流：检查每分钟实盘告警数是否超限"""
    import time
    now = time.time()
    # 清理过期时间戳
    global _rt_alert_timestamps
    _rt_alert_timestamps = [t for t in _rt_alert_timestamps if now - t < _RT_ALERT_WINDOW]
    if len(_rt_alert_timestamps) >= _RT_ALERT_LIMIT:
        return True
    return False


def _rt_record_alert():
    """记录一次实盘告警"""
    import time
    _rt_alert_timestamps.append(time.time())


def _reset_rt_daily_state():
    """跨天重置实盘辅助功能状态"""
    global _rt_daily_date
    today = datetime.now().date()
    if _rt_daily_date != today:
        _fund_flow_state.clear()
        _order_book_state.clear()
        _divergence_state.clear()
        _rt_daily_date = today


def _rt_throttled(state: dict, check_interval: int, now: float = None) -> bool:
    """节流判断：距上次检查不足 check_interval 秒则跳过"""
    if now is None:
        now = time.time()
    last = state.get('last_check')
    if last and (now - last) < check_interval:
        return True
    return False


def _gen_state(stock_code, last_signal):
    """生成状态变更记录"""
    return {
        'last_signal': last_signal,
        'last_time': time.time(),
    }


# ---------- 功能一：主力资金流向 ----------

def check_fund_flow_signal(stock_code, fund_flow: dict, metrics: dict,
                           feature_cfg: dict) -> Optional[dict]:
    """
    主力资金流向 → 资金信号

    Returns:
        None 或 {'signal': str, 'reason': str, 'msg_type': 'fund_flow', ...}
    """
    _reset_rt_daily_state()
    if not fund_flow or 'main_net' not in fund_flow:
        return None

    cfg = feature_cfg or {}
    check_interval = cfg.get('check_interval', 300)
    net_inflow_th = cfg.get('net_inflow_th', 1_000_000)
    net_outflow_th = cfg.get('net_outflow_th', -1_000_000)
    ratio_th = cfg.get('ratio_th', 0.05)

    state = _fund_flow_state.get(stock_code, {})
    if _rt_throttled(state, check_interval):
        return None

    main_net = fund_flow['main_net']
    if main_net is None:
        return None

    pct = metrics.get('price_change_pct', 0) if metrics else 0
    signal = None
    reason = None

    try:
        # 主力净流入占比（相对价格粗估，仅用于说明）
        if main_net >= net_inflow_th and pct > 0:
            signal = 'fund_inflow_bull'
            reason = f"主力净流入 {main_net/10000:.0f}万元，价格同步上涨，资金看多"
        elif main_net <= net_outflow_th and pct < 0:
            signal = 'fund_outflow_bear'
            reason = f"主力净流出 {abs(main_net)/10000:.0f}万元，价格下跌，资金看空"
        elif main_net <= net_outflow_th and pct > 0:
            signal = 'fund_divergence_top'
            reason = f"价格上涨但主力净流出 {abs(main_net)/10000:.0f}万元，量价背离（诱多警示）"
        elif main_net >= net_inflow_th and pct < 0:
            signal = 'fund_divergence_bottom'
            reason = f"价格下跌但主力净流入 {main_net/10000:.0f}万元，资金抄底（逆势吸筹）"
    except Exception as e:
        log(f"资金流判断异常 ({stock_code}): {e}", level="WARNING")
        return None

    if signal is None:
        _fund_flow_state[stock_code] = {**state, 'last_check': time.time()}
        return None

    # 同信号节流：避免连续重复推送
    if state.get('last_signal') == signal and \
            (state.get('last_time', 0) and (time.time() - state['last_time']) < check_interval):
        return None

    # 全局限流：避免过多实盘告警
    if _rt_global_throttled():
        log(f"{stock_code} 实盘告警全局限流中，跳过", level="DEBUG")
        return None

    _fund_flow_state[stock_code] = {
        **state,
        'last_signal': signal,
        'last_time': time.time(),
        'last_check': time.time(),
    }

    _rt_record_alert()

    return {
        'msg_type': 'fund_flow',
        'signal': signal,
        'reason': reason,
        'main_net': main_net,
        'super_net': fund_flow.get('super_net'),
        'large_net': fund_flow.get('large_net'),
    }


def build_fund_flow_message(stock, signal: dict) -> str:
    """构建主力资金流向推送消息"""
    stock_code = stock.get('code', 'UNKNOWN')
    stock_name = stock.get('name', stock_code)
    signal_type = signal.get('signal', '')

    meta = {
        'fund_inflow_bull':      ('🟢', '主力吸筹', '资金与价格同步看多'),
        'fund_outflow_bear':     ('🔴', '主力出货', '资金与价格同步看空'),
        'fund_divergence_top':   ('⚠️', '量价背离·诱多', '价格上涨但资金流出'),
        'fund_divergence_bottom':('🟡', '量价背离·吸筹', '价格下跌但资金流入'),
    }.get(signal_type, ('📊', '资金异动', ''))

    icon, label, desc = meta
    main_net = signal.get('main_net', 0)
    super_net = signal.get('super_net') or 0
    large_net = signal.get('large_net', 0)

    # 资金规模分析
    main_net_abs = abs(main_net) / 10000  # 转万元
    if main_net_abs >= 5000:
        fund_scale = "大资金介入"
    elif main_net_abs >= 1000:
        fund_scale = "中等资金"
    elif main_net_abs >= 100:
        fund_scale = "小规模资金"
    else:
        fund_scale = "资金规模较小"

    # 根据信号类型给出建议
    advice_map = {
        'fund_inflow_bull': "💡 主力资金配合上涨，趋势健康；可继续持有或顺势加仓",
        'fund_outflow_bear': "💡 主力资金离场，注意风险；建议减仓或观望",
        'fund_divergence_top': "💡 价涨资流=诱多嫌疑，警惕冲高回落；不宜追高",
        'fund_divergence_bottom': "💡 价跌资进=主力吸筹，关注止跌信号；可分批布局",
    }
    advice = advice_map.get(signal_type, "💡 关注后续资金流向变化")

    time_str = datetime.now().strftime('%H:%M:%S')
    return f"""{icon} 【{label}】{desc}

📊 {stock_name} ({stock_code})
💰 主力净流入：{main_net/10000:+,.0f}万元 ({fund_scale})
🧩 超大单净流入：{super_net/10000:+,.0f}万元
🧩 大单净流入：{large_net/10000:+,.0f}万元
🔍 {signal.get('reason', '')}
⏰ 时间：{time_str}

{advice}"""


# ---------- 功能二：五档盘口 ----------

def check_order_book_signal(stock_code, order_book: dict,
                            feature_cfg: dict) -> Optional[dict]:
    """
    五档盘口 → 盘口信号（委比失衡 / 封板 / 巨量封单）

    Returns:
        None 或 {'signal': str, 'reason': str, 'msg_type': 'order_book', ...}
    """
    _reset_rt_daily_state()
    if not order_book:
        return None

    cfg = feature_cfg or {}
    check_interval = cfg.get('check_interval', 120)
    vi_high = cfg.get('vi_ratio_high', 0.6)
    vi_low = cfg.get('vi_ratio_low', -0.6)
    seal_hand_th = cfg.get('seal_qty_th', 50000)  # 封单量阈值(手)

    state = _order_book_state.get(stock_code, {})
    if _rt_throttled(state, check_interval):
        return None

    vi_ratio = order_book.get('vi_ratio')
    bid_total = order_book.get('bid_total', 0)
    ask_total = order_book.get('ask_total', 0)
    current_price = order_book.get('current_price')
    limit_up = order_book.get('limit_up')
    bids = order_book.get('bids') or []
    asks = order_book.get('asks') or []

    signal = None
    reason = None

    # 封板/炸板检测：现价触及涨停价 且 卖一量为 0（无卖盘=封死）
    at_limit = (current_price is not None and limit_up and
                abs(current_price - limit_up) < 1e-6)
    if at_limit:
        ask1_qty = asks[0][1] if asks and asks[0] else 0
        if ask1_qty is not None and ask1_qty <= 0:
            seal_qty = bids[0][1] if bids and bids[0] else 0
            signal = 'limit_up_sealed'
            reason = f"涨停封板，买一挂单 {seal_qty or 0} 手"
        else:
            signal = 'limit_opened'
            reason = "触及涨停但卖盘未清空（可能开板）"
    # 委比失衡（托盘/压盘）
    elif vi_ratio is not None:
        if vi_ratio >= vi_high:
            signal = 'bid_dominant'
            reason = f"买盘挂单显著大于卖盘（委比 {vi_ratio:.0%}），下方托盘"
        elif vi_ratio <= vi_low:
            signal = 'ask_dominant'
            reason = f"卖盘挂单显著大于买盘（委比 {vi_ratio:.0%}），上方压盘"

    if signal is None:
        _order_book_state[stock_code] = {**state, 'last_check': time.time()}
        return None

    # 同信号节流
    if state.get('last_signal') == signal and \
            (state.get('last_time', 0) and (time.time() - state['last_time']) < check_interval):
        return None

    # 全局限流
    if _rt_global_throttled():
        log(f"{stock_code} 实盘告警全局限流中，跳过", level="DEBUG")
        return None

    _order_book_state[stock_code] = _gen_state(stock_code, signal)
    _rt_record_alert()

    return {
        'msg_type': 'order_book',
        'signal': signal,
        'reason': reason,
        'vi_ratio': vi_ratio,
        'bid_total': bid_total,
        'ask_total': ask_total,
    }


def build_order_book_message(stock, signal: dict) -> str:
    """构建五档盘口推送消息"""
    stock_code = stock.get('code', 'UNKNOWN')
    stock_name = stock.get('name', stock_code)
    signal_type = signal.get('signal', '')

    meta = {
        'limit_up_sealed': ('🔒', '涨停封板', '封单充足，短期强势；关注开板风险'),
        'limit_opened':    ('⚠️', '涨停开板', '封板不稳，可能回落；谨慎追高'),
        'bid_dominant':    ('🟢', '下方托盘', '大单护盘，短期有支撑；关注是否真护盘'),
        'ask_dominant':    ('🔴', '上方压盘', '大单压制，上方阻力大；警惕出货'),
    }.get(signal_type, ('📊', '盘口异动', '关注后续走势'))
    icon, label, advice = meta

    vi_ratio = signal.get('vi_ratio')
    vi_str = f"{vi_ratio:.0%}" if vi_ratio is not None else "N/A"
    bid_total = signal.get('bid_total', 0)
    ask_total = signal.get('ask_total', 0)
    
    # 挂单对比分析
    if bid_total > 0 and ask_total > 0:
        bid_ask_ratio = bid_total / ask_total
        if bid_ask_ratio >= 2.0:
            ratio_analysis = f"买盘是卖盘 {bid_ask_ratio:.1f} 倍，买方明显占优"
        elif bid_ask_ratio >= 1.2:
            ratio_analysis = f"买盘略多于卖盘（{bid_ask_ratio:.1f}倍），多方稍强"
        elif bid_ask_ratio <= 0.5:
            ratio_analysis = f"卖盘是买盘 {1/bid_ask_ratio:.1f} 倍，卖方明显占优"
        elif bid_ask_ratio <= 0.8:
            ratio_analysis = f"卖盘略多于买盘（{1/bid_ask_ratio:.1f}倍），空方稍强"
        else:
            ratio_analysis = "买卖盘基本均衡"
    else:
        ratio_analysis = ""

    time_str = datetime.now().strftime('%H:%M:%S')

    message = f"""{icon} 【{label}】{signal.get('reason', '')}

📊 {stock_name} ({stock_code})
🛒 买盘挂单：{bid_total:,.0f} 手
🛍️ 卖盘挂单：{ask_total:,.0f} 手
⚖️ 委比：{vi_str}
⏰ 时间：{time_str}

{ratio_analysis}
💡 {advice}"""

    return message


# ---------- 功能八：量价背离 ----------

def check_divergence_signal(stock_code, history: list,
                            feature_cfg: dict,
                            rsi: Optional[float] = None) -> Optional[dict]:
    """
    量价背离检测 → 顶背离/底背离信号

    Returns:
        None 或 {'signal': str, 'reason': str, 'msg_type': 'divergence', ...}
    """
    _reset_rt_daily_state()
    if not history:
        return None

    cfg = feature_cfg or {}
    check_interval = cfg.get('check_interval', 300)
    window = cfg.get('window', 5)

    state = _divergence_state.get(stock_code, {})
    if _rt_throttled(state, check_interval):
        return None

    detection = detect_divergence(history, rsi=rsi, window=window)
    if detection is None:
        _divergence_state[stock_code] = {**state, 'last_check': time.time()}
        return None

    sig_type = detection['type']
    signal = 'divergence_top' if sig_type == 'top' else 'divergence_bottom'

    # 同信号节流
    if state.get('last_signal') == signal and \
            (state.get('last_time', 0) and (time.time() - state['last_time']) < check_interval):
        return None

    # 全局限流
    if _rt_global_throttled():
        log(f"{stock_code} 实盘告警全局限流中，跳过", level="DEBUG")
        return None

    _divergence_state[stock_code] = _gen_state(stock_code, signal)
    _rt_record_alert()

    return {
        'msg_type': 'divergence',
        'signal': signal,
        'strength': detection['strength'],
        'reason': '顶背离：价格创新高但量能/RSI 未跟上，警惕见顶' if sig_type == 'top'
                  else '底背离：价格创新低但量能回升，关注反弹',
    }


def build_divergence_message(stock, signal: dict) -> str:
    """构建量价背离推送消息"""
    stock_code = stock.get('code', 'UNKNOWN')
    stock_name = stock.get('name', stock_code)
    signal_type = signal.get('signal', '')
    strength = signal.get('strength', 0)

    if signal_type == 'divergence_top':
        icon, label = '⚠️', '顶背离·见顶警示'
        # 背离强度分析
        if strength >= 0.8:
            strength_desc = "强背离：见顶概率高"
            advice = "💡 强背离信号，建议减仓或设置紧止损，警惕大幅回调"
        elif strength >= 0.6:
            strength_desc = "中等背离：注意风险"
            advice = "💡 中等背离，关注量能变化，若继续缩量上涨可适当减仓"
        else:
            strength_desc = "弱背离：观察确认"
            advice = "💡 弱背离信号，需等待更多确认，不宜急于操作"
    else:
        icon, label = '🟡', '底背离·反弹关注'
        if strength >= 0.8:
            strength_desc = "强背离：反弹概率高"
            advice = "💡 强底背离，可关注逢低布局机会，分批建仓为宜"
        elif strength >= 0.6:
            strength_desc = "中等背离：关注企稳"
            advice = "💡 中等背离，观察是否站稳均线，再考虑介入"
        else:
            strength_desc = "弱背离：等待信号"
            advice = "💡 弱背离信号，等待更明确的止跌信号"

    time_str = datetime.now().strftime('%H:%M:%S')

    return f"""{icon} 【{label}】背离强度 {strength:.0%}

📊 {stock_name} ({stock_code})
🔍 {signal.get('reason', '')}
📊 {strength_desc}
⏰ 时间：{time_str}

{advice}"""

