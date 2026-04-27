#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模拟告警推送测试 - 使用模拟数据走完整 send_alert 流程
敏感信息从 .env 文件读取
测试完成后可删除此文件
"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

from lib.logger import init_logger, log
from lib.config import load_env_file
from lib.notifier import send_alert

# 加载 .env
load_env_file(BASE_DIR / ".env")

# 初始化日志
init_logger(BASE_DIR / "logs" / "test-push.log")

# 模拟数据
stock = {"code": "600104", "name": "上汽集团", "enabled": True}

stock_data = {
    "current_price": 15.50,
    "open_price": 14.90,
    "yesterday_close": 15.02,
    "high_price": 15.68,
    "low_price": 14.85,
    "volume": 125000000,
    "amount": 192500,
}

metrics = {
    "price_change_rate": 0.038,
    "price_change_pct": 3.20,
    "volume_ratio": 3.85,
    "amplitude": 0.055,
}

triggered_l1 = [
    "价格波动率=3.80%(阈值0.7%)",
    "涨跌幅=+3.20%(阈值±2.5%)",
    "量比=3.85(阈值2.5)",
    "盘中振幅=5.50%(阈值2.0%)",
]

triggered_l2 = [
    "涨跌幅=+3.20%(阈值±3.0%)",
    "量比=3.85(阈值3.0)",
]

config = {
    "notification": {
        "enabled": True,
        "channel": "qqbot_c2c",
        "min_alert_interval": 0,
    },
}

log("=" * 50)
log("🧪 模拟告警推送测试")
log(f"   股票：{stock['name']} ({stock['code']})")
log(f"   涨跌幅：{metrics['price_change_pct']:+.2f}%")
log(f"   量比：{metrics['volume_ratio']:.2f}")
log("=" * 50)

success = send_alert(
    stock=stock,
    stock_data=stock_data,
    metrics=metrics,
    config=config,
    triggered_l1=triggered_l1,
    triggered_l2=triggered_l2,
    base_dir=BASE_DIR,
    alerts_file=BASE_DIR / "logs" / "alerts.json",
)

log(f"\n{'✅ 推送成功！' if success else '❌ 推送失败'}")
