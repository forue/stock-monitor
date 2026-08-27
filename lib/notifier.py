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
from pathlib import Path
from typing import Optional

from lib.logger import log

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
# {stock_code: {'at_limit': bool, 'limit_type': 'up'|'down', 'limit_price': float, 'broken_direction': str}}
_limit_state = {}
_limit_state_date = None


def _get_limit_pct(stock_code: str) -> float:
    """
    根据股票代码判断涨跌停幅度
    
    A股主板 (60xxxx, 00xxxx): ±10%
    创业板 (30xxxx): ±20%
    科创板 (68xxxx): ±20%
    ST股票: ±5% (需要额外判断，这里先返回默认值)
    """
    if stock_code.startswith('30'):
        return 20.0  # 创业板 ±20%
    elif stock_code.startswith('68'):
        return 20.0  # 科创板 ±20%
    else:
        return 10.0  # 主板 ±10%


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
                     volume_ratio: float = None) -> Optional[str]:
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
    
    # 根据股票代码获取涨跌停幅度
    limit_pct = _get_limit_pct(stock_code)
    
    # 涨停/跌停检测：涨跌幅 >= limit_pct - 0.5% 视为涨停/跌停
    at_limit_now = abs_pct >= (limit_pct - 0.5)
    
    # 检测破板：之前在涨停，现在不在了
    was_at_limit = limit_state.get('at_limit', False)
    limit_type = limit_state.get('limit_type')
    
    # 如果之前不在涨停状态，但当前在涨停，则更新状态
    if at_limit_now and not was_at_limit:
        _set_limit_state(stock_code, {
            'at_limit': True,
            'limit_type': 'up' if change_pct > 0 else 'down',
            'limit_price': current_price,
            'first_limit_time': now,
        })
        limit_label = f"±{limit_pct:.0f}%"
        log(f"{stock_name} 涨停/跌停检测：{change_pct:+.2f}% (阈值{limit_label})，进入涨停/跌停状态")
    
    # 更新涨停状态
    if at_limit_now:
        if not was_at_limit:
            _set_limit_state(stock_code, {
                'at_limit': True,
                'limit_type': 'up' if change_pct > 0 else 'down',
                'limit_price': current_price,
                'first_limit_time': now,
            })
            # 首次涨停，允许第一次告警
            pass
        else:
            # 持续涨停，静默（不触发常规告警）
            if state:  # 已有过告警
                log(f"{stock_name} 持续涨停中，静默")
                return None
    else:
        if was_at_limit:
            # 从涨停变为非涨停（破板）
            _set_limit_state(stock_code, {
                'at_limit': False,
                'broken_direction': current_dir,
                'broken_time': now,
            })
        else:
            _set_limit_state(stock_code, {'at_limit': False})

    # ============ 后告警抑制 ============
    # 上次告警后，如果量小且波动低，跳过（但破板检测已绕过此逻辑）
    if state:
        last_time = state.get("time", 0)
        elapsed_since_alert = now - last_time
        if elapsed_since_alert < 600:  # 10分钟内
            if volume_ratio is not None and volume_ratio < 1.5 and volatility is not None and volatility < 0.03:
                log(f"{stock_name} 后告警抑制：量比{volume_ratio:.2f}<1.5 且 波动率{volatility:.2%}<3%，跳过")
                return None
    
    # ============ 涨停/跌停破板特殊处理 ============
    # 如果检测到破板，立即返回（绕过所有后续检查）
    if was_at_limit and not at_limit_now:
        broken_direction = limit_state.get('broken_direction')
        if broken_direction and broken_direction != current_dir:
            log(f"{stock_name} 破板确认：{limit_type}停→{current_dir}，触发破板告警")
            _set_limit_state(stock_code, {'at_limit': False})
            if limit_type == 'up':
                return "limit_break_up"
            else:
                return "limit_break_down"

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

    # 阶梯式递增判断（传入波动率用于动态冷却，量比用于后告警抑制）
    volatility = metrics.get('price_change_rate')
    volume_ratio = metrics.get('volume_ratio')
    escalation_scenario = _should_escalate(stock_code, stock_name,
                                           current_price, change_pct, notif,
                                           escalation_config, volatility,
                                           volume_ratio)
    if escalation_scenario is None:
        return False

    # 保存旧状态（更新前），用于消息中的参考锚点
    old_state = _get_state(stock_code) if _get_state(stock_code) else None

    # 场景分类
    if escalation_scenario in ("first", "time_decay", "extreme"):
        scenario = classify_scenario(metrics, old_state)
    else:
        scenario = escalation_scenario

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

    # 构建消息（带场景和上次参考价）
    message = build_alert_message(stock, stock_data, metrics,
                                  triggered_l1, triggered_l2,
                                  scenario, last_alert_info)
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

