#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试 QQ 异动推送（独立版本）- 从 .env 文件读取凭证"""

import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime

# 从 .env 文件加载环境变量
_ENV_FILE = __import__('pathlib').Path(__file__).parent / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding='utf-8').splitlines():
        _line = _line.strip()
        if not _line or _line.startswith('#'):
            continue
        _k, _s, _v = _line.partition('=')
        _k = _k.strip()
        if _k and _s and _k not in os.environ:
            os.environ[_k] = _v.strip().strip('"').strip("'")

APP_ID = os.environ.get('QQ_APP_ID', '')
CLIENT_SECRET = os.environ.get('QQ_CLIENT_SECRET', '')
USER_OPENID = os.environ.get('QQ_USER_OPENID', '')

token_cache = None
token_expire = 0

def get_access_token():
    global token_cache, token_expire
    now = time.time()
    if token_cache and now < token_expire - 60:
        return token_cache
    
    url = "https://bots.qq.com/app/getAppAccessToken"
    data = json.dumps({
        "appId": APP_ID,
        "clientSecret": CLIENT_SECRET,
    }).encode('utf-8')
    
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as response:
        result = json.loads(response.read().decode('utf-8'))
    
    token = result.get('access_token')
    if token:
        token_cache = token
        token_expire = now + int(result.get('expires_in', 7200))
        print(f"✅ AccessToken 获取成功")
        return token
    else:
        print(f"❌ AccessToken 获取失败: {result}")
        return None

def send_message(content):
    token = get_access_token()
    if not token:
        return False
    
    url = f"https://api.sgroup.qq.com/v2/users/{USER_OPENID}/messages"
    payload = {
        "content": content,
        "msg_type": 0,  # 文本消息
    }
    data = json.dumps(payload).encode('utf-8')
    
    req = urllib.request.Request(url, data=data, headers={
        'Authorization': f'QQBot {token}',
        'Content-Type': 'application/json',
        'X-Union-Appid': APP_ID,
    })
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode('utf-8'))
        msg_id = result.get('id', '')
        print(f"✅ 消息发送成功! msg_id={msg_id}")
        return True
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        print(f"❌ HTTP {e.code}: {body}")
        return False
    except Exception as e:
        print(f"❌ 异常: {e}")
        return False

# 测试消息
test_msg = f"""🦐 测试消息

📊 上汽集团 (600104)
💰 当前价：¥15.50
📊 涨跌幅：+3.15%
📈 量比：3.20
⏰ 时间：{datetime.now().strftime('%H:%M:%S')}

这是一条测试消息，请确认是否收到！✅"""

print("="*50)
print(f"🧪 QQ 推送测试")
print(f"   时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"   目标用户：{USER_OPENID}")
print("="*50)

success = send_message(test_msg)
print(f"\n{'✅ 推送成功！' if success else '❌ 推送失败'}")
