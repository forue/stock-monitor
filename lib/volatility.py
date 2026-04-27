#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
波动率计算模块 - 价格波动率/涨跌幅/振幅/量比
"""

from pathlib import Path

from lib.logger import log
from lib.trading_calendar import get_trading_elapsed_ratio


def calculate_volatility(stock_data: dict, historical_data: list = None,
                         db_path: Path = None) -> dict:
    """
    计算股票波动指标

    当关键字段缺失时：
    - high_price/low_price 缺失 → 振幅不参与判断 (amplitude=None)
    - volume 缺失 → 量比不参与判断 (volume_ratio=None)
    - current_price/close_price 缺失 → 返回 None
    """
    current_price = stock_data['current_price']
    close_price = stock_data['close_price']
    high_price = stock_data.get('high_price')
    low_price = stock_data.get('low_price')
    current_volume = stock_data.get('volume')
    stock_code = stock_data.get('code', 'UNKNOWN')

    # 数据校验
    if close_price is None or current_price is None or close_price <= 0 or current_price <= 0:
        log(f"数据异常：{stock_code} - 收盘价={close_price}, 现价={current_price}", level="WARNING")
        return None

    if close_price < 0.1:
        log(f"数据异常：{stock_code} 收盘价 {close_price} < 0.1 元", level="WARNING")
        return None

    # 振幅计算
    amplitude = None
    if high_price is not None and low_price is not None:
        if high_price < low_price:
            log(f"数据异常：{stock_code} 最高价({high_price}) < 最低价({low_price})", level="WARNING")
            return None
        raw_amplitude = (high_price - low_price) / close_price
        if raw_amplitude > 1.0:
            log(f"数据异常：{stock_code} 振幅={raw_amplitude*100:.2f}% 超过100%", level="WARNING")
            return None
        amplitude = raw_amplitude
    else:
        log(f"{stock_code} 高低价数据缺失，振幅不参与判断", level="DEBUG")

    # 价格波动率
    price_change_rate = abs(current_price - close_price) / close_price

    # 涨跌幅 (%)
    price_change_pct = (current_price - close_price) / close_price * 100

    # 量比 = 当前累计量 / (历史日均全天量 × 已过交易时间占比)
    volume_ratio = None
    if current_volume is not None and current_volume > 0:
        avg_volume = 0
        if historical_data and len(historical_data) > 0:
            avg_vol = sum(d.get('volume', 0) for d in historical_data[-5:]) / min(5, len(historical_data))
            if avg_vol > 100000:
                avg_volume = avg_vol

        if avg_volume <= 0 and db_path:
            from lib.database import get_avg_volume_from_db
            avg_volume = get_avg_volume_from_db(db_path, stock_code)

        if avg_volume > 0:
            elapsed_ratio = get_trading_elapsed_ratio()
            volume_ratio = current_volume / (avg_volume * elapsed_ratio)
        else:
            log(f"{stock_code} 无有效历史均量，量比不参与判断", level="DEBUG")
    else:
        log(f"{stock_code} 成交量数据缺失或为0，量比不参与判断", level="DEBUG")

    return {
        'price_change_rate': price_change_rate,
        'price_change_pct': price_change_pct,
        'amplitude': amplitude,
        'volume_ratio': volume_ratio,
    }
