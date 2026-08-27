#!/bin/bash
# 股票监控保活脚本 — 仅 A 股交易日 + 交易时段才执行
# 被 cron 每 30 分钟调用

# ---- 时区：强制使用 Asia/Shanghai，避免 cron 环境默认 UTC 导致时段判断错误 ----
export TZ=Asia/Shanghai

# ---- 路径配置（自动适配，与 daemon 保持一致）----
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="/tmp/stock-monitor.pid"
SCRIPT="${SCRIPT_DIR}/monitor-daemon.py"
LOGFILE="${SCRIPT_DIR}/logs/keepalive.log"
DAEMON_STDOUT="${SCRIPT_DIR}/logs/daemon-stdout.log"

mkdir -p "$(dirname "$LOGFILE")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOGFILE"; }

# ---- 日志轮转：daemon-stdout.log (最大 10MB，保留 3 个备份) ----
rotate_daemon_stdout() {
    local max_size=$((10 * 1024 * 1024))  # 10MB
    local max_backups=3
    if [ -f "$DAEMON_STDOUT" ]; then
        local file_size=$(stat -f%z "$DAEMON_STDOUT" 2>/dev/null || stat -c%s "$DAEMON_STDOUT" 2>/dev/null || echo 0)
        if [ "$file_size" -gt "$max_size" ]; then
            # 删除最旧的备份
            if [ -f "${DAEMON_STDOUT}.${max_backups}" ]; then
                rm -f "${DAEMON_STDOUT}.${max_backups}"
            fi
            # 轮转：.3→删除, .2→.3, .1→.2, 当前→.1
            for i in $(seq $((max_backups - 1)) -1 1); do
                if [ -f "${DAEMON_STDOUT}.${i}" ]; then
                    mv "${DAEMON_STDOUT}.${i}" "${DAEMON_STDOUT}.$((i + 1))"
                fi
            done
            mv "$DAEMON_STDOUT" "${DAEMON_STDOUT}.1"
            log "📝 daemon-stdout.log 已轮转 (原大小: ${file_size} bytes)"
        fi
    fi
}

# ---- 日志轮转：keepalive.log (最大 5MB，保留 2 个备份) ----
rotate_keepalive_log() {
    local max_size=$((5 * 1024 * 1024))  # 5MB
    # (function defined before invocation)
    local max_backups=2
    if [ -f "$LOGFILE" ]; then
        local file_size=$(stat -f%z "$LOGFILE" 2>/dev/null || stat -c%s "$LOGFILE" 2>/dev/null || echo 0)
        if [ "$file_size" -gt "$max_size" ]; then
            if [ -f "${LOGFILE}.${max_backups}" ]; then
                rm -f "${LOGFILE}.${max_backups}"
            fi
            for i in $(seq $((max_backups - 1)) -1 1); do
                if [ -f "${LOGFILE}.${i}" ]; then
                    mv "${LOGFILE}.${i}" "${LOGFILE}.$((i + 1))"
                fi
            done
            mv "$LOGFILE" "${LOGFILE}.1"
        fi
    fi
}

# ---- 执行日志轮转 (函数已定义) ----
rotate_daemon_stdout
rotate_keepalive_log

# ---- 交易日判断 (使用 lib/trading_calendar.py 作为唯一数据源) ----
if ! python3 -c "
import sys; sys.path.insert(0, '${SCRIPT_DIR}')
from lib.trading_calendar import is_trading_day
exit(0 if is_trading_day() else 1)
" 2>/dev/null; then
    log "⏭️ 非交易日 (周末/节假日)，跳过保活"
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
# nohup 重定向到独立文件，避免与 RotatingFileHandler 冲突
nohup python3 "$SCRIPT" >> "$DAEMON_STDOUT" 2>&1 &
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
    if [ -f "${SCRIPT_DIR}/logs/daemon-stdout.log" ]; then
        log "📋 最近日志: $(tail -3 "${SCRIPT_DIR}/logs/daemon-stdout.log" 2>/dev/null | tr '\n' ' | ')"
    fi
fi
