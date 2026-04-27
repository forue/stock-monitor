#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票监控系统 - 流程测试脚本
测试各个模块功能是否正常
"""

import sys
import urllib.request
import json
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path("/home/node/.openclaw/workspace/stock-monitor")

def test_data_fetch():
    """测试数据获取"""
    print("=" * 50)
    print("测试 1: 数据获取 (东方财富)")
    print("=" * 50)
    
    test_stocks = [
        ('600104', '上汽集团'),
        ('002475', '立讯精密'),
        ('600519', '贵州茅台'),
    ]
    
    for code, name in test_stocks:
        market = '1' if code.startswith('6') else '0'
        url = f"http://push2.eastmoney.com/api/qt/stock/get?secid={market}.{code}&fields=f43,f44,f45,f46,f47,f48,f49,f14"
        
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=3) as response:
                content = response.read().decode('utf-8').strip()
            
            data = json.loads(content)
            
            if data.get('rc') == 0 and data.get('data'):
                d = data['data']
                price = float(d['f43']) / 100
                close = float(d['f46']) / 100
                change = (price - close) / close * 100 if close > 0 else 0
                
                print(f"  ✅ {name} ({code}): ¥{price:.2f}  涨跌:{change:+.2f}%")
            else:
                print(f"  ❌ {name} ({code}): API 返回错误")
        
        except Exception as e:
            print(f"  ❌ {name} ({code}): {e}")
    
    print()

def test_volatility_calc():
    """测试波动率计算"""
    print("=" * 50)
    print("测试 2: 波动率计算")
    print("=" * 50)
    
    stock_data = {
        'current_price': 15.50,
        'close_price': 15.00,
        'high_price': 15.80,
        'low_price': 14.90,
        'volume': 5000000,
    }
    
    # 价格波动率
    price_change_rate = abs(stock_data['current_price'] - stock_data['close_price']) / stock_data['close_price']
    
    # 涨跌幅
    price_change_pct = (stock_data['current_price'] - stock_data['close_price']) / stock_data['close_price'] * 100
    
    # 振幅
    amplitude = (stock_data['high_price'] - stock_data['low_price']) / stock_data['close_price']
    
    print(f"  当前价：{stock_data['current_price']}")
    print(f"  昨收价：{stock_data['close_price']}")
    print(f"  价格波动率：{price_change_rate:.2%}")
    print(f"  涨跌幅：{price_change_pct:+.2f}%")
    print(f"  振幅：{amplitude:.2%}")
    
    # 判断是否触发 L1
    L1_THRESHOLDS = {'price_change_rate': 0.007, 'price_change_pct': 2.5}
    
    if price_change_rate >= L1_THRESHOLDS['price_change_rate']:
        print(f"  ⚠️  触发 L1 阈值 (波动率>{L1_THRESHOLDS['price_change_rate']:.1%})")
    else:
        print(f"  ✓ 未触发 L1 阈值")
    
    if abs(price_change_pct) >= L1_THRESHOLDS['price_change_pct']:
        print(f"  ⚠️  触发 L1 阈值 (涨跌幅>{L1_THRESHOLDS['price_change_pct']}%)")
    
    print()

def test_database():
    """测试数据库"""
    print("=" * 50)
    print("测试 3: 数据库")
    print("=" * 50)
    
    db_path = BASE_DIR / "data" / "stock_monitor.db"
    
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        # 查询表
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cursor.fetchall()]
        print(f"  表：{tables}")
        
        # 查询数据量
        cursor.execute('SELECT COUNT(*) FROM stock_data')
        stock_count = cursor.fetchone()[0]
        print(f"  股票数据记录：{stock_count} 条")
        
        cursor.execute('SELECT COUNT(*) FROM alerts')
        alert_count = cursor.fetchone()[0]
        print(f"  告警记录：{alert_count} 条")
        
        conn.close()
        print("  ✅ 数据库正常")
    
    except Exception as e:
        print(f"  ❌ 数据库错误：{e}")
    
    print()

def test_process_status():
    """测试进程状态"""
    print("=" * 50)
    print("测试 4: 进程状态")
    print("=" * 50)
    
    import os
    import signal
    
    pidfile = "/tmp/stock-monitor.pid"
    
    if os.path.exists(pidfile):
        with open(pidfile, 'r') as f:
            pid = int(f.read().strip())
        
        try:
            os.kill(pid, 0)  # 检查进程是否存在
            print(f"  ✅ 进程运行中 (PID: {pid})")
        except ProcessLookupError:
            print(f"  ❌ 进程已停止 (PID: {pid})")
    else:
        print(f"  ❌ PID 文件不存在")
    
    print()

def test_alert_file():
    """测试告警文件"""
    print("=" * 50)
    print("测试 5: 告警文件")
    print("=" * 50)
    
    alerts_file = BASE_DIR / "logs" / "alerts.json"
    
    if alerts_file.exists():
        with open(alerts_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        print(f"  告警记录数：{len(lines)}")
        
        if lines:
            last_alert = json.loads(lines[-1])
            print(f"  最新告警：{last_alert.get('stock_name')} {last_alert.get('change_pct'):+.2f}%")
            print("  ✅ 告警文件正常")
    else:
        print("  ℹ️  告警文件不存在 (尚无告警)")
    
    print()

def test_config():
    """测试配置文件"""
    print("=" * 50)
    print("测试 6: 配置文件")
    print("=" * 50)
    
    config_file = BASE_DIR / "config.json"
    
    if config_file.exists():
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        stocks = config.get('stocks', [])
        print(f"  监控股票数：{len(stocks)}")
        
        for stock in stocks:
            status = "✓" if stock.get('enabled', True) else "✗"
            print(f"    {status} {stock['name']} ({stock['code']})")
        
        print("  ✅ 配置文件正常")
    else:
        print("  ❌ 配置文件不存在")
    
    print()

def main():
    print()
    print("🦐 股票监控系统 - 流程测试")
    print(f"   时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    test_config()
    test_data_fetch()
    test_volatility_calc()
    test_database()
    test_process_status()
    test_alert_file()
    
    print("=" * 50)
    print("✅ 测试完成!")
    print("=" * 50)
    print()

if __name__ == "__main__":
    main()
