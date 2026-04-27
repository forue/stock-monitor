#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
进程管理模块 - PID 管理/信号处理/可中断 sleep
"""

import os
import sys
import time
import signal

from lib.logger import log

# 全局运行标志
running = True


def signal_handler(sig, frame):
    """信号处理：收到 SIGTERM/SIGINT 时设置退出标志"""
    global running
    log(f"收到终止信号 (sig={sig})，准备退出...")
    running = False


def interruptible_sleep(seconds: float):
    """可中断的 sleep，每秒检查 running 标志，响应 Ctrl+C"""
    end = time.time() + seconds
    while running and time.time() < end:
        time.sleep(min(1, end - time.time()))


def check_and_write_pid(pidfile: str):
    """检查是否已有实例运行，若无则写入 PID 文件（原子锁）"""
    if os.path.exists(pidfile):
        try:
            old_pid = int(open(pidfile).read().strip())
            if old_pid > 0 and os.path.exists(f"/proc/{old_pid}"):
                print(f"❌ 已有实例运行中 (PID: {old_pid})，退出", file=sys.stderr)
                sys.exit(1)
            else:
                log(f"清理残留 PID 文件 (旧 PID: {old_pid} 已不存在)")
                os.remove(pidfile)
        except (ValueError, OSError):
            pass

    try:
        with open(pidfile, 'w') as f:
            f.write(str(os.getpid()))
        log(f"进程启动 (PID: {os.getpid()})")
    except Exception as e:
        log(f"写入 PID 文件失败：{e}", level="ERROR")
        sys.exit(1)


def cleanup_pid(pidfile: str):
    """清理 PID 文件（仅删除自己写入的）"""
    if os.path.exists(pidfile):
        try:
            pid_in_file = int(open(pidfile).read().strip())
            if pid_in_file == os.getpid():
                os.remove(pidfile)
                log("已清理 PID 文件")
        except (ValueError, OSError):
            pass


def register_signal_handlers():
    """注册信号处理器"""
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
