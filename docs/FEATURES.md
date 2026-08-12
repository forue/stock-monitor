# 实盘辅助功能设计 — 主力资金流 / 五档盘口 / 量价背离

> 版本: v1.0
> 日期: 2026-08-12
> 状态: 设计稿（待实现）
> 关联: 基于 v4.1 架构，复用现有数据流、config 热加载、阶梯式告警与 QQ 推送

---

## 0. 总览

| 功能 | 名称 | 数据源 | 依赖模块 | 新依赖 |
|------|------|--------|----------|--------|
| 1 | 主力资金流向监控 | 东方财富 `fflow/kline` | data_fetcher / alerter / notifier | 无 |
| 2 | 五档盘口监控 | 腾讯 qq 行情（买卖五档） | data_fetcher / alerter / notifier | 无 |
| 8 | 量价背离检测 | 现存 DB 历史 + 腾讯现价/量 | indicators / alerter / notifier | 无 |

三个功能都采用**单只串行请求**，均不违反「免费源不可并行批量读取」约束。

设计原则：
1. **按需获取**：仅在验证阶段确认东财资金流接口有实时数据的前提下启用，接口不可用则静默降级（不阻断主循环）。
2. **职责清晰**：`data_fetcher.py` 只负责取数解析；`alerter.py` 负责判断与去重；`notifier.py` 只负责按场景构建并推送消息；主循环只做调度与数据装配。
3. **可配置**：每个功能均有独立开关与阈值，统一放在 `config.json` 的 `real_time_features` 段，支持热加载。
4. **可降级**：任一数据源失败都不影响原有异动监控链路。

---

## 1. 功能一：主力资金流向监控

### 1.1 目标
监控盘中主力资金（超大单+大单）净流入/净流出，识别「主力吸筹」「主力出货」以及「价量金背离」（价格涨但主力净流出 = 诱多）。

### 1.2 数据源
东方财富 `push2.eastmoney.com` 实时资金流接口：

```
GET https://push2.eastmoney.com/api/qt/stock/fflow/kline/get
params:
  secid    = 1.600104 (沪 1. / 深 0.)
  klt      = 1        (1 分钟粒度)
  lmt      = N        (取最近 N 根 1 分钟 K 线，用于判断连续性)
  fields1  = f1,f2,f3,f7
  fields2  = f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65
```

返回 JSON `data.klines`，每行逗号分隔，字段索引（已验证 2026-08-12）：
```
0  f51 时间        "2026-08-12 14:58"
1  f52 主力净流入(元)   = f55大单 + f56超大单
2  f53 小单净流入(元)
3  f54 中单净流入(元)
4  f55 大单净流入(元)
5  f56 超大单净流入(元)
```
单位：元；正值=净流入，负值=净流出。

### 1.3 数据模型

`data_fetcher.fetch_fund_flow(stock_code) -> Optional[dict]`

```python
{
  'code': stock_code,
  'main_net':  main_net_inflow,      # 主力净流入(元)
  'super_net': super_net_inflow,     # 超大单净流入(元)
  'large_net': large_net_inflow,     # 大单净流入(元)
  'main_net_ratio': main_net/成交额,  # 主力净流入占比
  'timestamp': ...,
  'source': 'eastmoney_fflow',
}
```

### 1.4 触发规则（`alerter.check_fund_flow_signal`）

依据 `main_net` 与价格方向组合：

| 场景 | 条件 | 信号 |
|------|------|------|
| 主力吸筹 | `main_net > X` 且 `price_change_pct > 0` | 资金看多 |
| 主力出货 | `main_net < -X` 且 `price_change_pct < 0` | 资金看空 |
| 价涨资金流出（诱多） | `price_change_pct > 0` 且 `main_net < -Y` | 背离警示 |
| 价跌资金流入（吸筹） | `price_change_pct < 0` 且 `main_net > Y` | 抄底观察 |

去重：复用阶梯式递增思路，配置 `data_check_interval`（默认 300s）防抖 + 每日上限。

### 1.5 收敛到 config

```json
"real_time_features": {
  "fund_flow": {
    "enabled": true,
    "check_interval": 300,
    "net_inflow_th": 1000000,     // 主力净流入阈值(元)，默认 100 万
    "net_outflow_th": -1000000,   // 主力净流出阈值(元)
    "ratio_th": 0.05              // 主力净流入占比阈值
  }
}
```

---

## 2. 功能二：五档盘口监控

### 2.1 目标
监控买卖五档挂单结构，识别：
- **委比极端**（买盘 vs 卖盘挂单失衡）
- **托盘/压盘**（下方/上方大单持续挂单）
- **封单/炸板**（涨停时封单量变化）

### 2.2 数据源
腾讯 `qt.gtimg.cn`，`~` 分割（已验证 2026-08-12，88 字段）：

| 索引 | 含义 | 索引 | 含义 |
|------|------|------|------|
| 3 | 现价 | 4 | 昨收 |
| 9/10 | 买一价/量(手) | 19/20 | 卖一价/量(手) |
| 11/12 | 买二价/量 | 21/22 | 卖二价/量 |
| 13/14 | 买三价/量 | 23/24 | 卖三价/量 |
| 15/16 | 买四价/量 | 25/26 | 卖四价/量 |
| 17/18 | 买五价/量 | 27/28 | 卖五价/量 |
| 47 | 涨停价 | 48 | 跌停价 |
| 50 | 委差 | 51 | 当日均价 |

### 2.3 数据模型

`data_fetcher.fetch_order_book(stock_code) -> Optional[dict]`

```python
{
  'code': stock_code,
  'bids': [(price, qty), ...5档],   # 买一~买五 (价, 手)
  'asks': [(price, qty), ...5档],   # 卖一~卖五 (价, 手)
  'bid_total': 买盘总手, 'ask_total': 卖盘总手,
  'vi_ratio': 委比 = (bid-ask)/(bid+ask),   # 范围 -1 ~ 1
  'limit_up': 涨停价, 'limit_down': 跌停价,
}
```

### 2.4 触发规则（`alerter.check_order_book_signal`）

| 场景 | 条件 |
|------|------|
| 委比失衡（托盘） | `vi_ratio > VI_HIGH`（默认 0.6） |
| 委比失衡（压盘） | `vi_ratio < VI_LOW`（默认 -0.6） |
| 封板检测 | 现价 == 涨停价 且 卖一量为 0 |
| 巨量封单 | 涨停 + 买一量 > SEAL_HIGH（手） |

去重：配置 `check_interval` 防抖，同场景未变化不重复推送。

---

## 3. 功能八：量价背离检测

### 3.1 目标
检测**顶背离 / 底背离**：价格创新高/新低但量能（或 RSI）未同步，是变盘信号。
- 顶背离：价格创新高，但 RSI/量能走低 → 见顶预警
- 底背离：价格创新低，但 RSI/量能走高 → 见底信号

### 3.2 数据源
复用现有 DB（`stock_data` 表已有每轮 `current_price` 与 `volume`）+ 现有腾讯现价。因需多日粒度，采用 `indicators.get_close_history` 类似查询（低频，非阻塞）。

### 3.3 算法（`indicators.detect_divergence`）

取一段价格序列 P[0..n] 与成交量序列 V[0..n]（或 RSI）：

```
顶背离:
  价格段高点索引 idx_price_high = argmax(P[-window:])
  量的段高点 idx_vol_high     = argmax(V 对应时段)
  若 P 创新高 但 V 未创新高（或 RSI 下降）→ 顶背离
```

返回 `{'type': 'top'|'bottom', 'strength': float, ...}`。

### 3.4 触发规则（`alerter.check_divergence_signal`）

| 类型 | 条件 |
|------|------|
| 顶背离 | 价格段创新高 + RSI 未创新高 + 量能萎缩 |
| 底背离 | 价格段创新低 + RSI 未创新低 + 量能回升 |

低频检测：配置 `check_interval`（默认 300s），每日强信号有限次。

---

## 4. 主循环集成（monitor-daemon.py）

在现有「逐只取数 → 计算 → 判断」的主循环内，为每个功能增加独立分支（均在**同一轮数据**基础上，约构建一次实时快照）：

```
for stock in stocks:
    data = fetch_free_data(code)        # 原有：现价/量/高低
    metrics = calculate_volatility(...) # 原有
    db_writer.save_stock_data(...)      # 原有

    # —— 实盘辅助功能（按需，各自 try/except，失败不阻断）——
    if fw.enabled:  fund_flow = fetch_fund_flow(code)
                    signal = check_fund_flow_signal(...)
                    if signal: send_fund_flow(...)

    if ob.enabled:  order_book = fetch_order_book(code)
                    signal = check_order_book_signal(...)
                    if signal: send_alert(...)

    if dv.enabled:  signal = check_divergence_signal(...)
                    if signal: send_alert(...)
```

要点：
- 每个功能独立 `enabled` 开关，未开启则**不发起对应请求**（保护免费源频控）。
- 各功能请求**串行**、带轻量频率限制，不违反「不可并行批量读取」约束。
- 数据获取失败 → 记日志、跳过该功能本轮，不影响其他功能与原有异动监控。

---

## 5. 模块职责

| 模块 | 新增职责 |
|------|----------|
| `lib/data_fetcher.py` | `fetch_fund_flow()` 东财资金流解析；`fetch_order_book()` 腾讯五档解析；两者统一返回 dict 或 None |
| `lib/indicators.py` | `detect_divergence()` 顶/底背离算法 |
| `lib/alerter.py` | `check_fund_flow_signal()` / `check_order_book_signal()` / `check_divergence_signal()` 判断 + 去重状态 + 场景消息 |
| `lib/notifier.py` | 复用 `send_alert`，并新增各功能专用推送函数（构建消息 → QQ + 文件备份） |
| `monitor-daemon.py` | 主循环内集成三个功能的调度分支 |

---

## 6. 降级与容错

| 故障 | 处理 |
|------|------|
| 东财资金流接口失败 | `fetch_fund_flow` 返回 None，跳过本轮功能 |
| 腾讯五档接口失败 | `fetch_order_book` 返回 None，跳过功能 |
| DB 历史不足（量价背离） | `detect_divergence` 返回 None，不告警 |
| 推送失败 | 沿用 `_write_alert_file` 兜底 |
| 任一功能异常 | 独立 `try/except`，不扩散到主循环 |

---

## 7. 测试

- 语法检查：`python3 -m py_compile` 各模块
- 接口连通：`python3 -c` 直接调 `fetch_fund_flow` / `fetch_order_book`
- 全流程：`python3 scripts/test-flow.py`
- 手动验证推送：临时构造 signal 调用 `send_*`
