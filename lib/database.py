#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库模块 - DBWriter/初始化/数据清理/历史均量查询
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from lib.logger import log

# 数据保留天数
DATA_RETENTION_DAYS = 30


def init_database(db_path: Path):
    """初始化 SQLite 数据库（建表 + 索引）"""
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(db_path)) as conn:
            cursor = conn.cursor()

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS stock_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT,
                    timestamp TEXT NOT NULL,
                    current_price REAL,
                    open_price REAL,
                    close_price REAL,
                    high_price REAL,
                    low_price REAL,
                    volume REAL,
                    price_change_pct REAL,
                    volume_ratio REAL
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT,
                    alert_time TEXT NOT NULL,
                    current_price REAL,
                    change_pct REAL,
                    volume_ratio REAL,
                    alert_type TEXT,
                    verified INTEGER,
                    notified INTEGER
                )
            ''')

            cursor.execute('CREATE INDEX IF NOT EXISTS idx_stock_data_code_time ON stock_data(stock_code, timestamp)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_alerts_code_time ON alerts(stock_code, alert_time)')

            conn.commit()
        log(f"数据库初始化成功：{db_path}")
    except Exception as e:
        log(f"数据库初始化失败：{e}", level="ERROR")


def get_avg_volume_from_db(db_path: Path, stock_code: str, days: int = 5) -> float:
    """从数据库查询历史日均成交量（按天聚合，使用北京时间）"""
    try:
        with sqlite3.connect(str(db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT DATE(timestamp) AS trade_date, MAX(volume) AS day_volume
                FROM stock_data
                WHERE stock_code = ? AND volume IS NOT NULL
                    AND timestamp >= datetime('now', 'localtime', ?)
                GROUP BY DATE(timestamp)
                ORDER BY trade_date DESC
                LIMIT ?
            ''', (stock_code, f'-{days + 1} days', days))
            rows = cursor.fetchall()
            if rows:
                avg = sum(r[1] for r in rows if r[1] is not None) / len(rows)
                if avg > 100000:
                    return avg
    except Exception as e:
        log(f"查询历史均量失败 ({stock_code}): {e}", level="WARNING")
    return 0


def cleanup_old_data(db_path: Path):
    """清理过期数据，防止数据库无限增长"""
    try:
        with sqlite3.connect(str(db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM stock_data WHERE timestamp < datetime('now', 'localtime', ?)",
                (f'-{DATA_RETENTION_DAYS} days',)
            )
            stock_deleted = cursor.rowcount
            cursor.execute(
                "DELETE FROM alerts WHERE alert_time < datetime('now', 'localtime', ?)",
                (f'-{DATA_RETENTION_DAYS} days',)
            )
            alert_deleted = cursor.rowcount
            conn.commit()
            if stock_deleted > 0 or alert_deleted > 0:
                conn.execute("VACUUM")
                log(f"数据清理完成：stock_data 删除 {stock_deleted} 条，alerts 删除 {alert_deleted} 条")
    except Exception as e:
        log(f"数据清理失败：{e}", level="WARNING")


class DBWriter:
    """数据库批量写入器，复用连接，主循环一轮结束后统一 commit"""

    def __init__(self, db_path: Path):
        self.db_path = str(db_path)
        self._conn = None

    def open(self):
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")

    def close(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def save_stock_data(self, stock_code: str, stock_name: str, stock_data: dict, metrics: dict):
        """保存股票数据（写入但不 commit，等 flush）"""
        if not self._conn:
            return
        try:
            self._conn.execute('''
                INSERT INTO stock_data 
                (stock_code, stock_name, timestamp, current_price, open_price, close_price, 
                 high_price, low_price, volume, price_change_pct, volume_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                stock_code, stock_name, stock_data['timestamp'],
                stock_data['current_price'], stock_data['open_price'], stock_data['close_price'],
                stock_data['high_price'], stock_data['low_price'], stock_data['volume'],
                metrics['price_change_pct'], metrics.get('volume_ratio')
            ))
        except Exception as e:
            log(f"保存股票数据失败：{e}", level="WARNING")

    def save_alert_record(self, stock: dict, stock_data: dict, metrics: dict):
        """保存告警记录（写入但不 commit，等 flush）"""
        if not self._conn:
            return
        try:
            self._conn.execute('''
                INSERT INTO alerts 
                (stock_code, stock_name, alert_time, current_price, change_pct, 
                 volume_ratio, alert_type, verified, notified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                stock['code'], stock['name'], datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                stock_data['current_price'], metrics['price_change_pct'],
                metrics.get('volume_ratio'), 'price_spike', 1, 1
            ))
        except Exception as e:
            log(f"保存告警记录失败：{e}", level="WARNING")

    def flush(self):
        """提交本轮所有写入"""
        if self._conn:
            try:
                self._conn.commit()
            except Exception as e:
                log(f"数据库提交失败：{e}", level="ERROR")
