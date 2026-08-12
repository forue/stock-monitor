#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票智能监控 daemon - v4.0 (模块化重构)
常驻进程模式 + Cron 保活 + QQ 推送

模块结构:
  lib/config.py           - 配置加载/热加载
  lib/logger.py           - 日志系统
  lib/trading_calendar.py - 交易日历/时段判断
  lib/data_fetcher.py     - 免费数据源(腾讯/新浪)
  lib/volatility.py       - 波动率/量比计算
  lib/alerter.py          - L1/L2 阈值判断/消息构建
  lib/notifier.py         - QQ 推送/告警文件
  lib/database.py         - DB 操作/数据清理
  lib/process.py          - PID/信号/可中断 sleep
"""

import os
import sys
import time
from datetime import datetime
from pathlib import Path

# 锁定北京时区，确保交易日/时段判断不受服务器时区影响
os.environ['TZ'] = 'Asia/Shanghai'
if hasattr(time, 'tzset'):
    time.tzset()

# ============ 路径常量 ============
BASE_DIR = Path(os.environ.get("STOCK_MONITOR_HOME", Path(__file__).parent.resolve()))
PIDFILE = "/tmp/stock-monitor.pid"
CONFIG_FILE = BASE_DIR / "config.json"
LOG_FILE = BASE_DIR / "logs" / "monitor.log"
DB_FILE = BASE_DIR / "data" / "stock_monitor.db"
ALERTS_FILE = BASE_DIR / "logs" / "alerts.json"

# 将项目根目录加入 sys.path，确保 lib 可 import
sys.path.insert(0, str(BASE_DIR))

# ============ 初始化模块 ============
from lib.logger import init_logger, log
from lib.config import load_config, reload_config_if_changed, init_config_mtime, load_env_file
from lib.trading_calendar import is_trading_session, get_check_interval
from lib.data_fetcher import fetch_free_data, fetch_fund_flow, fetch_order_book
from lib.volatility import calculate_volatility
from lib.alerter import (should_trigger_l1, confirm_alert, reset_stock_tracker,
                         check_trading_signal, check_fund_flow_signal,
                         check_order_book_signal, check_divergence_signal)
from lib.notifier import (send_alert, send_trading_signal, send_feature_alert)
from lib.indicators import get_price_volume_history, calc_rsi
from lib.database import init_database, cleanup_old_data, DBWriter
import lib.process as process_mod
from lib.process import (check_and_write_pid, cleanup_pid,
                         register_signal_handlers, interruptible_sleep)


def main():
    # 加载 .env 环境变量（需在日志之前，避免敏感信息泄漏）
    load_env_file(BASE_DIR / ".env")

    # 单实例检查 + 写入 PID
    check_and_write_pid(PIDFILE)

    # 初始化日志
    init_logger(LOG_FILE)

    # 注册信号处理
    register_signal_handlers()

    # 加载配置
    config = load_config(CONFIG_FILE)
    init_config_mtime(CONFIG_FILE)

    # 初始化数据库
    init_database(DB_FILE)
    cleanup_old_data(DB_FILE)

    # 初始化数据库写入器
    db_writer = DBWriter(DB_FILE)

    try:
        db_writer.open()

        log("监控进程初始化完成，进入主循环")
        log(f"监控股票数：{len(config.get('stocks', []))}")

        last_heartbeat = None
        _stock_failures = {}  # {code: {'count': N, 'skip_until': timestamp}}

        # ============ 主循环 ============
        while process_mod.running:
            try:
                # 0. 检测配置变更
                config = reload_config_if_changed(CONFIG_FILE, config)

                # 1. 判断交易时段
                in_session, session_name = is_trading_session()

                if not in_session:
                    log(f"非交易时段 ({session_name})，休眠 5 分钟")
                    interruptible_sleep(300)
                    continue

                # 2. 遍历监控股票
                stocks = config.get('stocks', [])
                avg_volatility = 0
                stock_count = 0

                for stock in stocks:
                    if not stock.get('enabled', True):
                        continue

                    stock_code = stock['code']
                    stock_name = stock.get('name', stock_code)

                    # 连续失败降级：该股票处于冷却期则跳过
                    fail_info = _stock_failures.get(stock_code)
                    if fail_info and fail_info['skip_until'] > time.time():
                        continue

                    # 获取免费数据 (加间隔防限流)
                    interruptible_sleep(1.5)
                    stock_data = fetch_free_data(stock_code)
                    if not stock_data:
                        fi = _stock_failures.setdefault(stock_code, {'count': 0, 'skip_until': 0})
                        fi['count'] += 1
                        if fi['count'] >= 3:
                            skip = min(60 * (2 ** (fi['count'] - 3)), 3600)
                            fi['skip_until'] = time.time() + skip
                            log(f"{stock_name} 连续{fi['count']}次获取失败，冷却{skip}s", level="WARNING")
                        continue
                    _stock_failures.pop(stock_code, None)  # 成功则重置

                    # 计算波动指标
                    metrics = calculate_volatility(stock_data, db_path=DB_FILE)
                    if not metrics:
                        continue

                    avg_volatility += metrics['price_change_rate']
                    stock_count += 1

                    # 保存数据
                    db_writer.save_stock_data(stock_code, stock_name, stock_data, metrics)

                    # L1 判断
                    triggered_l1 = should_trigger_l1(metrics, config, stock_code)
                    if triggered_l1:
                        log(f"⚠️ {stock_name} 触发 L1 阈值: {triggered_l1}")

                        # L2 验证
                        triggered_l2 = confirm_alert(metrics, config, stock_code)
                        if triggered_l2:
                            log(f"🚨 {stock_name} 确认异动！L2 触发：{triggered_l2}")

                            # 推送告警
                            send_alert(stock, stock_data, metrics, config,
                                       triggered_l1, triggered_l2, BASE_DIR, ALERTS_FILE)

                            # 保存告警记录
                            db_writer.save_alert_record(stock, stock_data, metrics)

                            # 重置该股票追踪器
                            reset_stock_tracker(stock_code)
                        else:
                            log(f"{stock_name} L2 验证未通过")

                    # 技术指标检查（低频，不阻塞异常监控）
                    stock_config = stock.get('tech_analysis', {})
                    if stock_config.get('enabled', False):
                        signal = check_trading_signal(stock_code, stock_data.get("current_price"),
                                                       stock_config, config, DB_FILE)
                        if signal:
                            log(f"🔔 {stock_name} {signal['reason']}信号：MA{signal['ma_fast']}/MA{signal['ma_slow']}")
                            send_trading_signal(stock, signal, config,
                                               BASE_DIR, ALERTS_FILE)

                    # ============ 实盘辅助功能（各自独立，失败不阻断） ============
                    rt = config.get('real_time_features', {})

                    # 功能一：主力资金流向
                    try:
                        ff_cfg = rt.get('fund_flow', {})
                        if ff_cfg.get('enabled', False):
                            fund_flow = fetch_fund_flow(stock_code)
                            if fund_flow:
                                ff_signal = check_fund_flow_signal(
                                    stock_code, fund_flow, metrics, ff_cfg)
                                if ff_signal:
                                    log(f"💰 {stock_name} 资金信号：{ff_signal['reason']}")
                                    send_feature_alert(stock, ff_signal, config,
                                                       BASE_DIR, ALERTS_FILE)
                    except Exception as e:
                        log(f"主力资金流功能异常 ({stock_name}): {e}", level="ERROR")

                    # 功能二：五档盘口
                    try:
                        ob_cfg = rt.get('order_book', {})
                        if ob_cfg.get('enabled', False):
                            order_book = fetch_order_book(stock_code)
                            if order_book:
                                ob_signal = check_order_book_signal(
                                    stock_code, order_book, ob_cfg)
                                if ob_signal:
                                    log(f"🛒 {stock_name} 盘口信号：{ob_signal['reason']}")
                                    send_feature_alert(stock, ob_signal, config,
                                                       BASE_DIR, ALERTS_FILE)
                    except Exception as e:
                        log(f"五档盘口功能异常 ({stock_name}): {e}", level="ERROR")

                    # 功能八：量价背离
                    try:
                        dv_cfg = rt.get('divergence', {})
                        if dv_cfg.get('enabled', False):
                            hist = get_price_volume_history(DB_FILE, stock_code,
                                                            dv_cfg.get('window', 5) + 6)
                            if hist:
                                # 可选 RSI 辅助判断
                                rsi = None
                                dv_rsi_cfg = stock.get('tech_analysis', {})
                                closes = [h['price'] for h in hist]
                                if dv_rsi_cfg.get('enabled', False):
                                    rsi = calc_rsi(closes, dv_rsi_cfg.get('rsi_period', 14))
                                dv_signal = check_divergence_signal(
                                    stock_code, hist, dv_cfg, rsi)
                                if dv_signal:
                                    log(f"🔀 {stock_name} 背离信号：{dv_signal['reason']}")
                                    send_feature_alert(stock, dv_signal, config,
                                                       BASE_DIR, ALERTS_FILE)
                    except Exception as e:
                        log(f"量价背离功能异常 ({stock_name}): {e}", level="ERROR")

                # 3. 提交本轮写入
                db_writer.flush()

                # 4. 动态调整间隔
                if stock_count > 0:
                    avg_volatility /= stock_count
                interval = get_check_interval(session_name, avg_volatility)

                # 5. 心跳日志 (每 5 分钟)
                now = datetime.now()
                if last_heartbeat is None or (now - last_heartbeat).total_seconds() >= 300:
                    log(f"💓 心跳 - 时段:{session_name}, 间隔:{interval}秒")
                    last_heartbeat = now

                # 6. 休眠
                interruptible_sleep(interval)

            except KeyboardInterrupt:
                log("收到键盘中断，退出主循环")
                break
            except Exception as e:
                log(f"主循环异常：{e}", level="ERROR")
                interruptible_sleep(5)

    finally:
        try:
            db_writer.flush()
        except Exception:
            pass
        db_writer.close()
        cleanup_pid(PIDFILE)
        log("进程已退出")


if __name__ == "__main__":
    main()
