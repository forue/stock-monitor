# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Quick start

```bash
python3 monitor-daemon.py
```

Background:
```bash
nohup python3 monitor-daemon.py >> logs/daemon-stdout.log 2>&1 &
echo $! > /tmp/stock-monitor.pid
```

## Environment

- Runs in **WSL** (Linux). Windows paths like `\\wsl.localhost\Ubuntu\home\...` map to `/home/...`
- The `bash` tool executes inside WSL, **NOT** Windows PowerShell
- **Avoid** PowerShell special chars in commands: `&&`, `>`, `;`, `|` — use separate tool calls or WSL-native commands

## Configuration

- **Main config**: `config.json` (v4.1)
- **Sensitive credentials** in `.env` (gitignored), NEVER in `config.json`:
  - `QQ_APP_ID`, `QQ_CLIENT_SECRET`, `QQ_USER_OPENID`
- Per-stock `tech_analysis` overrides globals in `tech_analysis_defaults`
- Escalation keys are **decimal** (e.g., `trend_deviation: 0.02` = 2%), not percentage form
- `STOCK_MONITOR_HOME` env var overrides base dir; defaults to repo root

## Key commands

Syntax check (fast, no side effects):
```bash
python3 -m py_compile lib/indicators.py
python3 -m py_compile lib/alerter.py
python3 -m py_compile lib/notifier.py
python3 -m py_compile monitor-daemon.py
```

Full flow test:
```bash
python3 scripts/test-flow.py
```

QQ push test (needs valid `.env`):
```bash
python3 test-alert-push.py
```

## Architecture

Entry point: `monitor-daemon.py` — single main loop:

1. Load config + `.env` (via `lib/config.py:load_env_file()`)
2. Check A-share trading session (timezone locked to `Asia/Shanghai`)
3. For each enabled stock:
   - Fetch data: `lib/data_fetcher.py` (Tencent → EastMoney → Sina → NetEase, 3 retries each)
   - Calculate volatility: `lib/volatility.py` (price change rate, amplitude, volume ratio)
   - L1 trigger: `lib/alerter.py:should_trigger_l1()` (≥2 conditions + consecutive hits)
   - L2 confirm: `lib/alerter.py:confirm_alert()` (volume-price combo / extreme moves)
   - Technical signals: `lib/alerter.py:check_trading_signal()` (MA cross + RSI filter)
   - Send alerts: `lib/notifier.py:send_alert()` with staircase escalation
4. Dynamic sleep: `lib/trading_calendar.py:get_check_interval()`

## Module map

| File | Purpose |
|------|----------|
| `lib/config.py` | Load config + `.env` reader + hot-reload via mtime |
| `lib/indicators.py` | MA/RSI calculation, golden/death cross detection, daily close history |
| `lib/alerter.py` | L1/L2 triggers, 9 scenario classifications, `check_trading_signal()`, message builders |
| `lib/notifier.py` | Staircase escalation (trend/reversal/time_decay/extreme), QQ C2C push, alert file backup |
| `lib/volatility.py` | Price change rate, amplitude, volume ratio |
| `lib/data_fetcher.py` | Multi-source fetch: qt.gtimg.cn → push2.eastmoney → hq.sina → NetEase fallback |
| `lib/trading_calendar.py` | A-share calendar (2026 holidays), session detection, dynamic intervals |
| `lib/database.py` | SQLite WAL mode, 30-day cleanup, batch writes, `get_close_history()` |
| `lib/logger.py` | RotatingFileHandler (10MB × 5) |
| `lib/process.py` | PID lock, signal handlers (SIGTERM/SIGINT), interruptible sleep |

## Common gotchas

1. **WSL paths**: Windows `\\wsl.localhost\Ubuntu\home\...` → WSL `/home/...`
2. **No `&&` in bash tool**: PowerShell parses it as special char; use separate tool calls
3. **`.env` not committed**: Real credentials go in `.env` (gitignored), template in `.env.example`
4. **`check_trading_signal` config merge**: `stock_config` IS already the `tech_analysis` dict; don't call `.get('tech_analysis')` on it
5. **RSI needs `period+1` data points**: 14-day RSI requires 15 days of close history
6. **MA cross state consumed once**: `save_curr_ma()` must happen AFTER successful cross detection, not before
7. **nohup stdout → `logs/daemon-stdout.log`**: Separate file from RotatingFileHandler's `logs/monitor.log` to avoid corruption
8. **Timezone**: `os.environ['TZ'] = 'Asia/Shanghai'` + `time.tzset()` at module level in daemon
9. **`scripts/trading_calendar.py`**: Deleted; all logic in `lib/trading_calendar.py`

## Commit convention

- Prefix: `feat:` `fix:` `docs:` `refactor:`
- Keep messages under 72 chars
- Example: `feat: add technical indicators (MA/RSI Golden/Death Cross) + trading signals`
