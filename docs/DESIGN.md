# 股票智能监控系统

**版本**: v3.1  
**日期**: 2026-04-03  
**模式**: 常驻进程 + QQ C2C 直推

---

## 系统架构

```
常驻进程 (monitor-daemon.py)
  ├── 时间判断 → 交易时段活跃 / 非交易时段休眠
  ├── L1 筛查 → 东方财富免费数据，按需 1-5 秒检查
  ├── L2 验证 → 严格阈值确认异动
  └── QQ 推送 → 调用 QQ 开放平台 C2C API 直推
```

- **保活**: 独立启动或使用系统 Cron（`scripts/keepalive.sh`）
- **存储**: SQLite 数据库 + JSON 日志文件
- **推送**: 直接推送至用户 QQ 私信（C2C），不依赖 Cron 或频道

---

## 监控股票

| 代码 | 名称 | 状态 |
|------|------|------|
| 600104 | 上汽集团 | ✅ |
| 002475 | 立讯精密 | ✅ |
| 600519 | 贵州茅台 | ✅ |

---

## 分层监控

### L1 — 免费数据筛查

- 数据源: 东方财富 HTTP API（无限制）
- 触发阈值（任一即进入 L2）:

| 指标 | 阈值 |
|------|------|
| 价格波动率 | ≥ 0.7% |
| 涨跌幅 | ≥ 2.5% |
| 量比 | ≥ 2.5 倍 |
| 盘中振幅 | ≥ 2.0% |

### L2 — 确认验证

- 使用更严格阈值确认:

| 指标 | 阈值 |
|------|------|
| 涨跌幅 | ≥ 3.0% |
| 量比 | ≥ 3.0 倍 |

- 确认后通过 QQ C2C 推送告警

---

## 时间策略

| 时段 | 时间 | 检查间隔 |
|------|------|----------|
| 开盘高波动期 | 09:30-10:00 | 1-2 秒 |
| 早盘正常期 | 10:00-11:30 | 2-5 秒 |
| 午间休市 | 11:30-13:00 | 5 分钟（休眠） |
| 午后正常期 | 13:00-14:30 | 2-5 秒 |
| 收盘高波动期 | 14:30-15:00 | 1-2 秒 |
| 非交易时段 | 其他 | 5 分钟（休眠） |

波动率越高，检查越密。

---

## QQ 推送

- **方式**: QQ 开放平台 C2C 私聊消息 API
- **端点**: `POST /v2/users/{openid}/messages`
- **鉴权**: AccessToken 自动获取并缓存（`/app/getAppAccessToken`）
- **防重复**: 同只股票 5 分钟内不重复推送
- **告警格式**: 股票名称、代码、当前价、涨跌幅、量比、时间

配置在 `config.json` 的 `notification` 段，含 `qq_app_id`、`qq_client_secret`、`user_openid`。

---

## 文件结构

```
stock-monitor/
├── monitor-daemon.py       # 监控主程序
├── config.json             # 配置（股票、阈值、通知）
├── docs/
│   └── DESIGN.md           # 本文件
├── scripts/
│   ├── keepalive.sh        # 保活脚本
│   └── test-flow.py        # 流程测试
├── logs/
│   ├── monitor.log         # 运行日志
│   └── alerts.json         # 告警记录
└── data/
    └── stock_monitor.db    # SQLite 数据库
```

---

## 启动方式

```bash
cd stock-monitor
nohup python3 monitor-daemon.py >> logs/monitor.log 2>&1 &
echo $! > /tmp/stock-monitor.pid
```

保活（可选）: `*/30 * * * * stock-monitor/scripts/keepalive.sh`
