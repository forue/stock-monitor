#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通知推送模块 - QQ C2C 推送 / 阶梯式递增告警 / 文件备份

阶梯式递增告警规则：
  - 首次异动 → 立即推送
  - 趋势延续 → 同向 + 偏离上次告警价 ≥ 2% → 再推送（趋势加速）
  - 方向反转 → 反向 + 偏离上次告警价 ≥ 1.5% → 再推送（反弹/回落）
  - 时间衰减 → 距上次告警 ≥ 30分钟 + 偏离 ≥ 1% → 再推送
  - 极端行情 → 涨跌幅 ≥ 6% → 不检查间隔，直接推送
  - 冷却保护 → 距上次推送 < 180s → 跳过（除非极端）
  - 每日上限 → 非极端: 8次/天 | 极端: 不计入上限
"""

import time
import json
import os
import requests
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional

from lib.logger import log
from lib.close_cache import get_prev_close

# ============ QQ C2C 推送 ============

_qq_token_cache = None
_qq_token_expire = 0


def qq_get_access_token(app_id: str, client_secret: str) -> Optional[str]:
    """获取 QQ 开放平台 AccessToken (带缓存)"""
    global _qq_token_cache, _qq_token_expire
    now = time.time()
    if _qq_token_cache and now < _qq_token_expire - 60:
        return _qq_token_cache

    url = "https://bots.qq.com/app/getAppAccessToken"

    try:
        resp = requests.post(url, json={
            "appId": app_id,
            "clientSecret": client_secret,
        }, headers={'Content-Type': 'application/json'}, timeout=10)
        resp.raise_for_status()

        result = resp.json()
        token = result.get('access_token')
        expires_in = int(result.get('expires_in', 7200))

        if token:
            _qq_token_cache = token
            _qq_token_expire = now + expires_in
            log(f"QQ AccessToken 获取成功，有效期 {expires_in}s")
            return token
        else:
            log(f"QQ AccessToken 获取失败：{result}", level="ERROR")
            return None

    except requests.HTTPError as e:
        body = e.response.text[:500] if e.response else ''
        log(f"QQ AccessToken HTTP 错误 {e.response.status_code if e.response else '?'}: {body}", level="ERROR")
        return None
    except Exception as e:
        log(f"QQ AccessToken 请求异常：{e}", level="ERROR")
        return None


def qq_send_c2c_message(app_id: str, client_secret: str, user_openid: str, content: str) -> bool:
    """发送 QQ C2C 私聊消息"""
    token = qq_get_access_token(app_id, client_secret)
    if not token:
        return False

    url = f"https://api.sgroup.qq.com/v2/users/{user_openid}/messages"
    payload = {
        "content": content,
        "msg_type": 0,
    }

    try:
        resp = requests.post(url, json=payload, headers={
            'Authorization': f'QQBot {token}',
            'Content-Type': 'application/json',
            'X-Union-Appid': app_id,
        }, timeout=10)

        if resp.status_code >= 400:
            body = resp.text[:500]
            log(f"QQ C2C 推送失败 HTTP {resp.status_code}: {body}", level="ERROR")
            return False

        result = resp.json()
        msg_id = result.get('id', '')
        log(f"QQ C2C 消息发送成功！msg_id={msg_id}")
        return True

    except Exception as e:
        log(f"QQ C2C 消息发送异常：{e}", level="ERROR")
        return False


# ============ 阶梯式递增告警状态 ============

_escalation_state = {}
_daily_date = None

# 极端行情阈值（涨跌幅绝对值）
EXTREME_PCT_THRESHOLD = 6.0

# 阶梯规则参数
COOLDOWN_SECONDS = 180           # 最小冷却间隔
TREND_DEVIATION = 0.02           # 趋势延续：同向偏离 ≥ 2%
REVERSAL_DEVIATION = 0.015       # 方向反转：反向偏离 ≥ 1.5%
TIME_DECAY_SECONDS = 1800        # 时间衰减：30 分钟
TIME_DECAY_DEVIATION = 0.01      # 时间衰减：偏离 ≥ 1%
MAX_DAILY_NORMAL = 8             # 非极端每日上限
MAX_DAILY_TOTAL = 15             # 绝对每日上限（含极端）

# ============ 涨停/跌停状态追踪 ============
# {stock_code: {'at_limit': bool, 'limit_type': 'up'|'down', 'limit_price': float,
#               'first_limit_time': float, 'below_limit_since': float,
#               'reseal_time': float, 'break_count': int}}
_limit_state = {}
_limit_state_date = None

# 破板检测参数（可通过 config.json 的 limit_detection 覆盖）
DEFAULT_LIMIT_DETECTION = {
    'min_limit_duration': 30,      # 最短封板时长（秒），短于此视为假封板
    'break_confirm_interval': 10,  # 破板确认最小间隔（秒），防止瞬间抖动误报
    'min_reseal_duration': 60,     # 回封最短时长（秒），防止回封瞬间又破板
    'log_wait_interval': 60,       # 破板等待日志最小间隔（秒），防止刷屏
}

# 当前生效的检测参数（由 set_limit_detection_config 初始化）
_limit_detection = {**DEFAULT_LIMIT_DETECTION}

# 破板详情缓存（用于消息构建），带过期清理
_break_detail_cache = {}  # {stock_code: {'limit_price': float, 'limit_duration': float, 'break_count': int, 'expire_at': float}}
_BREAK_CACHE_TTL = 300  # 破板详情缓存有效期（秒）


def set_limit_detection_config(config: dict):
    """从配置文件加载破板检测参数"""
    global _limit_detection
    user_cfg = config.get('limit_detection', {})
    _limit_detection = {**DEFAULT_LIMIT_DETECTION, **user_cfg}
    log(f"破板检测参数已加载: {_limit_detection}")


def _get_limit_detection(key: str) -> float:
    """获取破板检测参数"""
    return _limit_detection.get(key, DEFAULT_LIMIT_DETECTION.get(key, 0))


def _cleanup_break_cache():
    """清理过期的破板详情缓存"""
    now = time.time()
    expired = [k for k, v in _break_detail_cache.items() if v.get('expire_at', 0) < now]
    for k in expired:
        del _break_detail_cache[k]


def _calc_limit_price(close_price: float, limit_pct: float, direction: str) -> float:
    """
    精确计算涨跌停价
    
    A股规则：涨跌停价 = round(昨收 × (1 ± 幅度), 2)，四舍五入到分
    
    由于四舍五入，实际涨跌幅会在 9.85%~10.15% 之间浮动：
        昨收 10.15 → 涨停 11.17 → 实际 +10.049%
        昨收 12.34 → 涨停 13.57 → 实际 +9.968%
        昨收 5.67  → 涨停 6.24  → 实际 +10.053%
    
    使用 Decimal 确保四舍五入（ROUND_HALF_UP），而非 Python 默认的银行家舍入
    
    Args:
        close_price: 昨收价
        limit_pct: 涨跌停幅度百分比 (10.0, 20.0, 5.0, 30.0)
        direction: 'up' 或 'down'
    Returns:
        涨跌停价（精确到分）
    """
    close = Decimal(str(close_price))
    pct = Decimal(str(limit_pct))
    
    if direction == 'up':
        multiplier = Decimal('1') + pct / Decimal('100')
    else:
        multiplier = Decimal('1') - pct / Decimal('100')
    
    # 四舍五入到分（ROUND_HALF_UP）
    result = (close * multiplier).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return float(result)


def _is_at_limit_price(current_price: float, limit_price: float, direction: str) -> bool:
    """
    判断当前价格是否处于涨跌停价
    
    使用精确价格对比，容差 0.005（半分），因为行情数据可能有精度误差
    """
    tol = 0.005
    if direction == 'up':
        return current_price >= (limit_price - tol)
    else:
        return current_price <= (limit_price + tol)


def _get_limit_pct(stock_code: str, stock_name: str = None) -> float:
    """
    根据股票代码判断涨跌停幅度
    
    A股规则（2026年7月6日主板ST调整后）：
    - 主板 (60xxxx, 00xxxx): ±10%（ST与普通股票一致）
    - 创业板 (30xxxx): ±20%（ST与普通股票一致）
    - 科创板 (68xxxx): ±20%（ST与普通股票一致）
    - 北交所 (8xxxxx, 4xxxxx): ±30%（ST与普通股票一致）
    
    历史变更：
    - 2020年创业板改革：创业板ST从5%调整为20%
    - 2020年科创板设立：科创板ST即为20%
    - 2026年7月6日：主板ST从5%调整为10%（与主板普通股票统一）
    
    现在所有板块的ST股票涨跌幅与该板块普通股票一致。
    
    Args:
        stock_code: 股票代码
        stock_name: 股票名称（保留参数以兼容调用，现已不用于涨跌幅判断）
    """
    if stock_code.startswith('30'):
        return 20.0  # 创业板 ±20%
    elif stock_code.startswith('68'):
        return 20.0  # 科创板 ±20%
    elif stock_code.startswith('8') or stock_code.startswith('4'):
        return 30.0  # 北交所 ±30%
    else:
        return 10.0  # 主板 ±10%（2026年7月起ST与普通一致）


def _reset_limit_state():
    """跨天重置涨停状态"""
    global _limit_state_date
    today = datetime.now().date()
    if _limit_state_date is None:
        # 首次调用，只设置日期不清空状态
        _limit_state_date = today
    elif _limit_state_date != today:
        # 跨天，清空状态
        _limit_state.clear()
        _limit_state_date = today


def _get_limit_state(stock_code: str) -> dict:
    return _limit_state.get(stock_code, {})


def _set_limit_state(stock_code: str, state: dict):
    _limit_state[stock_code] = state


def _reset_daily_state():
    global _daily_date
    today = datetime.now().date()
    if _daily_date != today:
        _escalation_state.clear()
        _daily_date = today


def _get_state(stock_code: str) -> dict:
    return _escalation_state.get(stock_code, {})


def _set_state(stock_code: str, state: dict):
    _escalation_state[stock_code] = state


def _should_escalate(stock_code: str, stock_name: str, current_price: float,
                     change_pct: float, notif: dict,
                     escalation_config: dict = None,
                     volatility: float = None,
                     volume_ratio: float = None,
                     close_price: float = None,
                     high_price: float = None) -> Optional[str]:
    """
    判断是否应该发送本次告警

    Args:
        volatility: 当前价格波动率（用于动态冷却计算），如 metrics['price_change_rate']
        volume_ratio: 当前量比，用于后告警抑制判断
    Returns:
        None  → 不发
        str   → 场景 key
    """
    _reset_daily_state()
    _reset_limit_state()
    state = _get_state(stock_code)
    now = time.time()
    abs_pct = abs(change_pct)
    esc = escalation_config or {}

    max_daily = esc.get('max_daily_normal', notif.get('max_daily_alerts_per_stock', MAX_DAILY_NORMAL))
    max_total = esc.get('max_daily_total', MAX_DAILY_TOTAL)
    min_interval = notif.get('min_alert_interval', 600)
    extreme_pct = esc.get('extreme_pct_threshold', EXTREME_PCT_THRESHOLD)

    is_extreme = abs_pct >= extreme_pct
    current_dir = "up" if change_pct > 0 else "down"

    # ============ 涨停/跌停状态追踪 ============
    limit_state = _get_limit_state(stock_code)
    limit_pct = _get_limit_pct(stock_code, stock_name)  # 传入stock_name以支持ST判断

    # 精确涨跌停价计算
    # 优先使用缓存的昨收价（更准确），其次使用数据源的close_price
    exact_limit_price = None
    limit_direction = 'up' if change_pct > 0 else 'down'
    
    prev_close = get_prev_close(stock_code)  # 从缓存获取
    if not prev_close or prev_close <= 0:
        prev_close = close_price  # fallback到数据源
    
    if prev_close and prev_close > 0:
        exact_limit_price = _calc_limit_price(prev_close, limit_pct, limit_direction)

    # 涨停/跌停判定：精确价格对比
    if exact_limit_price is not None:
        at_limit_now = _is_at_limit_price(current_price, exact_limit_price, limit_direction)
    else:
        # fallback：百分比阈值（精度较低）
        at_limit_now = abs_pct >= (limit_pct - 0.5)

    was_at_limit = limit_state.get('at_limit', False)
    limit_type = limit_state.get('limit_type')
    below_limit_since = limit_state.get('below_limit_since')
    first_limit_time = limit_state.get('first_limit_time', 0)
    break_count = limit_state.get('break_count', 0)

    if at_limit_now:
        if not was_at_limit:
            # 首次达到涨/跌停，或回封后再次达到
            _set_limit_state(stock_code, {
                'at_limit': True,
                'limit_type': limit_direction,
                'limit_price': exact_limit_price or current_price,
                'first_limit_time': now,
                'below_limit_since': None,
                'reseal_time': now if break_count > 0 else None,  # 回封时记录时间
                'break_count': break_count,  # 保留破板次数
            })
            if break_count > 0:
                log(f"{stock_name} 回封（第{break_count + 1}次封板）：{current_price:.2f} @ {exact_limit_price:.2f}")
            else:
                log(f"{stock_name} 涨/跌停：{current_price:.2f} @ {exact_limit_price:.2f} ({change_pct:+.2f}%)")
        else:
            # 持续涨/跌停：清除破板计时
            if below_limit_since is not None:
                _set_limit_state(stock_code, {**limit_state, 'below_limit_since': None})
            if state:  # 已有过告警则静默
                log(f"{stock_name} 持续{'涨停' if limit_type == 'up' else '跌停'}中，静默")
                return None
    else:
        if was_at_limit:
            # 涨/跌停后首次跌破 → 开始计时
            if below_limit_since is None:
                # 检查是否需要验证回封时长（如果之前已经破过板）
                reseal_time = limit_state.get('reseal_time')
                min_reseal = _get_limit_detection('min_reseal_duration')
                if break_count > 0 and reseal_time:
                    reseal_duration = now - reseal_time
                    if reseal_duration < min_reseal:
                        # 回封时间太短，视为抖动，不触发新破板
                        log(f"{stock_name} 回封仅{reseal_duration:.0f}s(<{min_reseal}s)，视为抖动")
                        _set_limit_state(stock_code, {'at_limit': False, 'break_count': break_count})
                        return None

                _set_limit_state(stock_code, {
                    'at_limit': True,
                    'limit_type': limit_type,
                    'limit_price': limit_state.get('limit_price'),
                    'first_limit_time': first_limit_time,
                    'below_limit_since': now,
                    'break_count': break_count,
                })
                log(f"{stock_name} 首次跌破{'涨停' if limit_type == 'up' else '跌停'}，等待确认...")
                return None
            else:
                # 已计时，检查是否满足破板条件
                below_duration = now - below_limit_since
                limit_duration = now - first_limit_time if first_limit_time else 0

                min_limit_dur = _get_limit_detection('min_limit_duration')
                break_confirm = _get_limit_detection('break_confirm_interval')

                if limit_duration >= min_limit_dur and below_duration >= break_confirm:
                    # 清理过期缓存
                    _cleanup_break_cache()
                    
                    new_break_count = break_count + 1
                    _set_limit_state(stock_code, {'at_limit': False, 'break_count': new_break_count})
                    
                    # 缓存破板详情，供消息构建使用（带过期时间）
                    _break_detail_cache[stock_code] = {
                        'limit_price': limit_state.get('limit_price'),
                        'limit_duration': limit_duration,
                        'break_count': new_break_count,
                        'break_time': now,
                        'expire_at': now + _BREAK_CACHE_TTL,
                    }
                    
                    log(f"{stock_name} 破板确认（第{new_break_count}次）：封板{limit_duration:.0f}s，跌破{below_duration:.0f}s，当前{change_pct:+.2f}%")
                    if limit_type == 'up':
                        return "limit_break_up"
                    else:
                        return "limit_break_down"
                else:
                    # 条件不满足，继续等待（控制日志频率）
                    last_wait_log = limit_state.get('last_wait_log', 0)
                    log_interval = _get_limit_detection('log_wait_interval')
                    if now - last_wait_log >= log_interval:
                        log(f"{stock_name} 破板等待中：封板{limit_duration:.0f}s(<{min_limit_dur}s) 或 跌破{below_duration:.0f}s(<{break_confirm}s)")
                        _set_limit_state(stock_code, {**limit_state, 'last_wait_log': now})
                    return None
        else:
            # 从未在涨/跌停
            _set_limit_state(stock_code, {'at_limit': False, 'break_count': 0})

    # ============ 后告警抑制 ============
    # 上次告警后，如果量小且波动低，跳过
    if state:
        last_time = state.get("time", 0)
        elapsed_since_alert = now - last_time
        if elapsed_since_alert < 600:  # 10分钟内
            if volume_ratio is not None and volume_ratio < 1.5 and volatility is not None and volatility < 0.03:
                log(f"{stock_name} 后告警抑制：量比{volume_ratio:.2f}<1.5 且 波动率{volatility:.2%}<3%，跳过")
                return None

    # 第一次告警：直接允许
    if not state:
        return "first"

    count_today = state.get("count_today", 0)
    last_time = state.get("time", 0)
    elapsed = now - last_time

    # 动态冷却：波动率越高冷却越长
    base_cooldown = esc.get('cooldown_seconds', COOLDOWN_SECONDS)
    if volatility is not None and volatility > 0:
        multiplier = max(0.5, min(3.0, volatility * 100))
        cooldown = int(base_cooldown * multiplier)
    else:
        cooldown = base_cooldown

    # 冷却保护：极端行情绕过
    if elapsed < cooldown and not is_extreme:
        log(f"{stock_name} 冷却中 ({elapsed:.0f}s/{cooldown}s, 波动率{volatility:.2%})，跳过")
        return None

    # 短期防重复：min_interval 秒内不重复（非极端）
    if elapsed < min_interval and not is_extreme:
        return None

    # 极端行情强制通道：不检查阶梯，不计入非极端上限
    if is_extreme:
        if count_today >= max_total:
            log(f"{stock_name} 今日已达绝对上限 ({max_total}次)，跳过")
            return None
        return "extreme"

    # 非极端每日上限
    if count_today >= max_daily:
        log(f"{stock_name} 今日已达非极端上限 ({max_daily}次)，跳过")
        return None

    # 阶梯判断
    last_price = state.get("alert_price")
    last_dir = state.get("direction")
    if not last_price or not last_dir:
        return "first"

    trend_dev = esc.get('trend_deviation', TREND_DEVIATION)
    reversal_dev = esc.get('reversal_deviation', REVERSAL_DEVIATION)
    decay_secs = esc.get('time_decay_seconds', TIME_DECAY_SECONDS)
    decay_dev = esc.get('time_decay_deviation', TIME_DECAY_DEVIATION)

    deviation = (current_price - last_price) / last_price
    abs_deviation = abs(deviation)

    # 趋势延续：同向 + 偏离达标
    if current_dir == last_dir and abs_deviation >= trend_dev:
        return "trend_continue_up" if current_dir == "up" else "trend_continue_down"

    # 方向反转：反向 + 偏离达标
    if current_dir != last_dir and abs_deviation >= reversal_dev:
        return "rebound" if current_dir == "up" else "pullback"

    # 时间衰减：超时 + 偏离达标
    if elapsed >= decay_secs and abs_deviation >= decay_dev:
        return "time_decay"

    log(f"{stock_name} 未达阶梯条件 (偏离{abs_deviation:.2%}, 同向={current_dir==last_dir})")
    return None


def _update_state(stock_code: str, current_price: float, change_pct: float, scenario: str):
    """更新递增告警状态"""
    state = _get_state(stock_code)
    count = state.get("count_today", 0) + 1
    direction = "up" if change_pct > 0 else "down"

    _set_state(stock_code, {
        "alert_price": current_price,
        "direction": direction,
        "time": time.time(),
        "count_today": count,
        "scenario": scenario,
    })


# ============ 告警推送入口 ============

def send_alert(stock: dict, stock_data: dict, metrics: dict, config: dict,
               triggered_l1: list = None, triggered_l2: list = None,
               base_dir: Path = None, alerts_file: Path = None) -> bool:
    """
    推送异动告警：阶梯式递增判断 → 场景分类 → QQ 推送 + 文件备份
    """
    from lib.alerter import build_alert_message, classify_scenario

    if triggered_l1 is None:
        triggered_l1 = []
    if triggered_l2 is None:
        triggered_l2 = []

    stock_code = stock['code']
    stock_name = stock.get('name', stock_code)
    current_price = stock_data['current_price']
    change_pct = metrics['price_change_pct']

    notif = config.get('notification', {})
    escalation_config = config.get('escalation', {})

    # 阶梯式递增判断（传入波动率用于动态冷却，量比用于后告警抑制，昨收/最高价用于精确破板检测）
    volatility = metrics.get('price_change_rate')
    volume_ratio = metrics.get('volume_ratio')
    escalation_scenario = _should_escalate(stock_code, stock_name,
                                           current_price, change_pct, notif,
                                           escalation_config, volatility,
                                           volume_ratio,
                                           close_price=stock_data.get('close_price'),
                                           high_price=stock_data.get('high_price'))
    if escalation_scenario is None:
        return False

    # 保存旧状态（更新前），用于消息中的参考锚点
    old_state = _get_state(stock_code) if _get_state(stock_code) else None

    # 场景分类
    if escalation_scenario in ("first", "time_decay", "extreme"):
        scenario = classify_scenario(metrics, old_state)
    else:
        scenario = escalation_scenario

    # 构建破板详情（如果是破板场景，使用缓存的破板信息）
    limit_info = None
    if scenario in ("limit_break_up", "limit_break_down"):
        limit_info = _break_detail_cache.pop(stock_code, None)

    # 更新状态
    _update_state(stock_code, current_price, change_pct, scenario)
    new_state = _get_state(stock_code)

    # 构建消息中的参考锚点（使用旧状态，如是首次则为 None）
    if old_state and old_state.get("alert_price"):
        last_alert_info = {
            "alert_price": old_state.get("alert_price"),
            "direction": old_state.get("direction"),
        }
    else:
        last_alert_info = None

    # 构建消息（带场景、上次参考价和破板详情）
    message = build_alert_message(stock, stock_data, metrics,
                                  triggered_l1, triggered_l2,
                                  scenario, last_alert_info, limit_info)
    log(f"准备推送告警 [{scenario}]：{stock_name} {change_pct:+.2f}% (今日第{new_state['count_today']}次)")

    # QQ 推送（优先从环境变量读取，config.json 作为 fallback）
    app_id = os.environ.get('QQ_APP_ID') or notif.get('qq_app_id', '')
    client_secret = os.environ.get('QQ_CLIENT_SECRET') or notif.get('qq_client_secret', '')
    user_openid = os.environ.get('QQ_USER_OPENID') or notif.get('user_openid', '')

    success = False
    if all([app_id, client_secret, user_openid]):
        success = qq_send_c2c_message(app_id, client_secret, user_openid, message)
    else:
        log("QQ 推送配置不完整，退化为写文件", level="ERROR")

    # 写入文件备份
    if base_dir and alerts_file:
        _write_alert_file(stock_code, stock_name, current_price,
                          change_pct, metrics, message, base_dir, alerts_file)

    return success


# ============ 告警文件写入 ============

def _rotate_file(filepath: Path, max_size: int = 5 * 1024 * 1024, max_backups: int = 3):
    """通用文件轮转：超过 max_size 时轮转，保留 max_backups 个备份"""
    if not filepath.exists():
        return
    
    try:
        file_size = filepath.stat().st_size
        if file_size <= max_size:
            return
        
        # 删除最旧的备份
        oldest = filepath.with_suffix(f"{filepath.suffix}.{max_backups}")
        if oldest.exists():
            oldest.unlink()
        
        # 轮转：.2→.3, .1→.2, 当前→.1
        for i in range(max_backups - 1, 0, -1):
            src = filepath.with_suffix(f"{filepath.suffix}.{i}")
            if src.exists():
                dst = filepath.with_suffix(f"{filepath.suffix}.{i + 1}")
                src.rename(dst)
        
        # 当前文件 → .1
        filepath.rename(filepath.with_suffix(f"{filepath.suffix}.1"))
        log(f"文件轮转完成：{filepath.name} (原大小: {file_size // 1024}KB)")
    except Exception as e:
        log(f"文件轮转失败 ({filepath.name}): {e}", level="WARNING")


def _write_alert_file(stock_code: str, stock_name: str, current_price: float,
                      change_pct: float, metrics: dict, message: str,
                      base_dir: Path, alerts_file: Path):
    """写入告警文件备份（带轮转）"""
    alert_record = {
        'timestamp': datetime.now().isoformat(),
        'stock_code': stock_code,
        'stock_name': stock_name,
        'current_price': current_price,
        'change_pct': change_pct,
        'volume_ratio': metrics.get('volume_ratio') if metrics else None,
        'message': message,
        'channel': 'qqbot_c2c',
        'status': 'pending',
    }
    line = json.dumps(alert_record, ensure_ascii=False) + '\n'
    try:
        for alert_file in [base_dir / "logs" / "qq-alert-queue.jsonl", alerts_file]:
            try:
                _rotate_file(alert_file, max_size=5 * 1024 * 1024, max_backups=3)
            except Exception as e:
                log(f"告警文件轮转失败 ({alert_file.name}): {e}", level="WARNING")
            with open(alert_file, 'a', encoding='utf-8') as f:
                f.write(line)
        log("告警已写入文件备份")
    except Exception as e:
        log(f"写入告警文件失败：{e}", level="ERROR")


# ============ 交易信号推送 ===========

def send_trading_signal(stock: dict, signal: dict, config: dict,
                         base_dir: Path = None, alerts_file: Path = None) -> bool:
    """
    推送买卖信号：构建消息 → QQ 推送 + 文件备份
    """
    from lib.alerter import build_trading_signal_message

    stock_code = stock.get('code', 'UNKNOWN')
    stock_name = stock.get('name', stock_code)
    signal_type = signal.get('signal', 'buy')

    notif = config.get('notification', {})

    # 构建消息
    message = build_trading_signal_message(stock, signal)
    log(f"准备推送交易信号 [{signal_type}]：{stock_name} {signal_type}信号")

    # QQ 推送（优先从环境变量读取，config.json 作为 fallback）
    app_id = os.environ.get('QQ_APP_ID') or notif.get('qq_app_id', '')
    client_secret = os.environ.get('QQ_CLIENT_SECRET') or notif.get('qq_client_secret', '')
    user_openid = os.environ.get('QQ_USER_OPENID') or notif.get('user_openid', '')

    success = False
    if all([app_id, client_secret, user_openid]):
        success = qq_send_c2c_message(app_id, client_secret, user_openid, message)
    else:
        log("QQ 推送配置不完整，退化为写文件", level="ERROR")

    # 写入文件备份
    if base_dir and alerts_file:
        _write_alert_file(
            stock_code, stock_name,
            signal.get('close', 0),
            None, None, message, base_dir, alerts_file
        )

    return success


# ============ 实盘辅助功能推送（统一入口） ============

def send_feature_alert(stock: dict, signal: dict, config: dict,
                       base_dir: Path = None, alerts_file: Path = None) -> bool:
    """
    推送实盘辅助功能告警（主力资金流 / 五档盘口 / 量价背离）

    依据 signal['msg_type'] 选择对应的消息构建函数，走 QQ 推送 + 文件兜底。
    """
    from lib.alerter import (
        build_fund_flow_message,
        build_order_book_message,
        build_divergence_message,
    )

    stock_code = stock.get('code', 'UNKNOWN')
    stock_name = stock.get('name', stock_code)
    msg_type = signal.get('msg_type', '')

    builders = {
        'fund_flow':   build_fund_flow_message,
        'order_book':  build_order_book_message,
        'divergence':  build_divergence_message,
    }
    builder = builders.get(msg_type)
    if not builder:
        log(f"未知实盘告警类型: {msg_type}", level="WARNING")
        return False

    message = builder(stock, signal)
    log(f"准备推送实盘告警 [{msg_type}/{signal.get('signal')}]：{stock_name} {signal.get('reason', '')}")

    notif = config.get('notification', {})
    app_id = os.environ.get('QQ_APP_ID') or notif.get('qq_app_id', '')
    client_secret = os.environ.get('QQ_CLIENT_SECRET') or notif.get('qq_client_secret', '')
    user_openid = os.environ.get('QQ_USER_OPENID') or notif.get('user_openid', '')

    success = False
    if all([app_id, client_secret, user_openid]):
        success = qq_send_c2c_message(app_id, client_secret, user_openid, message)
    else:
        log("QQ 推送配置不完整，退化为写文件", level="ERROR")

    if base_dir and alerts_file:
        _write_alert_file(
            stock_code, stock_name,
            signal.get('current_price', 0),
            None, None, message, base_dir, alerts_file
        )

    return success

