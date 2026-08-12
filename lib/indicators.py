#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
技术指标计算模块 — MA / RSI / 金叉死叉判断

数据库依赖：
  stock_data 表存储了每次检查的 current_price（作为当日收盘价候选）
  按 DATE(timestamp) 分组，取每组最后一条作为当日收盘价
"""

import sqlite3
from typing import Optional, List, Tuple

from lib.logger import log


def get_close_history(db_path: str, stock_code: str, days: int) -> List[float]:
    """
    获取最近 N 个交易日的收盘价（每日最后一条记录）

    使用子查询按 rowid 取每天最后一条记录，确保每个交易日恰好一行。
    """
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DATE(timestamp) AS trade_date, current_price
                FROM stock_data
                WHERE stock_code = ?
                AND rowid IN (
                    SELECT MAX(rowid) FROM stock_data
                    WHERE stock_code = ?
                    GROUP BY DATE(timestamp)
                )
                ORDER BY trade_date ASC
                LIMIT ?
            """, (stock_code, stock_code, days))
            rows = cursor.fetchall()

        return [r[1] for r in rows]

    except Exception as e:
        log(f"获取收盘价历史失败 ({stock_code}): {e}", level="WARNING")
        return []


def calc_ma(prices: List[float], period: int) -> Optional[float]:
    """计算移动平均线，数据不足返回 None"""
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def calc_rsi(prices: List[float], period: int = 14) -> Optional[float]:
    """
    计算 RSI 相对强弱指标

    RSI = 100 - 100 / (1 + RS)
    RS = 平均涨幅 / 平均跌幅（指定周期内）
    """
    if len(prices) < period + 1:
        return None

    changes = [prices[i] - prices[i-1] for i in range(1, len(prices))]

    gains = [max(c, 0) for c in changes[-period:]]
    losses = [abs(min(c, 0)) for c in changes[-period:]]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def check_golden_cross(ma_fast_cur: float, ma_slow_cur: float,
                       ma_fast_prev: float, ma_slow_prev: float) -> bool:
    """金叉：快线上穿慢线（需前后对比）"""
    if ma_fast_prev is None or ma_slow_prev is None:
        return False
    return ma_fast_cur > ma_slow_cur and ma_fast_prev <= ma_slow_prev


def check_death_cross(ma_fast_cur: float, ma_slow_cur: float,
                        ma_fast_prev: float, ma_slow_prev: float) -> bool:
    """死叉：快线下穿慢线（需前后对比）"""
    if ma_fast_prev is None or ma_slow_prev is None:
        return False
    return ma_fast_cur < ma_slow_cur and ma_fast_prev >= ma_slow_prev


# ============ 状态管理（用于金叉/死叉判断需前后对比） ===========

_prev_ma_state = {}


def get_prev_ma(stock_code: str) -> Tuple[Optional[float], Optional[float]]:
    """获取上期 MA 值（快线, 慢线）"""
    state = _prev_ma_state.get(stock_code)
    if not state:
        return None, None
    return state.get('ma_fast'), state.get('ma_slow')


def save_curr_ma(stock_code: str, ma_fast: float, ma_slow: float):
    """保存本期 MA 值，供下期对比"""
    _prev_ma_state[stock_code] = {
        'ma_fast': ma_fast,
        'ma_slow': ma_slow,
    }


# ============ 量价历史查询与背离检测 ============

def get_price_volume_history(db_path: str, stock_code: str, days: int) -> List[dict]:
    """
    获取最近 N 个交易日的 (收盘价, 成交量)，每日取最后一条记录

    Returns:
        [{'date': 'YYYY-MM-DD', 'price': float, 'volume': float}, ...] 按日期升序
    """
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DATE(timestamp) AS trade_date, current_price, volume
                FROM stock_data
                WHERE stock_code = ?
                AND volume IS NOT NULL
                AND rowid IN (
                    SELECT MAX(rowid) FROM stock_data
                    WHERE stock_code = ?
                    GROUP BY DATE(timestamp)
                )
                ORDER BY trade_date ASC
                LIMIT ?
            """, (stock_code, stock_code, days))
            rows = cursor.fetchall()

        return [{'date': r[0], 'price': r[1], 'volume': r[2]} for r in rows]
    except Exception as e:
        log(f"获取量价历史失败 ({stock_code}): {e}", level="WARNING")
        return []


def detect_divergence(history: List[dict], rsi: Optional[float] = None,
                      rsi_max: float = 70, rsi_min: float = 30,
                      window: int = 5) -> Optional[dict]:
    """
    检测量价背离（顶背离 / 底背离）

    基于最近 window 个交易日：
    - 顶背离：价格段最高点创新高，但同期成交量峰值未创新高
    - 底背离：价格段最低点创新低，但同期成交量峰值未创新低

    Args:
        history: get_price_volume_history 的返回，需足够长度
        rsi: 可选，若提供则结合 RSI 判断（顶背离要求 RSI < rsi_max，底背离要求 RSI > rsi_min）
        window: 背离观察窗口（交易日数）

    Returns:
        None 或 {'type': 'top'|'bottom', 'strength': float}，strength 0~1 表示背离强度
    """
    if history is None or len(history) < window + 2:
        return None

    prices = [h['price'] for h in history]
    volumes = [h['volume'] or 0 for h in history]

    recent = prices[-window:]
    prior_max_price = max(prices[:-window])
    prior_max_vol = max(volumes[:-window])

    cur_max_price = max(recent)
    cur_max_vol = max(volumes[-window:])

    # 顶背离：价格创新高，量能未创新高
    if cur_max_price > prior_max_price and cur_max_vol < prior_max_vol:
        # RSI 过滤：顶背离发生在超买回落阶段更有意义
        if rsi is not None and rsi >= rsi_max:
            strength = 1.0 - min(0.9, cur_max_vol / prior_max_vol) if prior_max_vol > 0 else 0.5
            return {'type': 'top', 'strength': max(0.1, strength)}
        elif rsi is None:
            strength = 1.0 - min(0.9, cur_max_vol / prior_max_vol) if prior_max_vol > 0 else 0.5
            return {'type': 'top', 'strength': max(0.1, strength)}

    # 底背离：价格创新低，量能未创新低
    cur_min_price = min(recent)
    if cur_min_price < min(prices[:-window]):
        # 量能条件：当前段量能不低于前段低谷（量能回升）
        if cur_max_vol > min(volumes[:-window]):
            if rsi is None or rsi <= rsi_min:
                strength = 1.0 - min(0.9, cur_min_price / min(prices[:-window])) if min(prices[:-window]) > 0 else 0.5
                return {'type': 'bottom', 'strength': max(0.1, strength)}

    return None
