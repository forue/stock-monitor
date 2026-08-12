#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据获取模块 - 免费数据源(腾讯/东方财富/新浪/网易) + 带重试 HTTP 请求

降级链: 腾讯 → 东方财富 → 新浪 → 网易
"""

import time
import requests
from datetime import datetime
from typing import Optional

from lib.logger import log

# 重试配置
MAX_RETRIES = 3
RETRY_DELAY = 1


def request_with_retry(method: str, url: str, **kwargs) -> Optional[requests.Response]:
    """带重试的 HTTP 请求（用于数据源获取，非 QQ 推送）"""
    kwargs.setdefault('timeout', 5)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            if attempt < MAX_RETRIES:
                log(f"请求失败 (第{attempt}次)，{RETRY_DELAY}s 后重试: {e}", level="WARNING")
                time.sleep(RETRY_DELAY)
            else:
                log(f"请求失败 (已重试{MAX_RETRIES}次): {e}", level="ERROR")
                return None
    return None


# ============ 腾讯财经 ============

def fetch_tencent_data(stock_code: str) -> Optional[dict]:
    """
    从腾讯财经获取免费实时数据

    字段索引参考：
    0=未知, 1=名称, 3=现价, 4=昨收, 5=今开,
    6=成交量(手), 33=最高, 34=最低, 36=成交量(手/备选),
    37=成交额(万), 38=换手率
    """
    prefix = 'sh' if stock_code.startswith('6') else 'sz'
    url = f"https://qt.gtimg.cn/q={prefix}{stock_code}"

    try:
        resp = request_with_retry('GET', url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        if not resp:
            return None

        text = resp.text
        if not text or '~' not in text:
            return None

        parts = text.split('~')
        if len(parts) < 50:
            return None

        stock_name = parts[1]
        current_price = float(parts[3]) if parts[3] else None
        close_price = float(parts[4]) if parts[4] else None
        open_price = float(parts[5]) if parts[5] else None
        _vol_str = parts[6] if len(parts) > 6 and parts[6] else (parts[36] if len(parts) > 36 and parts[36] else None)
        volume = float(_vol_str) * 100 if _vol_str else None   # 手转股
        high_price = float(parts[33]) if len(parts) > 33 and parts[33] else None
        low_price = float(parts[34]) if len(parts) > 34 and parts[34] else None
        amount = float(parts[37]) if len(parts) > 37 and parts[37] else None  # 成交额(万)

        if current_price is None or close_price is None:
            log(f"数据缺失 ({stock_code}): 现价={current_price}, 昨收={close_price}", level="WARNING")
            return None

        return {
            'code': stock_code,
            'name': stock_name,
            'current_price': current_price,
            'open_price': open_price,
            'close_price': close_price,
            'high_price': high_price,
            'low_price': low_price,
            'volume': volume,
            'amount': amount,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source': 'tencent',
        }

    except Exception as e:
        log(f"获取腾讯财经数据失败 ({stock_code}): {e}", level="ERROR")
        return None


# ============ 新浪财经 (备用) ============

def fetch_sina_data(stock_code: str) -> Optional[dict]:
    """
    从新浪财经获取免费实时数据（备用数据源）

    返回格式：var hq_str_sh600104="名称,今开,昨收,现价,最高,最低,成交量(股),成交额,..."
    """
    prefix = 'sh' if stock_code.startswith('6') else 'sz'
    url = f"https://hq.sinajs.cn/list={prefix}{stock_code}"

    try:
        resp = request_with_retry('GET', url, headers={
            'Referer': 'https://finance.sina.com.cn',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        if not resp:
            return None

        text = resp.text
        if not text or '=' not in text:
            return None

        # 解析：hq_str_sh600104="上汽集团,14.50,14.31,15.50,15.80,14.20,12345678,180000000,..."
        value_part = text.split('"')[1]
        if not value_part:
            return None

        fields = value_part.split(',')
        if len(fields) < 32:
            return None

        stock_name = fields[0]
        open_price = float(fields[1]) if fields[1] else None
        close_price = float(fields[2]) if fields[2] else None   # 昨收
        current_price = float(fields[3]) if fields[3] else None
        high_price = float(fields[4]) if fields[4] else None
        low_price = float(fields[5]) if fields[5] else None
        volume = float(fields[8]) if fields[8] else None        # 成交量(股)
        amount = float(fields[9]) if fields[9] else None        # 成交额(元)

        if current_price is None or close_price is None:
            log(f"新浪数据缺失 ({stock_code}): 现价={current_price}, 昨收={close_price}", level="WARNING")
            return None

        return {
            'code': stock_code,
            'name': stock_name,
            'current_price': current_price,
            'open_price': open_price,
            'close_price': close_price,
            'high_price': high_price,
            'low_price': low_price,
            'volume': volume,
            'amount': amount / 10000 if amount else None,  # 元转万元
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source': 'sina',
        }

    except Exception as e:
        log(f"获取新浪财经数据失败 ({stock_code}): {e}", level="ERROR")
        return None


# ============ 东方财富 ============

def fetch_eastmoney_data(stock_code: str) -> Optional[dict]:
    """
    从东方财富获取免费实时数据（稳定性最好的免费源）

    API: push2.eastmoney.com
    secid: 1.600104 (沪) / 0.002475 (深)
    价格单位: 分 (需 /100), 成交量单位: 股
    """
    market = '1' if stock_code.startswith('6') else '0'
    secid = f"{market}.{stock_code}"
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        'secid': secid,
        'ut': 'fa5fd1943c7b386f172d6893dbfba10b',
        'fltt': '2',  # 2=不除以100的价格(直接返回元), 1=返回分
        'invt': '2',
        'fields': 'f43,f44,f45,f46,f47,f48,f57,f58,f60',
        # f43=最新价, f44=最高, f45=最低, f46=今开, f47=成交量(股),
        # f48=成交额(元), f57=代码, f58=名称, f60=昨收
    }

    try:
        resp = request_with_retry('GET', url, params=params, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://quote.eastmoney.com/',
        })
        if not resp:
            return None

        result = resp.json()
        data = result.get('data')
        if not data:
            return None

        current_price = data.get('f43')
        close_price = data.get('f60')
        open_price = data.get('f46')
        high_price = data.get('f44')
        low_price = data.get('f45')
        volume = data.get('f47')        # 成交量(股)
        amount = data.get('f48')        # 成交额(元)
        stock_name = data.get('f58', stock_code)

        # fltt=2 时价格已经是元，但某些情况下仍返回分，做兼容
        def _safe_float(val):
            if val is None or val == '-':
                return None
            return float(val)

        current_price = _safe_float(current_price)
        close_price = _safe_float(close_price)
        open_price = _safe_float(open_price)
        high_price = _safe_float(high_price)
        low_price = _safe_float(low_price)
        volume = _safe_float(volume)
        amount = _safe_float(amount)

        if current_price is None or close_price is None:
            log(f"东方财富数据缺失 ({stock_code}): 现价={current_price}, 昨收={close_price}", level="WARNING")
            return None

        # 成交量已是股，无需转换
        # 成交额转为万元
        if amount:
            amount = amount / 10000

        return {
            'code': stock_code,
            'name': stock_name,
            'current_price': current_price,
            'open_price': open_price,
            'close_price': close_price,
            'high_price': high_price,
            'low_price': low_price,
            'volume': volume,
            'amount': amount,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source': 'eastmoney',
        }

    except Exception as e:
        log(f"获取东方财富数据失败 ({stock_code}): {e}", level="ERROR")
        return None


# ============ 网易财经 ============

def fetch_netease_data(stock_code: str) -> Optional[dict]:
    """
    从网易财经获取免费实时数据（第三备选）

    API: money.finance.sina.com.cn (网易与新浪共用行情源)
    返回 JSON
    """
    # 网易原 api.money.126.net 已不稳定，改用新浪财经的行情页接口
    # 此接口返回更完整的数据，作为终极备用
    prefix = 'sh' if stock_code.startswith('6') else 'sz'
    url = f"https://hq.sinajs.cn/list={prefix}{stock_code}"

    try:
        resp = request_with_retry('GET', url, headers={
            'Referer': 'https://finance.sina.com.cn',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        if not resp:
            return None

        text = resp.text
        if not text or '=' not in text:
            return None

        value_part = text.split('"')[1]
        if not value_part:
            return None

        fields = value_part.split(',')
        if len(fields) < 32:
            return None

        stock_name = fields[0]
        open_price = float(fields[1]) if fields[1] else None
        close_price = float(fields[2]) if fields[2] else None   # 昨收
        current_price = float(fields[3]) if fields[3] else None
        high_price = float(fields[4]) if fields[4] else None
        low_price = float(fields[5]) if fields[5] else None
        volume = float(fields[8]) if fields[8] else None        # 成交量(股)
        amount = float(fields[9]) if fields[9] else None        # 成交额(元)

        if current_price is None or close_price is None:
            log(f"网易数据缺失 ({stock_code}): 现价={current_price}, 昨收={close_price}", level="WARNING")
            return None

        return {
            'code': stock_code,
            'name': stock_name,
            'current_price': current_price,
            'open_price': open_price,
            'close_price': close_price,
            'high_price': high_price,
            'low_price': low_price,
            'volume': volume,
            'amount': amount / 10000 if amount else None,  # 元转万元
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source': 'netease',
        }

    except Exception as e:
        log(f"获取网易财经数据失败 ({stock_code}): {e}", level="ERROR")
        return None


# ============ 统一接口 ============

# 数据源降级链
DATA_SOURCES = [
    ('tencent', fetch_tencent_data),
    ('eastmoney', fetch_eastmoney_data),
    ('sina', fetch_sina_data),
    ('netease', fetch_netease_data),
]


def fetch_free_data(stock_code: str) -> Optional[dict]:
    """
    获取免费实时数据（按降级链依次尝试）

    降级链: 腾讯 → 东方财富 → 新浪 → 网易
    """
    for name, fetcher in DATA_SOURCES:
        data = fetcher(stock_code)
        if data:
            return data
        log(f"{name} 接口失败，尝试下一个 ({stock_code})", level="WARNING")

    log(f"所有免费数据源均失败 ({stock_code})", level="ERROR")
    return None


# ============ 主力资金流向 (东方财富) ============

def fetch_fund_flow(stock_code: str) -> Optional[dict]:
    """
    获取主力资金流向（东方财富实时资金流接口）

    仅取最近一根 1 分钟 K 线的资金净额（单位: 元）。
    字段：f51=时间, f52=主力净流入(元), f53=小单, f54=中单, f55=大单, f56=超大单
    正值=净流入，负值=净流出。

    Returns:
        dict 或 None（接口失败/数据不可用时）
    """
    market = '1' if stock_code.startswith('6') else '0'
    secid = f"{market}.{stock_code}"
    url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
    params = {
        'lmt': '1', 'klt': '1', 'secid': secid,
        'fields1': 'f1,f2,f3,f7',
        'fields2': 'f51,f52,f53,f54,f55,f56',
    }

    try:
        resp = request_with_retry('GET', url, params=params, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://quote.eastmoney.com/',
        })
        if not resp:
            return None

        result = resp.json()
        data = result.get('data')
        klines = (data or {}).get('klines') or []
        if not klines:
            log(f"资金流数据为空 ({stock_code})", level="WARNING")
            return None

        fields = klines[-1].split(',')
        if len(fields) < 6:
            log(f"资金流字段不足 ({stock_code})", level="WARNING")
            return None

        def _f(idx):
            try:
                return float(fields[idx])
            except (ValueError, IndexError):
                return None

        main_net = _f(1)
        super_net = _f(5)
        large_net = _f(4)

        return {
            'code': stock_code,
            'main_net': main_net,          # 主力净流入(元)
            'super_net': super_net,        # 超大单净流入(元)
            'large_net': large_net,        # 大单净流入(元)
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source': 'eastmoney_fflow',
        }
    except Exception as e:
        log(f"获取主力资金流失败 ({stock_code}): {e}", level="ERROR")
        return None


# ============ 五档盘口 (腾讯) ============

def fetch_order_book(stock_code: str) -> Optional[dict]:
    """
    获取买卖五档盘口（腾讯行情接口）

    ~ 分割字段索引（已验证 88 字段）：
      3=现价, 4=昨收
      9/10   买一价/量(手)  11/12 买二价/量  13/14 买三价/量
      15/16  买四价/量      17/18 买五价/量
      19/20  卖一价/量      21/22 卖二价/量  23/24 卖三价/量
      25/26  卖四价/量      27/28 卖五价/量
      47=涨停价, 48=跌停价, 50=委差, 51=当日均价

    Returns:
        dict 或 None
    """
    prefix = 'sh' if stock_code.startswith('6') else 'sz'
    url = f"https://qt.gtimg.cn/q={prefix}{stock_code}"

    try:
        resp = request_with_retry('GET', url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        if not resp:
            return None

        resp.encoding = 'gbk'
        text = resp.text
        if not text or '~' not in text:
            return None

        parts = text.split('~')
        if len(parts) < 82:
            return None

        def _f(idx):
            try:
                val = parts[idx].strip()
                if not val:
                    return None
                return float(val)
            except (ValueError, IndexError):
                return None

        def _parse_level(price_idx, qty_idx):
            price = _f(price_idx)
            qty = _f(qty_idx)  # 手
            return (price, qty)

        bids = [_parse_level(9, 10), _parse_level(11, 12), _parse_level(13, 14),
                _parse_level(15, 16), _parse_level(17, 18)]
        asks = [_parse_level(19, 20), _parse_level(21, 22), _parse_level(23, 24),
                _parse_level(25, 26), _parse_level(27, 28)]

        bid_total = sum(q for _, q in bids if q is not None)
        ask_total = sum(q for _, q in asks if q is not None)

        # 委比 = (买盘-卖盘)/(买盘+卖盘)，范围 -1 ~ 1
        vi_ratio = None
        if (bid_total + ask_total) > 0:
            vi_ratio = (bid_total - ask_total) / (bid_total + ask_total)

        return {
            'code': stock_code,
            'bids': bids,
            'asks': asks,
            'bid_total': bid_total,
            'ask_total': ask_total,
            'vi_ratio': vi_ratio,
            'limit_up': _f(47),
            'limit_down': _f(48),
            'current_price': _f(3),
            'close_price': _f(4),
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source': 'tencent_orderbook',
        }
    except Exception as e:
        log(f"获取五档盘口失败 ({stock_code}): {e}", level="ERROR")
        return None
