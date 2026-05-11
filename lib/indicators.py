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
