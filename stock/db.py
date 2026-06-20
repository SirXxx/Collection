# -*- coding: utf-8 -*-
"""
db.py — SQLite 数据库管理（股票筛选器持久化层）

表结构：
  screener_sessions  — 每次筛选会话记录
  watchlist          — 自选股票（含筛选时所有字段）
  tracking_snapshots — 次日多时点快照
"""

import sqlite3
import os
import json
from contextlib import contextmanager
from datetime import datetime

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_selector.db")

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS screener_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT    NOT NULL,
    params_json TEXT,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS watchlist (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER REFERENCES screener_sessions(id) ON DELETE SET NULL,
    code            TEXT    NOT NULL,
    name            TEXT,
    add_date        TEXT    NOT NULL,   -- YYYY-MM-DD
    add_time        TEXT,               -- HH:MM:SS
    add_price       REAL,
    add_pct_chg     REAL,
    volume_ratio    REAL,
    turnover        REAL,
    market_cap      REAL,
    pe              REAL,
    pb              REAL,
    eps             REAL,
    revenue         REAL,
    net_profit      REAL,
    revenue_yoy     REAL,
    profit_yoy      REAL,
    roe             REAL,
    ma5             REAL,
    ma10            REAL,
    ma20            REAL,
    above_ma5       INTEGER,            -- 1=above, 0=below, NULL=unknown
    above_ma10      INTEGER,
    above_ma20      INTEGER,
    ma5_trend       TEXT,               -- up/down/flat
    tail_30min_pct  REAL,
    day5_high       REAL,
    day5_low        REAL,
    today_high      REAL,
    today_low       REAL,
    industry        TEXT,
    report_date     TEXT,
    note            TEXT
);

CREATE INDEX IF NOT EXISTS idx_wl_date ON watchlist(add_date);
CREATE INDEX IF NOT EXISTS idx_wl_code ON watchlist(code);

CREATE TABLE IF NOT EXISTS tracking_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    code           TEXT NOT NULL,
    watch_date     TEXT NOT NULL,       -- YYYY-MM-DD  (与 watchlist.add_date 对应)
    snapshot_time  TEXT NOT NULL,       -- ISO datetime
    label          TEXT,                -- "9:35" / "10:30" / "close" 等
    price          REAL,
    pct_chg        REAL,                -- 相对昨收涨跌幅（东方财富实时字段）
    high           REAL,
    low            REAL,
    volume         REAL,
    volume_ratio   REAL
);

CREATE INDEX IF NOT EXISTS idx_snap_date ON tracking_snapshots(watch_date, code);
"""

# ── watchlist 所有可存字段 ──────────────────────────────────────────
WATCHLIST_FIELDS = [
    "session_id", "code", "name", "add_date", "add_time",
    "add_price", "add_pct_chg", "volume_ratio", "turnover", "market_cap",
    "pe", "pb", "eps", "revenue", "net_profit", "revenue_yoy", "profit_yoy",
    "roe", "ma5", "ma10", "ma20", "above_ma5", "above_ma10", "above_ma20",
    "ma5_trend", "tail_30min_pct", "day5_high", "day5_low",
    "today_high", "today_low", "industry", "report_date", "note",
]


@contextmanager
def get_conn():
    """线程安全连接（每次调用创建新连接，WAL 模式支持并发读）。"""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """初始化数据库，创建表和索引（幂等操作）。"""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()


# ── screener_sessions ─────────────────────────────────────────────

def create_session(params: dict, note: str = "") -> int:
    """新建筛选会话，返回会话 id。"""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO screener_sessions(created_at, params_json, note) VALUES (?,?,?)",
            (datetime.now().isoformat(timespec="seconds"),
             json.dumps(params, ensure_ascii=False), note)
        )
        return cur.lastrowid


def list_sessions(limit: int = 100) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, created_at, note FROM screener_sessions ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── watchlist ─────────────────────────────────────────────────────

def add_watchlist_batch(items: list) -> list:
    """批量插入自选，跳过同一代码同一日期的重复项。返回插入的 id 列表。"""
    inserted = []
    with get_conn() as conn:
        for item in items:
            code = item.get("code", "")
            date = item.get("add_date", "")
            if not code or not date:
                continue
            existing = conn.execute(
                "SELECT id FROM watchlist WHERE code=? AND add_date=?",
                (code, date)
            ).fetchone()
            if existing:
                continue
            keys = [f for f in WATCHLIST_FIELDS if f in item and item[f] is not None]
            if not keys:
                continue
            placeholders = ",".join(["?"] * len(keys))
            col_names = ",".join(keys)
            values = [item[k] for k in keys]
            cur = conn.execute(
                f"INSERT INTO watchlist({col_names}) VALUES ({placeholders})",
                values
            )
            inserted.append(cur.lastrowid)
    return inserted


def get_watchlist_dates() -> list:
    """返回所有有记录的日期（降序）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT add_date FROM watchlist ORDER BY add_date DESC"
        ).fetchall()
    return [r["add_date"] for r in rows]


def get_watchlist_by_date(date_str: str) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM watchlist WHERE add_date=? ORDER BY add_pct_chg DESC",
            (date_str,)
        ).fetchall()
    return [dict(r) for r in rows]


def remove_watchlist_item(item_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM watchlist WHERE id=?", (item_id,))


def update_watchlist_note(item_id: int, note: str):
    with get_conn() as conn:
        conn.execute("UPDATE watchlist SET note=? WHERE id=?", (note, item_id))


def add_watchlist_manual(code: str, name: str = "", note: str = "") -> int:
    """手动添加单只股票到今日自选（不含技术指标）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    now_t = datetime.now().strftime("%H:%M:%S")
    item = {"code": code, "name": name, "add_date": today, "add_time": now_t, "note": note}
    ids = add_watchlist_batch([item])
    return ids[0] if ids else -1


# ── tracking_snapshots ────────────────────────────────────────────

def save_snapshots_batch(records: list):
    """批量保存快照，同 code+watch_date+label 的旧快照会被替换。"""
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        for r in records:
            conn.execute(
                "DELETE FROM tracking_snapshots WHERE code=? AND watch_date=? AND label=?",
                (r["code"], r["watch_date"], r["label"])
            )
            conn.execute(
                """INSERT INTO tracking_snapshots
                   (code, watch_date, snapshot_time, label,
                    price, pct_chg, high, low, volume, volume_ratio)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (r["code"], r["watch_date"], r.get("snapshot_time", now), r["label"],
                 r.get("price"), r.get("pct_chg"), r.get("high"),
                 r.get("low"), r.get("volume"), r.get("volume_ratio"))
            )


def get_snapshots_by_date(watch_date: str) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM tracking_snapshots
               WHERE watch_date=? ORDER BY code, snapshot_time""",
            (watch_date,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_snapshot_labels(watch_date: str) -> list:
    """返回某日已有的快照标签列表（按时间顺序）。"""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT DISTINCT label FROM tracking_snapshots
               WHERE watch_date=? ORDER BY snapshot_time""",
            (watch_date,)
        ).fetchall()
    return [r["label"] for r in rows]


def get_tracking_dates() -> list:
    """返回所有有快照的日期（降序）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT watch_date FROM tracking_snapshots ORDER BY watch_date DESC"
        ).fetchall()
    return [r["watch_date"] for r in rows]


# ── 模块初始化 ────────────────────────────────────────────────────
init_db()
