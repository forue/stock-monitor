#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QQ 告警推送脚本 - 读取告警队列并推送
可配置为定时任务，每分钟执行一次
"""

import json
import os
import time
import requests
from pathlib import Path
from datetime import datetime

BASE_DIR = Path("/home/node/.openclaw/workspace/stock-monitor")
ALERTS_QUEUE = BASE_DIR / "logs" / "qq-alert-queue.jsonl"
PROCESSED_FILE = BASE_DIR / "logs" / "qq-alert-processed.jsonl"
CONFIG_FILE = BASE_DIR / "config.json"

# QQ C2C Token 缓存
_qq_token_cache = None
_qq_token_expire = 0

def load_config():
    """加载配置"""
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}


def load_env():
    """从 .env 文件加载环境变量"""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, sep, val = line.partition('=')
            key = key.strip()
            if not key or not sep:
                continue
            val = val.strip().strip('"').strip("'")
            if key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass

def qq_get_access_token(app_id: str, client_secret: str) -> str:
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
        }, headers={'Content-Type': 'application/json'}, timeout=5)
        resp.raise_for_status()
        result = resp.json()
        token = result.get('access_token')
        expires_in = int(result.get('expires_in', 7200))
        if token:
            _qq_token_cache = token
            _qq_token_expire = now + expires_in
            print(f"  ✅ AccessToken 获取成功，有效期 {expires_in}s")
            return token
        else:
            print(f"  ❌ AccessToken 获取失败：{result}")
            return None
    except Exception as e:
        print(f"  ❌ AccessToken 请求异常：{e}")
        return None

def send_via_qqbot_c2c(message: str, config: dict) -> bool:
    """
    通过 QQ C2C 私聊发送消息（优先从环境变量读取，config.json 作为 fallback）
    """
    notif = config.get('notification', {})
    app_id = os.environ.get('QQ_APP_ID') or notif.get('qq_app_id', '')
    client_secret = os.environ.get('QQ_CLIENT_SECRET') or notif.get('qq_client_secret', '')
    user_openid = os.environ.get('QQ_USER_OPENID') or notif.get('user_openid', '')

    if not all([app_id, client_secret, user_openid]):
        print("  ⚠️ QQ C2C 推送配置不完整 (qq_app_id/qq_client_secret/user_openid)")
        return False

    token = qq_get_access_token(app_id, client_secret)
    if not token:
        return False

    url = f"https://api.sgroup.qq.com/v2/users/{user_openid}/messages"
    payload = {
        "content": message,
        "msg_type": 0,  # 0 = 文本消息
    }

    try:
        resp = requests.post(url, json=payload, headers={
            'Authorization': f'QQBot {token}',
            'Content-Type': 'application/json',
            'X-Union-Appid': app_id,
        }, timeout=5)
        resp.raise_for_status()
        result = resp.json()
        msg_id = result.get('id', '')
        print(f"  ✅ C2C 消息推送成功！msg_id={msg_id}")
        return True
    except Exception as e:
        print(f"  ❌ C2C 消息推送异常：{e}")
        return False

def send_via_onebot(message: str, config: dict) -> bool:
    """
    通过 OneBot (go-cqhttp) 发送 QQ 群消息
    """
    notification = config.get('notification', {})
    group_id = notification.get('group_id')
    onebot_url = notification.get('onebot_url', 'http://127.0.0.1:5700')

    if not group_id:
        print("  ⚠️  未配置群号，跳过推送")
        return False

    url = f"{onebot_url}/send_group_msg"
    data = json.dumps({
        "group_id": int(group_id),
        "message": message
    }).encode('utf-8')

    try:
        req = requests.post(url, json={"group_id": int(group_id), "message": message}, timeout=5)
        result = req.json()

        if result.get('status') == 'ok':
            print(f"  ✅ 群消息推送成功 (群号：{group_id})")
            return True
        else:
            print(f"  ❌ 推送失败：{result}")
            return False

    except Exception as e:
        print(f"  ❌ 推送异常：{e}")
        return False

def send_alert(alert: dict, config: dict) -> bool:
    """
    根据配置选择推送方式
    """
    channel = config.get('notification', {}).get('channel', 'qqbot')

    if channel == 'qqbot_c2c':
        return send_via_qqbot_c2c(alert['message'], config)
    elif channel == 'qqbot':
        # 旧版频道推送（未实现）
        print(f"  ⚠️ qqbot 频道推送未实现，请改用 qqbot_c2c")
        return False
    elif channel == 'qqbot-group':
        return send_via_onebot(alert['message'], config)
    else:
        print(f"  ⚠️ 未知推送渠道 '{channel}'，跳过")
        return False

def main():
    print("=" * 50)
    print(f"🦐 QQ 告警推送 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    # 加载 .env 环境变量
    load_env()
    
    if not ALERTS_QUEUE.exists():
        print("✅ 告警队列为空，无需处理")
        return
    
    # 读取未处理的告警
    with open(ALERTS_QUEUE, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    if not lines:
        print("✅ 告警队列为空，无需处理")
        return
    
    print(f"📋 待处理告警：{len(lines)} 条")
    
    # 加载配置
    config = load_config()
    
    processed = []
    success_count = 0
    fail_count = 0
    
    for i, line in enumerate(lines, 1):
        try:
            alert = json.loads(line.strip())
            
            if alert.get('status') == 'sent':
                print(f"  [{i}/{len(lines)}] ⏭️  已发送，跳过：{alert.get('stock_name')}")
                continue
            
            stock_info = f"{alert.get('stock_name')} ({alert.get('stock_code')})"
            print(f"  [{i}/{len(lines)}] 📤 推送中：{stock_info} {alert.get('change_pct'):+.2f}%")
            
            # 发送消息
            success = send_alert(alert, config)
            
            # 更新状态
            alert['status'] = 'sent' if success else 'failed'
            alert['sent_time'] = datetime.now().isoformat()
            
            processed.append(json.dumps(alert, ensure_ascii=False))
            
            if success:
                print(f"           ✅ 推送成功")
                success_count += 1
            else:
                print(f"           ❌ 推送失败")
                fail_count += 1
        
        except Exception as e:
            print(f"  [{i}/{len(lines)}] ❌ 处理异常：{e}")
            fail_count += 1
    
    # 写入已处理文件
    if processed:
        with open(PROCESSED_FILE, 'a', encoding='utf-8') as f:
            f.write('\n'.join(processed) + '\n')
        
        # 清空队列
        with open(ALERTS_QUEUE, 'w') as f:
            pass
        
        print()
        print(f"📊 处理完成：成功 {success_count} 条，失败 {fail_count} 条")
    else:
        print()
        print("✅ 无需处理")
    
    print("=" * 50)

if __name__ == "__main__":
    main()
