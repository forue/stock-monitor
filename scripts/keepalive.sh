#!/bin/bash
# 股票监控保活脚本 — 仅 A 股交易日 + 交易时段才执行
# 被 cron 每 30 分钟调用

# ---- 路径配置（自动适配，与 daemon 保持一致）----
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="/tmp/stock-monitor.pid"
SCRIPT="${SCRIPT_DIR}/monitor-daemon.py"
LOGFILE="${SCRIPT_DIR}/logs/keepalive.log"
MONITOR_LOG="${SCRIPT_DIR}/logs/monitor.log"

mkdir -p "$(dirname "$LOGFILE")" "$(dirname "$MONITOR_LOG")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOGFILE"; }

# ---- 交易日判断 ----
DOW=$(date +%u)   # 1=周一 … 6=周六 7=周日
TODAY=$(date +%Y-%m-%d)

# 周末直接跳过
[ "$DOW" -ge 6 ] && { log "⏭️ 周末，跳过"; exit 0; }

# 2026 年 A 股节假日列表（交易所公布）
is_holiday() {
    case "$1" in
        2026-01-01|2026-01-02) ;;
        2026-02-16|2026-02-17|2026-02-18|2026-02-19|2026-02-20|2026-02-23) ;;
        2026-04-06) ;;
        2026-05-01|2026-05-04|2026-05-05) ;;
        2026-06-19) ;;
        2026-09-25) ;;
        2026-10-01|2026-10-02|2026-10-06|2026-10-07) ;;
        *) return 1 ;;
    esac
    return 0
}

if is_holiday "$TODAY"; then
    log "🎌 节假日休市 ($TODAY)，跳过保活"
    exit 0
fi

# 交易时段判断 (09:00-15:30，留余量覆盖开盘准备)
H=$(date +%H)
M=$(date +%M)
VAL=$((10#$H * 100 + 10#$M))
if [ "$VAL" -lt 900 ] || [ "$VAL" -gt 1530 ]; then
    log "⏭️ 非交易时段 (${H}:${M})，跳过"
    exit 0
fi

# ---- 进程存活检查 ----
PID=$(cat "$PIDFILE" 2>/dev/null)

if [ -n "$PID" ] && ps -p "$PID" > /dev/null 2>&1; then
    log "✅ 进程运行正常 (PID: $PID)"
    exit 0
fi

log "⚠️ 进程未运行，启动..."
rm -f "$PIDFILE"

cd "$SCRIPT_DIR"
# daemon 自身通过 RotatingFileHandler 管理格式化日志
# nohup 重定向作为兜底，捕获未处理的异常和 print 输出
nohup python3 "$SCRIPT" >> "$MONITOR_LOG" 2>&1 &
NEW_PID=$!

# 等待 daemon 完成初始化（含数据库、配置加载等）
sleep 5

# 读取 daemon 自己写入的 PID（更准确）
DAEMON_PID=$(cat "$PIDFILE" 2>/dev/null)

if [ -n "$DAEMON_PID" ] && ps -p "$DAEMON_PID" > /dev/null 2>&1; then
    log "🚀 进程已启动 (PID: $DAEMON_PID)"
    log "✅ 启动验证成功"
elif [ -n "$NEW_PID" ] && ps -p "$NEW_PID" > /dev/null 2>&1; then
    # daemon 未写 PID 文件但进程仍存活（可能初始化中）
    echo "$NEW_PID" > "$PIDFILE"
    log "🚀 进程已启动 (PID: $NEW_PID, 使用 nohup PID)"
    log "✅ 启动验证成功"
else
    log "❌ 启动验证失败，进程可能已退出"
    # 输出最后几行日志便于排查
    if [ -f "${SCRIPT_DIR}/logs/monitor.log" ]; then
        log "📋 最近日志: $(tail -3 "${SCRIPT_DIR}/logs/monitor.log" 2>/dev/null | tr '\n' ' | ')"
    fi
fi
