# -*- coding: utf-8 -*-
"""
tracker.py — 次日多时点快照模块

职责：
  1. 拉取自选股票的实时行情并写入 tracking_snapshots
  2. 聚合快照数据供 GUI 展示
  3. 计算次日各时点相对加入价的涨跌幅（用于统计）
"""

import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import akshare as ak

import db


def _safe_float(v, default=None):
    if v is None:
        return default
    try:
        return float(v)
    except Exception:
        return default


# ── 实时快照拉取 ──────────────────────────────────────────────────

def _fetch_realtime_batch(codes: list) -> dict:
    """
    批量拉取若干股票的实时行情，返回 {code: {...}} dict。
    使用东方财富 push2 ulist.np 接口。
    """
    import requests
    if not codes:
        return {}

    # 构建 secid 列表
    secids = []
    for code in codes:
        c = str(code).strip().zfill(6)
        if c.startswith(("600", "601", "603", "605", "688", "689")):
            secids.append(f"1.{c}")
        else:
            secids.append(f"0.{c}")

    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://quote.eastmoney.com/center/gridlist.html",
    }
    fields = "f12,f2,f3,f15,f16,f5,f10"
    # f12=代码 f2=最新价 f3=涨跌幅 f15=最高 f16=最低 f5=成交量 f10=量比

    result = {}
    batch_size = 200
    for i in range(0, len(secids), batch_size):
        batch = secids[i: i + batch_size]
        params = {
            "secids": ",".join(batch),
            "fields": fields,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
        }
        try:
            r = requests.get(url, params=params, headers=headers, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json()
            diffs = (data.get("data") or {}).get("diff") or []
            for item in diffs:
                code = str(item.get("f12", "")).zfill(6)
                result[code] = {
                    "price":        _safe_float(item.get("f2")),
                    "pct_chg":      _safe_float(item.get("f3")),
                    "high":         _safe_float(item.get("f15")),
                    "low":          _safe_float(item.get("f16")),
                    "volume":       _safe_float(item.get("f5")),
                    "volume_ratio": _safe_float(item.get("f10")),
                }
        except Exception:
            pass
        time.sleep(0.3)

    return result


def take_snapshot(watch_date: str, label: str, progress_cb=None) -> int:
    """
    对 watch_date 当天自选的所有股票拍一张快照，写入数据库。

    Args:
        watch_date: 自选日期 YYYY-MM-DD
        label:      快照标签，如 "9:35" / "10:30" / "14:30" / "close"
        progress_cb: 进度回调 (msg: str)

    Returns:
        成功写入的快照数量
    """
    items = db.get_watchlist_by_date(watch_date)
    if not items:
        if progress_cb:
            progress_cb(f"自选列表 {watch_date} 为空，无需快照")
        return 0

    codes = [item["code"] for item in items]
    if progress_cb:
        progress_cb(f"正在拉取 {len(codes)} 只股票实时行情...")

    realtime = _fetch_realtime_batch(codes)

    now = datetime.now().isoformat(timespec="seconds")
    records = []
    for code in codes:
        data = realtime.get(code, {})
        records.append({
            "code":         code,
            "watch_date":   watch_date,
            "label":        label,
            "snapshot_time": now,
            "price":        data.get("price"),
            "pct_chg":      data.get("pct_chg"),
            "high":         data.get("high"),
            "low":          data.get("low"),
            "volume":       data.get("volume"),
            "volume_ratio": data.get("volume_ratio"),
        })

    db.save_snapshots_batch(records)
    if progress_cb:
        progress_cb(f"快照 [{label}] 已保存 {len(records)} 条，时间：{now}")
    return len(records)


# ── 聚合展示数据 ──────────────────────────────────────────────────

def get_tracking_table(watch_date: str) -> pd.DataFrame:
    """
    返回次日跟踪综合表：每行=一只股票，每列快照=一个时点的涨跌幅。
    涨跌幅以加入时价格（add_price）为基准计算。

    列：code, name, add_price, add_pct_chg, <label1>, <label2>, ...
    其中 <label> 列值为相对 add_price 的百分比变动（%），None 表示无快照。
    """
    watchlist = db.get_watchlist_by_date(watch_date)
    if not watchlist:
        return pd.DataFrame()

    snapshots = db.get_snapshots_by_date(watch_date)
    labels = db.get_snapshot_labels(watch_date)

    # snapshots 按 code + label 聚合
    snap_map: dict = {}   # (code, label) -> row
    for s in snapshots:
        snap_map[(s["code"], s["label"])] = s

    rows = []
    for w in watchlist:
        code = w["code"]
        add_price = _safe_float(w.get("add_price"))
        row = {
            "id":           w["id"],
            "code":         code,
            "name":         w.get("name", ""),
            "add_price":    add_price,
            "add_pct_chg":  _safe_float(w.get("add_pct_chg")),
        }
        for label in labels:
            snap = snap_map.get((code, label))
            if snap and snap.get("price") is not None and add_price:
                snap_price = _safe_float(snap["price"])
                row[label] = round((snap_price - add_price) / add_price * 100, 2) if snap_price else None
                row[f"{label}_price"] = snap_price
            else:
                row[label] = None
                row[f"{label}_price"] = None
        rows.append(row)

    return pd.DataFrame(rows)


def get_summary(watch_date: str, label: str = None) -> dict:
    """
    返回某日（可指定时点）跟踪汇总统计。

    Returns: {
        "total": int,
        "up": int,        # 涨幅 > 0
        "down": int,      # 涨幅 <= 0
        "avg_pct": float, # 平均涨跌幅 (%)
        "label": str,
    }
    """
    table = get_tracking_table(watch_date)
    if table.empty:
        return {"total": 0, "up": 0, "down": 0, "avg_pct": None, "label": label or "-"}

    # 如果没指定 label，取最新（最后一列快照）
    labels = db.get_snapshot_labels(watch_date)
    if not labels:
        return {"total": len(table), "up": 0, "down": 0, "avg_pct": None, "label": "-"}

    use_label = label if (label and label in labels) else labels[-1]
    if use_label not in table.columns:
        return {"total": len(table), "up": 0, "down": 0, "avg_pct": None, "label": use_label}

    pcts = table[use_label].dropna()
    total = len(table)
    up = int((pcts > 0).sum())
    down = int((pcts <= 0).sum())
    avg_pct = round(float(pcts.mean()), 2) if not pcts.empty else None
    return {"total": total, "up": up, "down": down, "avg_pct": avg_pct, "label": use_label}
