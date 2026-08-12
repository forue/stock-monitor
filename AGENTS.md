# AGENTS.md - 股票监控系统#

Quick-start command (WSL, run in repo root):
```bash
python3 monitor-daemon.py
```

Background with log:
```bash
nohup python3 monitor-daemon.py >> logs/monitor.log 2>&1 &
echo $! > /tmp/stock-monitor.pid
```

## Environment#

- Runs in **WSL** (Linux), Windows paths like `\\wsl.localhost\Ubuntu\home\...` map to `/home/...`
- The `bash` tool executes inside WSL, **NOT** Windows PowerShell
- **Avoid** PowerShell special chars in commands: `&&`, `>`, `;`, `|` — use space-separated commands or WSL-native commands only
- Example: use `python3 script.py` NOT `python3 script.py && echo done`

## Configuration#

- **Main config**: `config.json` (v4.1)
- **Deprecated**: `config.yaml` (ignore it)
- **Sensitive credentials** (QQ bot): Store in `.env` file, NEVER in `config.json`
  ```
  QQ_APP_ID=your_id
  QQ_CLIENT_SECRET=your_secret
  QQ_USER_OPENID=your_openid
  ```
- **Per-stock technical analysis**: Each stock can override defaults in `tech_analysis` field
- **Global defaults**: `tech_analysis_defaults` section in `config.json`

## Key Commands#

Syntax check (fast, no side effects):
```bash
python3 -m py_compile lib/indicators.py
python3 -m py_compile lib/alerter.py
python3 -m py_compile lib/notifier.py
python3 -m py_compile monitor-daemon.py
```

Git operations (run in WSL path):
```bash
cd ~/.openclaw/workspace/stock-monitor
git status
git add <file>
git commit -m "type: message"
git push
```

**Avoid** `git add file1 && git commit` — use separate `bash` calls.

## Architecture#

Entry point: `monitor-daemon.py` → main loop:
1. Load config + `.env` (via `lib/config.py:load_env_file()`)
2. Check trading session (WSL time)
3. For each enabled stock (10 stocks):
   a. Fetch data: `lib/data_fetcher.py` (Tencent→EastMoney→Sina→NetEase, 3 retries each)
   b. Calculate volatility: `lib/volatility.py`
   c. L1 trigger: `lib/alerter.py:should_trigger_l1()` (≥2 conditions + consecutive hits)
   d. L2 confirm: `lib/alerter.py:confirm_alert()` (volume-price combo / extreme moves)
   e. Technical signals: `lib/alerter.py:check_trading_signal()` (MA cross / RSI)
   f. Real-time features: `fund_flow` / `order_book` / `divergence` (each via `config.real_time_features`, discrete `enabled` switch, failure does NOT block loop)
   g. Send alerts: `lib/notifier.py:send_alert()` / `send_trading_signal()` / `send_feature_alert()`
4. Dynamic sleep: `lib/trading_calendar.py:get_check_interval()`

## Module Map#

| File | Purpose |
|------|----------|
| `lib/config.py` | Load config + `.env` reader (`load_env_file()`) |
| `lib/indicators.py` | **NEW** MA/RSI calc, golden/death cross detection, `detect_divergence()` (top/bottom divergence) |
| `lib/alerter.py` | L1/L2 triggers + 9 scenario classifications + `check_trading_signal()` + real-time checks: `check_fund_flow_signal()` / `check_order_book_signal()` / `check_divergence_signal()` |
| `lib/notifier.py` | Staircase escalation alerts + `send_trading_signal()` + `send_feature_alert()` (real-time alert unified entry) |
| `lib/volatility.py` | Price change rate, amplitude, volume ratio |
| `lib/data_fetcher.py` | Multi-source: qt.gtimg.cn → push2.eastmoney → hq.sina → hq.sina (fallback); + `fetch_fund_flow()` (EastMoney) + `fetch_order_book()` (Tencent 5-level) |
| `lib/trading_calendar.py` | A-share calendar, session detection, dynamic intervals |
| `lib/database.py` | SQLite ops, 30-day cleanup, `get_close_history()` for MA |
| `lib/process.py` | PID lock, signal handlers, interruptible sleep |

Real-time features are single-stock SERIAL requests (free sources rate-limit/bann parallel bulk). Default `enabled:false` — see `docs/FEATURES.md`.

## Testing#

- **Syntax check** (fast, no side effects): `python3 -m py_compile <file>`
- **Full test**: `python3 scripts/test-flow.py`
- **QQ push test**: `python3 test-alert-push.py` (needs `.env` with valid credentials)
- **Never** run unverified scripts with `&&` — use separate WSL commands

## Common Gotchas#

1. **WSL paths**: Windows `\\wsl.localhost\Ubuntu\home\...` → WSL `/home/...`
2. **No `&&` in commands**: PowerShell parses it as special char, use separate `bash` calls
3. **`.env` not `.env.example`**: Real credentials go in `.env` (gitignored)
4. **RSI period**: Needs `period+1` data points (14-day RSI needs 15 days of closes)
5. **Golden cross**: Requires previous MA values for comparison (stored in `_prev_ma_state` dict)
6. **Database reads only**: `get_close_history()` SELECTs only, never writes — safe for testing
7. **Cron**: `*/30 * * * * /path/to/keepalive.sh` — script handles WSL env

## File Structure (what matters)#

```
config.json          # Main config (v4.1, tech_analysis per stock)
.env                # Credentials (gitignored, see .env.example)
lib/indicators.py   # NEW: MA/RSI/golden-death cross
lib/alerter.py      # Extended: check_trading_signal()
lib/notifier.py     # Extended: send_trading_signal()
monitor-daemon.py    # Extended: tech analysis branch in main loop
README.md           # Extended: technical indicators docs
AGENTS.md           # This file
```

## Commit Convention#

- Prefix: `feat:` `fix:` `docs:` `refactor:`
- Example: `feat: add technical indicators (MA/RSI Golden/Death Cross) + trading signals`
- Keep messages under 72 chars