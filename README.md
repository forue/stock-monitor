# 股票智能监控系统

A 股异动实时监控 + QQ 推送，常驻进程 + Cron 保活，零运维。

## 📋 文件结构

```
/home/node/.openclaw/workspace/stock-monitor/
├── monitor-daemon.py          # 常驻监控进程 (主程序)
├── config.json                # 配置文件 (v4.1)
├── config.yaml                # 旧版配置 (已弃用，以 config.json 为准)
├── requirements.txt           # Python 依赖 (仅 requests)
├── lib/                       # 核心模块
│   ├── config.py              #   配置加载 / 热加载
│   ├── logger.py              #   日志系统 (RotatingFileHandler)
│   ├── trading_calendar.py    #   A 股交易日历 / 时段判断 / 时间占比
│   ├── data_fetcher.py        #   多源数据获取 (腾讯→东方财富→新浪→网易)
│   ├── volatility.py          #   波动率 / 涨跌幅 / 振幅 / 量比计算
│   ├── alerter.py             #   L1/L2 量价组合确认 + 9种场景分类
│   ├── notifier.py            #   阶梯式递增告警 + QQ C2C 推送
│   ├── database.py            #   SQLite 操作 / 数据清理
│   └── process.py             #   PID 管理 / 信号处理 / 可中断 sleep
├── scripts/
│   ├── keepalive.sh           #   Cron 保活脚本
│   ├── send-qq-alert.py       #   QQ 告警队列推送脚本
│   ├── test-flow.py           #   全流程测试
│   ├── test-qq-push.py        #   QQ 推送测试
│   └── trading_calendar.py    #   交易日历独立工具
├── test-alert-push.py         # 模拟告警推送测试 (临时，可删)
├── test-fetch-data.py         # 数据源获取测试 (临时，可删)
├── test-paid-sources.py       # 付费数据源测试 (临时，可删)
├── test-qq-push.py            # QQ 推送独立测试 (临时，可删)
├── logs/
│   ├── monitor.log            #   运行日志 (10MB 轮转，5 份)
│   ├── keepalive.log          #   保活日志
│   ├── alerts.json            #   告警记录
│   ├── qq-alert-queue.jsonl   #   QQ 告警队列
│   └── qq-alert-processed.jsonl # 已处理告警
├── data/
│   └── stock_monitor.db       #   SQLite 数据库 (30 天自动清理)
└── docs/
    ├── DESIGN.md              #   系统设计文档
    └── TRIGGER.md             #   异动触发说明
```

## 🚀 快速启动

### 0. 配置凭证

```bash
cd /home/node/.openclaw/workspace/stock-monitor
cp .env.example .env
# 编辑 .env 填入真实的 QQ Bot 凭证
```

### 1. 手动启动 (测试用)

```bash
cd /home/node/.openclaw/workspace/stock-monitor
python3 monitor-daemon.py
```

Ctrl+C 可优雅退出。

### 2. 后台启动 (推荐)

```bash
cd /home/node/.openclaw/workspace/stock-monitor
nohup python3 monitor-daemon.py >> logs/monitor.log 2>&1 &
echo $! > /tmp/stock-monitor.pid
```

### 3. 使用保活脚本

```bash
bash /home/node/.openclaw/workspace/stock-monitor/scripts/keepalive.sh
```

## ⏰ 配置 Cron 保活

```bash
# 每 30 分钟执行一次保活检查
*/30 * * * * /home/node/.openclaw/workspace/stock-monitor/scripts/keepalive.sh
```

**添加方法**: `crontab -e` 粘贴上行，保存退出。**验证**: `crontab -l`

### 保活脚本流程

```
cron 触发 keepalive.sh
        │
        ▼
┌─────────────────────────┐
│ Step 0: 交易日判断       │
│ · 周六日 → 退出          │
│ · 2026 A 股节假日 → 退出  │
│ · 09:00 前 / 15:30 后 → 退出 │
└────────┬────────────────┘
         │ 通过
         ▼
┌─────────────────────────┐
│ Step 1: 进程存活检查      │
│ · 读取 PID 文件           │
│ · 存活 → 记录日志退出     │
└────────┬────────────────┘
         │ 不存在
         ▼
┌─────────────────────────┐
│ Step 2: 启动 daemon      │
│ · nohup python3 &        │
│ · 重定向至 monitor.log    │
└────────┬────────────────┘
         │
         ▼
┌─────────────────────────┐
│ Step 3: 验证启动 (5s后)  │
│ · 读取 daemon 自写入 PID  │
│ · 验证存活 / 输出最近日志  │
└─────────────────────────┘
```

### 单实例保证

- 启动时读取 PID 文件，通过 `/proc/{PID}` 判断旧进程是否存活
- 存活则拒绝启动并报错退出
- 退出时只清理自己写入的 PID 文件，避免误删

## 🏗️ 系统架构

```
常驻进程 (monitor-daemon.py)
  │
  ├── 配置热加载 → 修改 config.json 无需重启
  ├── 交易时段判断 → 活跃 / 休眠
  ├── 多源数据获取 → 腾讯→东方财富→新浪→网易 自动降级
  ├── 波动指标计算 → 涨跌幅 / 振幅 / 量比 / 波动率
  ├── L1 初筛 → 至少2个条件同时满足 + 连续2次命中验证
  ├── L2 确认 → 量价共振 / 极端涨跌 / 极端放量
  ├── 场景分类 → 9种异动场景智能识别
  └── 阶梯式递增告警 → 按趋势/反转/时间衰减规则推送到 QQ
```

## 📊 异动检测算法

### 设计理念

单一指标的瞬时波动多为噪音。真正的异动是 **"价量齐动"**：价格变化必须有成交量配合才具有信号意义。系统通过 **多条件组合 + 连续命中验证 + 阶梯式递增告警** 三层机制过滤噪音。

### L1 — 初筛

**至少 2 个条件同时满足** + 连续命中验证，任一条件不满足即跳过：

| 指标 | 阈值 | 说明 |
|------|------|------|
| 价格波动率 | ≥ 1.0% | 现价偏离昨收的绝对比例 |
| 涨跌幅 | ≥ ±2.5% | 涨跌幅绝对值 |
| 量比 | 分时段动态 | 见下方分时段配置 |
| 盘中振幅 | ≥ 2.0% | (最高-最低)/昨收 |

> 需 ≥ 2 个指标同时超标 + 连续 2 次命中（防止瞬时脉冲），才进入 L2。

### L2 — 确认（量价共振）

三种确认路径，**满足任一条即确认异动**：

| 路径 | 条件 | 说明 |
|------|------|------|
| A. 量价共振 | 涨跌幅 ≥ 3.0% **且** 量比 ≥ 分时段阈值 | 价量同向，主力资金介入 |
| B. 极端涨跌 | \|涨跌幅\| ≥ 5.0% | 忽略量比，优先告警 |
| C. 极端放量 | 量比 ≥ 6.0x **且** \|涨跌幅\| ≥ 1.0% | 异常成交量 |

> 同样要求连续 2 次命中才最终确认，避免单次数据抖动。

### 分时段动态量比阈值

开盘集合竞价导致量比虚高，按交易时段设置不同阈值：

| 时段 | 时间 | L1 量比 | L2 量比 |
|------|------|---------|---------|
| 开盘 | 09:30-10:00 | 3.0 | 5.0 |
| 早盘 | 10:00-11:30 | 2.0 | 3.0 |
| 午后 | 13:00-14:30 | 2.0 | 3.0 |
| 收盘 | 14:30-15:00 | 2.5 | 4.0 |

量比公式：`当前成交量 / (近5日均量 × 已交易时间占比)`，最小有效时间 30 分钟。

## 🎯 阶梯式递增告警

### 为什么不设硬上限

固定次数限制会漏掉关键场景：单边趋势持续扩大、急跌后低位反弹、涨停前持续放量等。改为**按价格偏离阶梯递增**，只在异动实质性演化时才再推送。

### 告警规则

```
首次异动 → 记录参考价和方向，立即推送

后续异动逐次判断：
├─ 极端行情 (≥ ±6%) → 强制通道，不检查间隔
├─ 冷却保护 < 180s → 跳过 (极端行情除外)
├─ 趋势延续 → 同向 + 偏离上次参考价 ≥ 2% → 推送
├─ 方向反转 → 反向 + 偏离上次参考价 ≥ 1.5% → 推送
├─ 时间衰减 → ≥ 30min + 偏离 ≥ 1% → 推送
└─ 不满足 → 静默，等待下次采样

上限兜底：
├─ 非极端：≤ 8 次/天/股
└─ 绝对上限：≤ 15 次/天/股 (含极端)
```

### 9 种异动场景分类

| 场景 | 图标 | 触发条件 | 含义 |
|------|------|----------|------|
| 量价共振 | 📊 | 涨跌幅+量比同时达标 | 主力资金介入 |
| 急速拉升 | 🚀 | 涨幅 ≥ 6% | 关注冲高持续性 |
| 急速下跌 | 💥 | 跌幅 ≥ 6% | 利空或错杀 |
| 异动放量上攻 | 📈 | 量比 ≥ 6x 且涨 ≥ 1% | 吸筹或出货 |
| 异动放量下砸 | 📉 | 量比 ≥ 6x 且跌 ≥ 1% | 恐慌或洗盘 |
| 涨势加速 | 🔥 | 同向上涨偏离参考价 ≥ 2% | 趋势强化 |
| 跌势加速 | 🧊 | 同向下跌偏离参考价 ≥ 2% | 趋势强化 |
| 低位反弹 | 🔄 | 先跌后涨偏离参考价 ≥ 1.5% | 反转信号 |
| 高位回落 | ⚠️ | 先涨后跌偏离参考价 ≥ 1.5% | 见顶信号 |

## 📡 多源数据降级链

```
腾讯 (qt.gtimg.cn) → 东方财富 (push2.eastmoney.com) → 新浪 (hq.sinajs.cn) → 网易 (hq.sinajs.cn 兜底)
```

- 每个数据源内置 3 次重试，1 秒间隔
- 自动降级到下一数据源，无需手动切换
- 统一接口 `fetch_free_data()` 透明降级

## 💬 QQ 推送

- **方式**: QQ 开放平台 C2C 私聊消息
- **鉴权**: AccessToken 自动获取并缓存
- **防重复**: 阶梯式递增告警替代固定间隔，同向偏离 2%/反向偏离 1.5%/30分钟衰减才再推
- **兜底**: 推送失败仍写入 `alerts.json` + `qq-alert-queue.jsonl`

### 告警消息格式

#### 首次告警

```
📊 【量价共振】价量同向异动，主力资金介入信号

📊 上汽集团 (600104)
💰 当前价：¥14.32
📊 涨跌幅：+3.25%
📈 量比：3.50
📊 振幅：4.12%
⏰ 时间：09:35:30

🚨 触发条件:
  • 连续命中: 2/2
  • 涨跌幅=+3.25%(阈值±2.5%)
  • 量比=3.50(阈值3.0[opening])

✅ 确认异动:
  • 量价共振: 涨跌幅=+3.25%(阈值±3.0%) + 量比=3.50(阈值5.0) ✅

请速速查看！🦐
```

#### 趋势延续告警（含参考锚点）

```
🔥 【涨势加速】同向持续扩大，趋势强化

📊 上汽集团 (600104)
💰 当前价：¥14.95
📊 涨跌幅：+4.62%
📈 量比：4.10
📊 振幅：5.80%
📍 上次告警: ¥14.32 (上涨) → 当前偏离 +4.40%
⏰ 时间：09:58:15

🚨 触发条件:
  • 连续命中: 2/2
  • 涨跌幅=+4.62%(阈值±2.5%)
  • 量比=4.10(阈值2.0[morning])

✅ 确认异动:
  • 量价共振: 涨跌幅=+4.62%(阈值±3.0%) + 量比=4.10(阈值3.0) ✅

请速速查看！🦐
```

## 📈 交易时段与动态间隔

| 时段 | 时间 | 状态 | 基础间隔 |
|------|------|------|----------|
| 盘前 | 09:30 前 | 休眠 | 5 分钟 |
| 开盘高波动 | 09:30-10:00 | 监控 | 2-3 秒 |
| 早盘 | 10:00-11:30 | 监控 | 5-8 秒 |
| 午间 | 11:30-13:00 | 休眠 | 5 分钟 |
| 午后 | 13:00-14:30 | 监控 | 5-8 秒 |
| 收盘高波动 | 14:30-15:00 | 监控 | 2-3 秒 |
| 盘后 | 15:00 后 | 休眠 | 5 分钟 |

波动率越高检查越密：≥2% 时 3 秒，≥1% 时 2 秒，≥0.5% 时减半（最低 2 秒）。

## 🧪 测试

```bash
# 全流程测试
python3 /home/node/.openclaw/workspace/stock-monitor/scripts/test-flow.py

# QQ 推送测试
python3 /home/node/.openclaw/workspace/stock-monitor/scripts/test-qq-push.py

# 模拟告警推送 (临时测试文件)
python3 /home/node/.openclaw/workspace/stock-monitor/test-alert-push.py

# 数据源获取测试 (临时测试文件)
python3 /home/node/.openclaw/workspace/stock-monitor/test-fetch-data.py

# 付费数据源测试 (临时测试文件)
python3 /home/node/.openclaw/workspace/stock-monitor/test-paid-sources.py
```

## 📊 监控指标

### 查看进程状态

```bash
cat /tmp/stock-monitor.pid && ps -p $(cat /tmp/stock-monitor.pid) 2>/dev/null && echo "运行中" || echo "未运行"
```

### 查看日志

```bash
tail -f /home/node/.openclaw/workspace/stock-monitor/logs/monitor.log      # 运行日志
tail -f /home/node/.openclaw/workspace/stock-monitor/logs/keepalive.log    # 保活日志
```

### 查看告警

```bash
cat /home/node/.openclaw/workspace/stock-monitor/logs/alerts.json | python3 -m json.tool
```

### 查看数据库

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('/home/node/.openclaw/workspace/stock-monitor/data/stock_monitor.db')
c = conn.cursor()
c.execute('SELECT COUNT(*) FROM stock_data'); print('股票数据:', c.fetchone()[0], '条')
c.execute('SELECT COUNT(*) FROM alerts'); print('告警记录:', c.fetchone()[0], '条')
conn.close()
"
```

## 🔧 配置说明

### 添加监控股票

编辑 `config.json`，在 `stocks` 数组中添加：

```json
{"code": "000001", "name": "平安银行", "enabled": true}
```

### 调整 L1/L2 阈值

```json
{
  "l1_thresholds": {
    "price_change_rate": 0.01,
    "price_change_pct": 2.5,
    "volume_ratio": 2.0,
    "amplitude": 0.02,
    "min_conditions_required": 2,
    "min_consecutive_hits": 2,
    "volume_ratio_by_session": {
      "opening": 3.0,
      "morning": 2.0,
      "afternoon": 2.0,
      "closing": 2.5
    }
  },
  "l2_thresholds": {
    "price_change_pct": 3.0,
    "volume_ratio": 3.0,
    "extreme_price_change_pct": 5.0,
    "extreme_volume_ratio": 6.0,
    "min_price_for_volume": 1.0,
    "min_consecutive_hits": 2,
    "volume_ratio_by_session": {
      "opening": 5.0,
      "morning": 3.0,
      "afternoon": 3.0,
      "closing": 4.0
    }
  }
}
```

### 调整阶梯式递增告警参数

```json
{
  "escalation": {
    "cooldown_seconds": 180,
    "trend_deviation_pct": 2.0,
    "reversal_deviation_pct": 1.5,
    "time_decay_seconds": 1800,
    "time_decay_deviation_pct": 1.0,
    "extreme_pct_threshold": 6.0,
    "max_daily_normal": 8,
    "max_daily_total": 15
  }
}
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `cooldown_seconds` | 180 | 两次告警最小间隔（秒），极端行情绕过 |
| `trend_deviation_pct` | 2.0 | 同向趋势延续：偏离上次参考价 ≥ 2% 才再推 |
| `reversal_deviation_pct` | 1.5 | 方向反转：偏离上次参考价 ≥ 1.5% 才再推 |
| `time_decay_seconds` | 1800 | 时间衰减：距上次告警 ≥ 30min |
| `time_decay_deviation_pct` | 1.0 | 时间衰减：偏离 ≥ 1% |
| `extreme_pct_threshold` | 6.0 | 极端行情阈值：≥ 此值不检查间隔和阶梯 |
| `max_daily_normal` | 8 | 非极端告警每日上限 |
| `max_daily_total` | 15 | 绝对每日上限（含极端） |

### 修改检查间隔

```json
{
  "time_strategy": {
    "intervals": {
      "high_volatility": 3,
      "normal": 8,
      "off_hours": 300
    }
  }
}
```

### QQ 推送凭证

敏感信息已从 `config.json` 移至 `.env` 文件：

```bash
# .env
QQ_APP_ID=你的APP_ID
QQ_CLIENT_SECRET=你的SECRET
QQ_USER_OPENID=用户OPENID
```

> 程序启动时自动加载 `.env`，`config.json` 中的对应字段可作为 fallback。

### 通知行为配置

```json
{
  "notification": {
    "enabled": true,
    "channel": "qqbot_c2c",
    "min_alert_interval": 600,
    "max_daily_alerts_per_stock": 8
  }
}
```

## 🛠️ 故障排查

### 进程未运行

```bash
bash /home/node/.openclaw/workspace/stock-monitor/scripts/keepalive.sh    # 手动启动
cat /home/node/.openclaw/workspace/stock-monitor/logs/keepalive.log       # 查看保活日志
```

### 数据获取失败

检查网络连接：
```bash
curl -I "http://qt.gtimg.cn/q=sh600104"           # 腾讯
curl -I "http://hq.sinajs.cn/list=sh600104"        # 新浪
```

### 告警未推送

```bash
cat /home/node/.openclaw/workspace/stock-monitor/logs/alerts.json             # 查看告警记录
cat /home/node/.openclaw/workspace/stock-monitor/logs/qq-alert-queue.jsonl    # 查看推送队列
```

### Ctrl+C 无法停止

确保使用 `import lib.process as process_mod`（模块引用），而非 `from lib.process import running`（值拷贝）。

## 🔑 核心特性

| 特性 | 说明 |
|------|------|
| 量价组合确认 | L1 多条件 AND + L2 量价共振，过滤单一指标噪音 |
| 连续命中验证 | L1/L2 均要求连续2次命中，防止瞬时脉冲误报 |
| 阶梯式递增告警 | 按价格偏离阶梯递增，趋势/反转/时间衰减自动调节 |
| 9种场景分类 | 量价共振/急速拉升/跌势加速/反弹/回落等智能识别 |
| 多源降级 | 腾讯→东方财富→新浪→网易，自动切换 |
| 分时段量比 | 开盘/早盘/午后/收盘动态阈值，避免集合竞价误触 |
| 配置热加载 | 修改 config.json 无需重启 |
| 动态间隔 | 波动率越高检查越密 (3-300 秒) |
| QQ 直推 | C2C 私聊推送 + 推送失败文件兜底 |
| 防重复 | 阶梯式递增（非简单时间间隔），仅在异动演化时再推 |
| 极端通道 | 涨跌幅 ≥ 6% 强制推送，不检查冷却和阶梯 |
| 单实例保证 | PID 文件原子锁 |
| 交易日历 | 2026 年 A 股休市日 + 交易时段识别 |
| 极轻依赖 | 仅 `requests` 一个第三方包 |

## 🎯 后续计划

1. **Tushare 付费 API** — 配置 Token 后启用 L2 精确验证
2. **添加更多股票** — 在 config.json 中扩展监控列表
3. **优化量比模型** — 集合竞价成交量剔除，更精确的基准量
4. **Web 控制台** — 实时查看状态和调整阈值

---

**版本**: v4.1  
**配置版本**: v4.1  
**更新时间**: 2026-04-27  
**状态**: 运行中
