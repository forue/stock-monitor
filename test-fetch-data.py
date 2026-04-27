#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试数据源获取 - 验证全部数据源能否拉到实时数据
测试完成后可删除此文件
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from lib.logger import init_logger, log
from lib.data_fetcher import (
    fetch_tencent_data, fetch_eastmoney_data, fetch_sina_data, fetch_netease_data,
    fetch_free_data,
)
from lib.volatility import calculate_volatility

init_logger(BASE_DIR / "logs" / "test-fetch.log")

test_code = "002475"

# 逐个测试每个数据源
sources = [
    ("腾讯", fetch_tencent_data),
    ("东方财富", fetch_eastmoney_data),
    ("新浪", fetch_sina_data),
    ("网易", fetch_netease_data),
]

for name, fetcher in sources:
    log(f"--- {name} ({test_code}) ---")
    data = fetcher(test_code)
    if data:
        log(f"  现价: {data.get('current_price')}, 昨收: {data.get('close_price')}")
        log(f"  开盘: {data.get('open_price')}, 最高: {data.get('high_price')}, 最低: {data.get('low_price')}")
        log(f"  成交量: {data.get('volume')}, 成交额(万): {data.get('amount')}")
        log(f"  ✅ {name} 数据源正常")
    else:
        log(f"  ❌ {name} 数据源失败")

# 测试统一接口
log(f"\n--- 统一接口 fetch_free_data ({test_code}) ---")
data = fetch_free_data(test_code)
if data:
    log(f"  来源: {data.get('source')}, 现价: {data.get('current_price')}")
