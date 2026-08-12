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
        header = '🔔 【买入信号】价量齐动 + 技术面共振'
        icon = '📈'
        reason_str = f'金叉：MA{int(ma_fast)} 上穿 MA{int(ma_slow)} ✅'
        filter_ok = close >= ma_filter * ma_filter_pct
        filter_str = f'MA{int(ma_filter)}：收盘价 ¥{close:.2f} 占比 {close/ma_filter*100:.1f}%' + \
            f' {"✅" if filter_ok else "❌"} (≥{ma_filter_pct:.0%})'
        rsi_str = f'RSI{rsi_period}：{rsi:.1f}' + \
            f' {"✅" if rsi and rsi < rsi_max else "❌"} (<{rsi_max})'
    else:
        header = '🔻 【卖出信号】技术面死叉'
        icon = '📉'
        reason_str = f'死叉：MA{int(ma_fast)} 下穿 MA{int(ma_slow)} ⚠️'
        filter_str = f'MA{int(ma_filter)}：收盘价 ¥{close:.2f}'
        rsi_str = f'RSI{rsi_period}：{rsi:.1f}' if rsi else 'RSI：N/A'

    from datetime import datetime
    time_str = datetime.now().strftime('%H:%M:%S')

    message = f"""{header}

📊 {stock_name} ({stock_code})
💰 当前价：¥{close:.2f}
{icon} {reason_str}
📊 {filter_str}
📉 {rsi_str}
⏰ 时间：{time_str}

    请关注{'买入机会' if signal_type == 'buy' else '卖出时机'}！{'📈' if signal_type == 'buy' else '🔻'}
"""
    return message


# ============ 实盘辅助功能：主力资金流 / 五档盘口 / 量价背离 ============

_fund_flow_state = {}
_order_book_state = {}
_divergence_state = {}
_rt_daily_date = None


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

    _fund_flow_state[stock_code] = {
        **state,
        'last_signal': signal,
        'last_time': time.time(),
        'last_check': time.time(),
    }

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
        'fund_inflow_bull':      ('🟢', '主力吸筹', '资金看多'),
        'fund_outflow_bear':     ('🔴', '主力出货', '资金看空'),
        'fund_divergence_top':   ('⚠️', '量价背离·诱多', '价格涨但资金流出'),
        'fund_divergence_bottom':('🟡', '量价背离·吸筹', '价格跌但资金流入'),
    }.get(signal_type, ('📊', '资金异动', ''))

    icon, label, desc = meta
    main_net = signal.get('main_net', 0)
    super_net = signal.get('super_net') or 0
    large_net = signal.get('large_net') or 0

    time_str = datetime.now().strftime('%H:%M:%S')
    return f"""{icon} 【{label}】{desc}

📊 {stock_name} ({stock_code})
💰 主力净流入：{main_net/10000:+.0f}万元
🧩 超大单净流入：{super_net/10000:+.0f}万元
🧩 大单净流入：{large_net/10000:+.0f}万元
🔍 {signal.get('reason', '')}
⏰ 时间：{time_str}

请结合盘面判断！{icon}"""


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

    _order_book_state[stock_code] = _gen_state(stock_code, signal)

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

    meta = {
        'limit_up_sealed': ('🔒', '涨停封板'),
        'limit_opened':    ('⚠️', '涨停开板'),
        'bid_dominant':    ('🟢', '下方托盘'),
        'ask_dominant':    ('🔴', '上方压盘'),
    }.get(signal.get('signal', ''), ('📊', '盘口异动'))
    icon, label = meta

    vi_ratio = signal.get('vi_ratio')
    vi_str = f"{vi_ratio:.0%}" if vi_ratio is not None else "N/A"
    time_str = datetime.now().strftime('%H:%M:%S')

    return f"""{icon} 【{label}】{signal.get('reason', '')}

📊 {stock_name} ({stock_code})
🛒 买盘挂单：{signal.get('bid_total', 0):.0f} 手
🛍️ 卖盘挂单：{signal.get('ask_total', 0):.0f} 手
⚖️ 委比：{vi_str}
⏰ 时间：{time_str}

请速速查看！{icon}"""


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

    _divergence_state[stock_code] = _gen_state(stock_code, signal)

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

    icon, label = ('⚠️', '顶背离·见顶警示') if signal_type == 'divergence_top' \
        else ('🟡', '底背离·反弹关注')
    strength = signal.get('strength', 0)
    time_str = datetime.now().strftime('%H:%M:%S')

    return f"""{icon} 【{label}】背离强度 {strength:.0%}

📊 {stock_name} ({stock_code})
🔍 {signal.get('reason', '')}
⏰ 时间：{time_str}

请结合趋势判断！{icon}"""

