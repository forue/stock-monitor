# AGENTS.md - 股票监控系统

Quick-start command (WSL):
```bash
cd /home/fayou/.openclaw/workspace/stock-monitor
python3 monitor-daemon.py
```

Background with log:
```bash
cd /home/fayou/.openclaw/workspace/stock-monitor
nohup python3 monitor-daemon.py >> logs/monitor.log 2>&1 &
echo $! > /tmp/stock-monitor.pid
```

## Environment

- Runs in **WSL** (Linux), Windows paths like `\\wsl.localhost\Ubuntu\home\...`  map to `/home/...`
- The `bash` tool executes inside WSL, NOT Windows PowerShell
- **Avoid** PowerShell special chars in commands: `&&`, `>`, `;`, `|` — use space-separated `;` or WSL-native commands only
- Example: use `python3 script.py` NOT `python3 script.py && echo done`

## Configuration

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

## Key Commands

Syntax check (prefer over running):
```bash
python3 -m py_compile lib/indicators.py
python3 -m py_compile lib/alerter.py
python3 -m py_compile lib/notifier.py
python3 -m py_compile monitor-daemon.py
```

Git operations (run in WSL path):
```bash
cd /home/fayou/.openclaw/workspace/stock-monitor
git status
git add file1 file2
git commit -m "message"
git push
```

**Avoid** `git add file1 && git commit` — use separate commands.

## Architecture

Entry point: `monitor-daemon.py` → main loop:
1. Load config + `.env` (via `lib/config.py:load_env_file()`)
2. Check trading session (WSL time)
3. For each enabled stock (10 stocks):
   a. Fetch data: `lib/data_fetcher.py` (Tencent→EastMoney→Sina→NetEase, 3 retries each)
   b. Calculate volatility: `lib/volatility.py`
   c. L1 trigger: `lib/alerter.py:should_trigger_l1()` (≥2 conditions + consecutive hits)
   d. L2 confirm: `lib/alerter.py:confirm_alert()` (volume-price combo / extreme moves)
   e. Technical signals: `lib/alerter.py:check_trading_signal()` (MA cross / RSI)
   f. Send alerts: `lib/notifier.py:send_alert()` or `send_trading_signal()`
4. Dynamic sleep: `lib/trading_calendar.py:get_check_interval()`

## Module Map

| File | Purpose |
|------|----------|
| `lib/config.py` | Load config + `.env` reader (`load_env_file()`) |
| `lib/indicators.py` | **NEW** MA/RSI calc, golden/death cross detection |
| `lib/alerter.py` | L1/L2 triggers + 9 scenario classifications + **NEW** `check_trading_signal()` + `build_trading_signal_message()` |
| `lib/notifier.py` | Staircase escalation alerts + QQ C2C push + **NEW** `send_trading_signal()` |
| `lib/volatility.py` | Price change rate, amplitude, volume ratio |
| `lib/data_fetcher.py` | Multi-source: qt.gtimg.cn → push2.eastmoney → hq.sinajs.cn → hq.sinajs.cn |
| `lib/trading_calendar.py` | A-share calendar, session detection, dynamic intervals |
| `lib/database.py` | SQLite ops, 30-day cleanup, `get_close_history()` for MA |
| `lib/process.py` | PID lock, signal handlers, interruptible sleep |

## Testing

- **Syntax check** (fast, no side effects): `python3 -m py_compile <file>`
- **Full test**: `python3 scripts/test-flow.py`
- **QQ push test**: `python3 test-alert-push.py` (needs `.env` with valid credentials)
- **Never** run unverified scripts with `&&` — use separate WSL commands

## Common Gotchas

1. **WSL paths**: Windows `\\wsl.localhost\Ubuntu\...` → WSL `/home/...`
2. **No `&&` in commands**: PowerShell parses it as special char, use separate `bash` calls
3. **`.env` not `.env.example`**: Real credentials go in `.env` (gitignored)
4. **RSI period**: Needs `period+1` data points (14-day RSI needs 15 days of closes)
5. **Golden cross**: Requires previous MA values for comparison (stored in `_prev_ma_state` dict)
6. **Database reads only**: `get_close_history()` SELECTs only, never writes — safe for testing
7. **Cron**: `*/30 * * * * /path/to/keepalive.sh` — script handles WSL env

## File Structure (what matters)

```
config.json          # Main config (v4.1, tech_analysis per stock)
.env                # Credentials (gitignored, see .env.example)
lib/indicators.py   # NEW: MA/RSI/golden-death cross
lib/alerter.py      # Extended: check_trading_signal()
lib/notifier.py     # Extended: send_trading_signal()
monitor-daemon.py    # Extended: tech analysis branch in main loop
```

## Commit Convention

- Prefix: `feat:` `fix:` `docs:` `refactor:`
- Example: `feat: add technical indicators (MA/RSI Golden/Death Cross) + trading signals`
- Keep messages under 72 chars
