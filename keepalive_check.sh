#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOGFILE="${SCRIPT_DIR}/logs/keepalive.log"
DAEMON_STDOUT="${SCRIPT_DIR}/logs/daemon-stdout.log"
PIDFILE="/tmp/stock-monitor.pid"
mkdir -p "$(dirname "$LOGFILE")"

echo "===== Keepalive check started at $(date) =====" >> "$LOGFILE" 2>&1

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOGFILE" 2>&1
}

# Check if today is a trading day (uses lib/trading_calendar.py as single source of truth)
if ! python3 -c "
import sys; sys.path.insert(0, '${SCRIPT_DIR}')
from lib.trading_calendar import is_trading_day
exit(0 if is_trading_day() else 1)
" 2>/dev/null; then
    log "Today is not a trading day (weekend/holiday), skip."
    exit 0
fi

# Check if within trading hours 09:00-15:30
current_time=$(date +%H:%M)
if [[ "$current_time" < "09:00" ]] || [[ "$current_time" > "15:30" ]]; then
    log "Outside trading hours (09:00-15:30): $current_time, skip."
    exit 0
fi

log "Within trading hours, proceeding..."

if [ -f "$PIDFILE" ]; then
    pid=$(cat "$PIDFILE")
    if [ -n "$pid" ] && ps -p "$pid" > /dev/null 2>&1; then
        log "进程运行正常 (PID: $pid)"
        exit 0
    else
        log "PID file exists but process not running (PID: $pid). Will attempt to start."
    fi
else
    log "PID file not found. Will attempt to start."
fi

# Start the daemon
cd "$SCRIPT_DIR"

# 先删除可能由 shell 写入的残留 PID 文件
rm -f "$PIDFILE"

# 使用独立文件重定向 stdout/stderr，避免与 daemon 的 RotatingFileHandler 冲突
nohup python3 monitor-daemon.py >> "$DAEMON_STDOUT" 2>&1 &

# 等待 daemon 自己写入 PID（由 lib/process.py 的 check_and_write_pid 完成）
sleep 5

# Re-read PID file (daemon should have written its own PID)
if [ -f "$PIDFILE" ]; then
    pid=$(cat "$PIDFILE")
    if [ -n "$pid" ] && ps -p "$pid" > /dev/null 2>&1; then
        log "启动验证成功 (PID: $pid)"
        exit 0
    else
        log "启动验证失败"
        if [ -f "$DAEMON_STDOUT" ]; then
            tail -3 "$DAEMON_STDOUT" >> "$LOGFILE" 2>&1
        else
            log "daemon-stdout.log not found"
        fi
        exit 1
    fi
else
    log "启动验证失败: PID file not found after start"
    exit 1
fi
