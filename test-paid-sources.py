#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试付费/高级数据源可用性（纯 requests，无需安装第三方库）
1. Tushare HTTP API (需要 token)
2. AKShare HTTP API (通过东方财富接口模拟，免费)

测试完成后可删除此文件
"""

import sys
import os
import json
import requests
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from lib.logger import init_logger, log

init_logger(BASE_DIR / "logs" / "test-paid.log")


# ============ 1. Tushare (HTTP API) ============
log("--- 测试 Tushare ---")

token = os.environ.get('TUSHARE_TOKEN', '')
if not token:
    try:
        with open(BASE_DIR / 'config.json', 'r') as f:
            cfg = json.load(f)
        api_cfg = cfg.get('api', {}).get('paid_sources', [])
        for src in api_cfg:
            if src.get('name') == 'tushare':
                env_key = src.get('api_key_env', 'TUSHARE_TOKEN')
                token = os.environ.get(env_key, '')
                break
    except Exception:
        pass

if token:
    try:
        url = "https://api.tushare.pro"
        payload = {
            "api_name": "daily",
            "token": token,
            "params": {
                "ts_code": "002475.SZ",
                "limit": 3,
            }
        }
        resp = requests.post(url, json=payload, timeout=10)
        result = resp.json()

        if result.get('code') == 0:
            fields = result['data']['fields']
            rows = result['data']['items']
            log(f"  ✅ Tushare 可用！最近3日数据:")
            for row in rows:
                d = dict(zip(fields, row))
                log(f"     {d.get('trade_date')} 收盘:{d.get('close')} 涨幅:{d.get('pct_chg')}% 成交量:{d.get('vol')}手")
        else:
            log(f"  ❌ Tushare 返回错误: {result.get('msg', result)}")
    except Exception as e:
        log(f"  ❌ Tushare 异常: {e}")
else:
    log(f"  ⚠️ 未配置 TUSHARE_TOKEN，跳过")
    log(f"     配置方式: export TUSHARE_TOKEN=你的token")
    log(f"     获取地址: https://tushare.pro/register")


# ============ 2. AKShare (通过东方财富接口模拟，免费无需 token) ============
log("\n--- 测试 AKShare (东方财富全市场行情) ---")
try:
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        'pn': '1',       # 页码
        'pz': '5',       # 每页条数
        'po': '1',       # 排序
        'np': '1',
        'fltt': '2',
        'invt': '2',
        'fid': 'f3',     # 按涨跌幅排序
        'fs': 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23',  # A股
        'fields': 'f2,f3,f4,f5,f6,f7,f8,f12,f14,f15,f16,f17,f18',
        # f2=最新价, f3=涨跌幅, f4=涨跌额, f5=成交量(手), f6=成交额
        # f7=振幅, f8=换手率, f12=代码, f14=名称, f15=最高, f16=最低, f17=今开, f18=昨收
    }
    resp = requests.get(url, params=params, headers={
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://quote.eastmoney.com/',
    }, timeout=10)

    if resp.status_code == 200:
        result = resp.json()
        data = result.get('data', {})
        total = data.get('total', 0)
        items = data.get('diff', [])
        if items:
            log(f"  ✅ AKShare (东方财富) 可用！共 {total} 只股票，涨幅前5:")
            for item in items:
                code = item.get('f12', '')
                name = item.get('f14', '')
                price = item.get('f2', '')
                pct = item.get('f3', '')
                log(f"     {name}({code}) 现价:{price} 涨跌幅:{pct}%")
        else:
            log(f"  ❌ 返回空数据")
    else:
        log(f"  ❌ HTTP {resp.status_code}")

except Exception as e:
    log(f"  ❌ AKShare 异常: {e}")


# ============ 3. 新浪全市场行情 (免费无需 token) ============
log("\n--- 测试新浪全市场行情 ---")
try:
    # 新浪全市场接口，支持批量查询
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    params = {
        'page': '1',
        'num': '5',
        'sort': 'changepercent',
        'asc': '0',
        'node': 'hs_a',
        'symbol': '',
        '_s_r_a': 'page',
    }
    resp = requests.get(url, params=params, headers={
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://finance.sina.com.cn',
    }, timeout=10)

    if resp.status_code == 200 and resp.text.strip():
        items = json.loads(resp.text)
        if items:
            log(f"  ✅ 新浪全市场行情可用！涨幅前5:")
            for item in items[:5]:
                log(f"     {item.get('name')}({item.get('code')}) 现价:{item.get('trade')} 涨跌幅:{item.get('changepercent')}%")
        else:
            log(f"  ❌ 返回空数据")
    else:
        log(f"  ❌ HTTP {resp.status_code}")

except Exception as e:
    log(f"  ❌ 新浪全市场异常: {e}")


log("\n测试完成")
log("提示: 如需使用 Tushare，请设置 TUSHARE_TOKEN 环境变量")
