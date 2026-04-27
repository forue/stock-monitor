#!/bin/bash
set -e

LOGFILE="/home/node/.openclaw/workspace/stock-monitor/logs/keepalive.log"
echo "===== Keepalive check started at $(date) =====" >> "$LOGFILE" 2>&1

# Function to log message
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOGFILE" 2>&1
}

# Check if today is weekend
day_of_week=$(date +%u) # 1=Mon, 7=Sun
if [ "$day_of_week" -gt 5 ]; then
    log "Today is weekend, skip."
    exit 0
fi

# Check if today is a holiday (MM-DD format)
today_mmdd=$(date +%m-%d)
holidays="01-01 01-02 02-16 02-17 02-18 02-19 02-20 02-23 04-06 05-01 05-04 05-05 06-19 09-25 10-01 10-02 10-06 10-07"
if [[ " $holidays " == *" $today_mmdd "* ]]; then
    log "Today is a holiday ($today_mmdd), skip."
    exit 0
fi

# Check if within trading hours 09:00-15:30
current_time=$(date +%H:%M)
if [[ "$current_time" < "09:00" ]] || [[ "$current_time" > "15:30" ]]; then
    log "Outside trading hours (09:00-15:30): $current_time, skip."
    exit 0
fi

log "Within trading hours, proceeding..."

PIDFILE="/tmp/stock-monitor.pid"
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
cd /home/node/.openclaw/workspace/stock-monitor
nohup python3 monitor-daemon.py >> logs/monitor.log 2>&1 &
echo $! > /tmp/stock-monitor.pid  # Write our own PID? Actually daemon should write its own PID. We'll wait and read back.
# Wait 5 seconds
sleep 5

# Re-read PID file (daemon should have written its own PID)
if [ -f "$PIDFILE" ]; then
    pid=$(cat "$PIDFILE")
    if [ -n "$pid" ] && ps -p "$pid" > /dev/null 2>&1; then
        log "启动验证成功 (PID: $pid)"
        exit 0
    else
        log "启动验证失败"
        # Output last 3 lines of monitor.log
        if [ -f "logs/monitor.log" ]; then
            tail -3 logs/monitor.log >> "$LOGFILE" 2>&1
        else
            log "logs/monitor.log not found"
        fi
        exit 1
    fi
else
    log "启动验证失败: PID file not found after start"
    exit 1
fi