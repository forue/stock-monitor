#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QQ 推送测试脚本
测试告警消息生成和推送流程
"""

import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path("/home/node/.openclaw/workspace/stock-monitor")
ALERTS_QUEUE = BASE_DIR / "logs" / "qq-alert-queue.jsonl"

def test_alert_generation():
    """测试告警消息生成"""
    print("=" * 50)
    print("测试 1: 告警消息生成")
    print("=" * 50)
    
    test_cases = [
        {'code': '600104', 'name': '上汽集团', 'price': 14.31, 'change': 3.15},
        {'code': '002475', 'name': '立讯精密', 'price': 49.89, 'change': -2.50},
        {'code': '600519', 'name': '贵州茅台', 'price': 1459.88, 'change': 5.00},
    ]
    
    for tc in test_cases:
        direction = "📈" if tc['change'] > 0 else "📉"
        message = f"""{direction} 股票异动告警

📊 {tc['name']} ({tc['code']})
💰 当前价：¥{tc['price']:.2f}
📊 涨跌幅：{tc['change']:+.2f}%
⏰ 时间：{datetime.now().strftime('%H:%M:%S')}

请速速查看！🦐
"""
        print(f"\n  {tc['name']} ({tc['code']}) {tc['change']:+.2f}%:")
        print("  " + "-" * 40)
        for line in message.split('\n'):
            print(f"  {line}")
    
    print()

def test_queue_write():
    """测试告警队列写入"""
    print("=" * 50)
    print("测试 2: 告警队列写入")
    print("=" * 50)
    
    # 创建测试告警
    alert = {
        'timestamp': datetime.now().isoformat(),
        'stock_code': '600104',
        'stock_name': '上汽集团',
        'current_price': 14.31,
        'change_pct': 3.15,
        'volume_ratio': 3.5,
        'alert_type': 'price_spike',
        'verified': True,
        'message': f"""📈 股票异动告警

📊 上汽集团 (600104)
💰 当前价：¥14.31
📊 涨跌幅：+3.15%
⏰ 时间：{datetime.now().strftime('%H:%M:%S')}

请速速查看！🦐
""",
        'channel': 'qqbot',
        'status': 'pending'
    }
    
    try:
        # 确保目录存在
        ALERTS_QUEUE.parent.mkdir(parents=True, exist_ok=True)
        
        # 写入队列
        with open(ALERTS_QUEUE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(alert, ensure_ascii=False) + '\n')
        
        print(f"  ✅ 测试告警已写入：{ALERTS_QUEUE}")
        print(f"  📊 股票：{alert['stock_name']}")
        print(f"  📈 涨跌幅：{alert['change_pct']:+.2f}%")
        print(f"  📝 状态：{alert['status']}")
        
        # 验证写入
        with open(ALERTS_QUEUE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        print(f"  📋 队列中的告警数：{len(lines)} 条")
        
    except Exception as e:
        print(f"  ❌ 写入失败：{e}")
    
    print()

def test_queue_read():
    """测试告警队列读取"""
    print("=" * 50)
    print("测试 3: 告警队列读取")
    print("=" * 50)
    
    if not ALERTS_QUEUE.exists():
        print("  ℹ️  队列为空")
        return
    
    with open(ALERTS_QUEUE, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    if not lines:
        print("  ℹ️  队列为空")
        return
    
    print(f"  📋 队列中有 {len(lines)} 条告警")
    
    for i, line in enumerate(lines, 1):
        try:
            alert = json.loads(line.strip())
            print(f"\n  [{i}] {alert.get('stock_name')} ({alert.get('stock_code')})")
            print(f"      当前价：¥{alert.get('current_price', 0):.2f}")
            print(f"      涨跌幅：{alert.get('change_pct', 0):+.2f}%")
            print(f"      状态：{alert.get('status', 'unknown')}")
            print(f"      时间：{alert.get('timestamp', 'N/A')}")
        except Exception as e:
            print(f"  [{i}] ❌ 解析失败：{e}")
    
    print()

def test_push_script():
    """测试推送脚本"""
    print("=" * 50)
    print("测试 4: 推送脚本执行")
    print("=" * 50)
    
    import subprocess
    
    script_path = BASE_DIR / "scripts" / "send-qq-alert.py"
    
    if not script_path.exists():
        print(f"  ❌ 脚本不存在：{script_path}")
        return
    
    try:
        result = subprocess.run(
            ['python3', str(script_path)],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        
        if result.returncode == 0:
            print("  ✅ 推送脚本执行成功")
        else:
            print(f"  ❌ 推送脚本执行失败 (返回码：{result.returncode})")
    
    except subprocess.TimeoutExpired:
        print("  ❌ 脚本执行超时")
    except Exception as e:
        print(f"  ❌ 执行异常：{e}")
    
    print()

def test_config():
    """测试配置文件"""
    print("=" * 50)
    print("测试 5: 配置文件检查")
    print("=" * 50)
    
    config_file = BASE_DIR / "config.json"
    
    if not config_file.exists():
        print("  ❌ 配置文件不存在")
        return
    
    with open(config_file, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    notification = config.get('notification', {})
    
    print(f"  推送渠道：{notification.get('channel', '未配置')}")
    print(f"  启用状态：{'✅' if notification.get('enabled', False) else '❌'}")
    
    if notification.get('channel') == 'qqbot':
        print(f"  频道 ID: {notification.get('guild_id', '未配置')}")
        print(f"  子频道 ID: {notification.get('channel_id', '未配置')}")
    elif notification.get('channel') == 'qqbot-group':
        print(f"  群号：{notification.get('group_id', '未配置')}")
        print(f"  OneBot 地址：{notification.get('onebot_url', '未配置')}")
    
    print()

def main():
    print()
    print("🦐 QQ 推送测试")
    print(f"   时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    test_config()
    test_alert_generation()
    test_queue_write()
    test_queue_read()
    test_push_script()
    
    print("=" * 50)
    print("✅ 测试完成!")
    print("=" * 50)
    print()
    print("📝 下一步:")
    print("   1. 配置 QQ 频道/群号 (编辑 config.json)")
    print("   2. 运行 send-qq-alert.py 推送测试告警")
    print("   3. 检查 QQ 是否收到消息")
    print()

if __name__ == "__main__":
    main()
