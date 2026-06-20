# -*- coding: utf-8 -*-
"""
watchlist_manager.py — 自选列表业务逻辑

负责将筛选结果（含或不含技术指标）转化为 watchlist 数据库记录。
"""

from datetime import datetime
import db
import indicators as ind


def _make_item(row: dict, session_id: int = None) -> dict:
    """将筛选结果 row 转为 watchlist 表的 dict。"""
    today = datetime.now().strftime("%Y-%m-%d")
    now_t = datetime.now().strftime("%H:%M:%S")
    return {
        "session_id":    session_id,
        "code":          row.get("code"),
        "name":          row.get("name"),
        "add_date":      today,
        "add_time":      now_t,
        "add_price":     row.get("price"),
        "add_pct_chg":   row.get("pct_chg"),
        "volume_ratio":  row.get("volume_ratio"),
        "turnover":      row.get("turnover"),
        "market_cap":    row.get("market_cap"),
        "pe":            row.get("pe"),
        "pb":            row.get("pb"),
        "eps":           row.get("eps"),
        "revenue":       row.get("revenue"),
        "net_profit":    row.get("net_profit"),
        "revenue_yoy":   row.get("revenue_yoy"),
        "profit_yoy":    row.get("profit_yoy"),
        "roe":           row.get("roe"),
        "ma5":           row.get("ma5"),
        "ma10":          row.get("ma10"),
        "ma20":          row.get("ma20"),
        "above_ma5":     row.get("above_ma5"),
        "above_ma10":    row.get("above_ma10"),
        "above_ma20":    row.get("above_ma20"),
        "ma5_trend":     row.get("ma5_trend"),
        "tail_30min_pct": row.get("tail_30min_pct"),
        "day5_high":     row.get("day5_high"),
        "day5_low":      row.get("day5_low"),
        "today_high":    row.get("today_high"),
        "today_low":     row.get("today_low"),
        "industry":      row.get("industry"),
        "report_date":   row.get("report_date"),
        "note":          "",
    }


def add_rows_to_watchlist(rows: list, session_id: int = None,
                          enrich: bool = False, fetch_tail: bool = True,
                          fetch_roe: bool = False, progress_cb=None) -> int:
    """
    将筛选结果批量加入自选列表。

    Args:
        rows:       筛选 result rows（list of dict）
        session_id: 关联的筛选会话 id
        enrich:     是否在加入前自动补充技术指标（调用 indicators 模块）
        fetch_tail: enrich=True 时是否获取尾盘30min涨跌幅
        fetch_roe:  enrich=True 时是否获取 ROE
        progress_cb: 进度回调 (current, total, code)

    Returns:
        实际插入数量
    """
    if not rows:
        return 0

    if enrich:
        rows = ind.enrich_indicators(
            rows, fetch_tail=fetch_tail, fetch_roe=fetch_roe,
            progress_cb=progress_cb
        )

    items = [_make_item(r, session_id) for r in rows]
    inserted = db.add_watchlist_batch(items)
    return len(inserted)


# ── 查询 ──────────────────────────────────────────────────────────

def get_dates() -> list:
    """返回自选记录的所有日期（降序）。"""
    return db.get_watchlist_dates()


def get_by_date(date_str: str) -> list:
    """返回指定日期的自选列表。"""
    return db.get_watchlist_by_date(date_str)


def remove(item_id: int):
    """删除单条自选记录。"""
    db.remove_watchlist_item(item_id)


def add_manual(code: str, name: str = "") -> bool:
    """手动添加单只股票到今日自选。返回是否成功插入（False=已存在）。"""
    item_id = db.add_watchlist_manual(code, name)
    return item_id > 0
