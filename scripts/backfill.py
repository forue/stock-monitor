#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backfill.py — 股票监控数据回填工具（补录漏采的行情数据）

用途：当监控进程停摆/漏采导致某交易日数据缺失时，从腾讯行情接口回填。

数据源（腾讯，均可用）：
  - 分时接口  web.ifzq.gtimg.cn/appstock/app/minute/query  → 仅返回【最近一个交易日】的分时
  - 日K接口   web.ifzq.gtimg.cn/appstock/app/fqkline/get   → 支持【任意历史日期】的 OHLC+量
  - 详情接口  qt.gtimg.cn                                  → 昨收/今开/最高/最低/当前/时间戳

两种模式：
  --mode minute [--date YYYY-MM-DD]   分时回填（仅能填最近交易日；--date 省略则自动取）
  --mode daily  --date YYYY-MM-DD     日K回填（任意历史日期，每只1条收盘快照）

用法示例：
  # 回填最近交易日全量分时（13只×267条）
  python3 scripts/backfill.py --mode minute

  # 回填指定历史日期日K（13只×1条）
  python3 scripts/backfill.py --mode daily --date 2026-10-28

  # 干跑（不写库，只打印预览）
  python3 scripts/backfill.py --mode minute --dry-run

依赖：requests, sqlite3（标准库+requests）
"""
import sqlite3
import sys
import re
import os
import time
import argparse
import requests

# ============ 路径与配置 ============
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STOCK_DIR = os.path.dirname(SCRIPT_DIR)          # stock-monitor/
DB_PATH = os.path.join(STOCK_DIR, "data", "stock_monitor.db")
CONFIG_PATH = os.path.join(STOCK_DIR, "config.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}


# ============ 股票列表（优先从 config.json 读取，失败则用内置） ============
FALLBACK_STOCKS = [
    ("600104", "sh", "上汽集团"), ("002475", "sz", "立讯精密"),
    ("600519", "sh", "贵州茅台"), ("002402", "sz", "和而泰"),
    ("601611", "sh", "中国核建"), ("601012", "sh", "隆基绿能"),
    ("600028", "sh", "中国石化"), ("002736", "sz", "国信证券"),
    ("601890", "sh", "亚星锚链"), ("600150", "sh", "中国船舶"),
    ("001270", "sz", "铖昌科技"), ("603738", "sh", "泰晶科技"),
    ("603773", "sh", "沃格光电"),
]


def load_stocks():
    """从 config.json 加载监控股票，失败则用内置列表"""
    try:
        import json
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        stocks = []
        for s in cfg.get("stocks", []):
            code = str(s.get("code", "")).strip()
            name = s.get("name", "")
            if not code:
                continue
            # 判断交易所前缀
            prefix = "sh" if code.startswith(("6", "9")) else "sz"
            stocks.append((code, prefix, name))
        if stocks:
            return stocks
    except Exception as e:
        print(f"[WARN] config.json 读取失败，使用内置列表: {e}")
    return FALLBACK_STOCKS


# ============ 数据获取 ============
def get_qt_detail(symbol):
    """个股详情: 昨收/今开/最高/最低/当前/时间戳。失败返回 None"""
    url = f"https://qt.gtimg.cn/q={symbol}"
    for _ in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            r.encoding = "gbk"
            m = re.search(r'="(.*)"', r.text)
            if m:
                f = m.group(1).split("~")
                if len(f) > 34:
                    return {
                        "name": f[1], "price": float(f[3]), "prev_close": float(f[4]),
                        "open": float(f[5]), "high": float(f[33]), "low": float(f[34]),
                        "timestamp": f[30] if len(f) > 30 else "",
                    }
            return None
        except Exception:
            time.sleep(1)
    return None


def get_minute_data(symbol):
    """分时数据，仅返回最近交易日。返回 (list_of_lines, date_str, error)"""
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={symbol}"
    for _ in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            d = r.json()
            data = d.get("data", {}).get(symbol, {})
            mins = data.get("data", {}).get("data", [])
            qt = data.get("qt", {}).get(symbol, [])
            ts = qt[30] if len(qt) > 30 else ""
            if mins and ts:
                return mins, ts, None
            return [], "", f"无数据 {str(d)[:150]}"
        except Exception:
            time.sleep(1)
    return [], "", "请求失败"


def get_daily_kline(symbol, start, end):
    """日K线数据，支持任意历史日期范围。返回 (list_of_rows, error)
    row: [日期, 开, 收, 高, 低, 量(手)]  (qfq 前复权)
    """
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,{start},{end},100,qfq"
    for _ in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            d = r.json()
            data = d.get("data", {}).get(symbol, {})
            klines = data.get("day") or data.get("qfqday") or []
            if klines:
                return klines, None
            return [], f"无日K数据 {str(d)[:150]}"
        except Exception as e:
            time.sleep(1)
    return [], "请求失败"


# ============ 模式1：分时回填 ============
def backfill_minute(conn, stocks, target_date, dry_run):
    """分时回填：仅能填最近交易日"""
    results = []
    for code, prefix, name in stocks:
        symbol = f"{prefix}{code}"
        detail = get_qt_detail(symbol)
        if not detail:
            print(f"[FAIL] {code} {name}: 详情获取失败")
            results.append({"code": code, "ok": False})
            continue

        ts = detail["timestamp"]  # 20261029161457
        ts_date = ts[0:8] if len(ts) >= 8 else ""
        ts_date_fmt = f"{ts_date[0:4]}-{ts_date[4:6]}-{ts_date[6:8]}" if len(ts_date) == 8 else ""
        if target_date and ts_date_fmt != target_date:
            print(f"[SKIP] {code} {name}: 数据日期 {ts_date_fmt} 非目标 {target_date}（分时接口仅返回最近交易日）")
            results.append({"code": code, "ok": False, "skip": True})
            continue

        mins, _, err = get_minute_data(symbol)
        if not mins:
            print(f"[FAIL] {code} {name}: 分时获取失败 {err}")
            results.append({"code": code, "ok": False})
            continue

        prev_close = detail["prev_close"]
        day_open, day_high = detail["open"], detail["high"]
        day_low, day_close = detail["low"], detail["price"]

        rows = []
        prev_cum = 0.0
        for m in mins:
            if isinstance(m, str):
                parts = m.split()
                if len(parts) < 3:
                    continue
                t, price, cum_vol = parts[0], float(parts[1]), float(parts[2])
            else:
                t, price, cum_vol = m[0], float(m[1]), float(m[2])
            minute_vol = cum_vol - prev_cum
            prev_cum = cum_vol
            pct = (price - prev_close) / prev_close * 100 if prev_close else 0.0
            hh, mm = t[0:2], t[2:4]
            ss = t[4:6] if len(t) >= 6 else "00"
            ts_full = f"{ts_date_fmt} {hh}:{mm}:{ss}"
            rows.append((code, name, ts_full, price, day_open, day_close,
                         day_high, day_low, minute_vol, round(pct, 2), None))

        if dry_run:
            print(f"[DRY] {code} {name}: {len(rows)} 条 | O{day_open}/H{day_high}/L{day_low}/C{day_close} | 昨收{prev_close}")
            results.append({"code": code, "ok": True, "dry": True})
            continue

        conn.execute("DELETE FROM stock_data WHERE stock_code=? AND timestamp LIKE ?", (code, ts_date_fmt + "%"))
        conn.executemany(
            "INSERT INTO stock_data (stock_code, stock_name, timestamp, current_price, open_price, close_price, high_price, low_price, volume, price_change_pct, volume_ratio) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        print(f"[OK] {code} {name}: 写入 {len(rows)} 条 | O{day_open}/H{day_high}/L{day_low}/C{day_close} | 昨收{prev_close}")
        results.append({"code": code, "ok": True})
    return results


# ============ 模式2：日K回填 ============
def backfill_daily(conn, stocks, target_date, dry_run):
    """日K回填：任意历史日期，每只写入1条收盘快照"""
    if not target_date:
        print("[ERROR] --mode daily 需要指定 --date YYYY-MM-DD")
        return []
    results = []
    for code, prefix, name in stocks:
        symbol = f"{prefix}{code}"
        klines, err = get_daily_kline(symbol, target_date, target_date)
        if err or not klines:
            print(f"[FAIL] {code} {name}: 日K获取失败 {err}")
            results.append({"code": code, "ok": False})
            continue
        # 找目标日期那一根
        target_row = None
        for k in klines:
            if k[0] == target_date:
                target_row = k
                break
        if not target_row:
            print(f"[SKIP] {code} {name}: 目标日期 {target_date} 无K线（可能非交易日）")
            results.append({"code": code, "ok": False, "skip": True})
            continue

        date_s, open_p, close_p, high_p, low_p, vol = (
            target_row[0], float(target_row[1]), float(target_row[2]),
            float(target_row[3]), float(target_row[4]), float(target_row[5])
        )
        pct = None  # 日K不含涨跌幅，可后续补

        if dry_run:
            print(f"[DRY] {code} {name}: 1 条 | O{open_p}/H{high_p}/L{low_p}/C{close_p} | 量{vol}")
            results.append({"code": code, "ok": True, "dry": True})
            continue

        conn.execute("DELETE FROM stock_data WHERE stock_code=? AND timestamp LIKE ?", (code, target_date + "%"))
        conn.execute(
            "INSERT INTO stock_data (stock_code, stock_name, timestamp, current_price, open_price, close_price, high_price, low_price, volume, price_change_pct, volume_ratio) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (code, name, f"{date_s} 15:00:00", close_p, open_p, close_p, high_p, low_p, vol, pct, None),
        )
        conn.commit()
        print(f"[OK] {code} {name}: 写入 1 条 | O{open_p}/H{high_p}/L{low_p}/C{close_p} | 量{vol}")
        results.append({"code": code, "ok": True})
    return results


# ============ main ============
def main():
    parser = argparse.ArgumentParser(description="股票监控数据回填工具")
    parser.add_argument("--mode", choices=["minute", "daily"], default="minute",
                        help="minute=分时回填(最近交易日) daily=日K回填(任意历史日期)")
    parser.add_argument("--date", default=None, help="目标日期 YYYY-MM-DD（daily 必填；minute 可选校验）")
    parser.add_argument("--dry-run", action="store_true", help="干跑，不写库")
    args = parser.parse_args()

    stocks = load_stocks()
    print(f"加载监控股票: {len(stocks)} 只\n")

    conn = sqlite3.connect(DB_PATH)
    try:
        if args.mode == "minute":
            print(f"=== 分时回填模式（仅最近交易日）===")
            results = backfill_minute(conn, stocks, args.date, args.dry_run)
        else:
            print(f"=== 日K回填模式 target={args.date} ===")
            results = backfill_daily(conn, stocks, args.date, args.dry_run)
    finally:
        conn.close()

    ok = sum(1 for r in results if r.get("ok"))
    total = len(results)
    print(f"\n=== {'干跑' if args.dry_run else '写入'}完成: {ok}/{total} 成功 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
