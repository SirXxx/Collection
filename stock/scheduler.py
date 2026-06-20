# -*- coding: utf-8 -*-
"""
scheduler.py — 自动定时快照调度模块

使用 APScheduler（BackgroundScheduler）在交易日的指定时间点
自动拍取前一天自选列表的实时快照。

配置文件：stock/scheduler_config.json
  {
    "enabled": true,
    "times": ["09:35", "10:30", "13:30", "14:30", "15:00"],
    "watch_date": ""     // 留空则自动取最新自选日期
  }

GUI 通过 start() / stop() 控制，通过 is_running() 查询状态。
"""

import json
import os
from datetime import datetime, date

SCHEDULER_CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "scheduler_config.json"
)

DEFAULT_CONFIG = {
    "enabled": False,
    "times": ["09:35", "10:30", "13:30", "14:30", "15:00"],
    "watch_date": "",
}

# APScheduler 可选导入（未安装时调度功能不可用）
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    _APScheduler = BackgroundScheduler
    _APSCHEDULER_AVAILABLE = True
except ImportError:
    _APScheduler = None
    _APSCHEDULER_AVAILABLE = False

_scheduler_instance = None
_snapshot_callback = None   # 外部注入：(watch_date, label) -> None


# ── 配置 ──────────────────────────────────────────────────────────

def load_config() -> dict:
    if not os.path.exists(SCHEDULER_CONFIG_FILE):
        return dict(DEFAULT_CONFIG)
    try:
        with open(SCHEDULER_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(data)
        return cfg
    except Exception:
        return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    with open(SCHEDULER_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ── 交易日判断 ────────────────────────────────────────────────────

def is_trading_day(d: date = None) -> bool:
    """
    简单判断：周一至周五视为交易日（不含节假日）。
    若需精确排除节假日，可集成 trading_calendars 或 chinese_calendar 包。
    """
    if d is None:
        d = date.today()
    return d.weekday() < 5   # 0=Mon … 4=Fri


def is_trading_hours() -> bool:
    """判断当前是否在 A 股交易时间内（9:25 - 15:05，简单判断）。"""
    now = datetime.now().time()
    from datetime import time as dt_time
    return dt_time(9, 25) <= now <= dt_time(15, 5)


# ── 调度核心 ──────────────────────────────────────────────────────

def _make_label(time_str: str) -> str:
    """将 'HH:MM' 转为快照标签，如 '09:35' → '9:35'。"""
    parts = time_str.split(":")
    return f"{int(parts[0])}:{parts[1]}"


def _scheduled_job(time_str: str):
    """定时任务执行体，由 APScheduler 调用。"""
    if not is_trading_day():
        return
    if _snapshot_callback is None:
        return

    cfg = load_config()
    watch_date = cfg.get("watch_date", "").strip()
    if not watch_date:
        # 自动取最新自选日期
        import db
        dates = db.get_watchlist_dates()
        if not dates:
            return
        watch_date = dates[0]

    label = _make_label(time_str)
    try:
        _snapshot_callback(watch_date, label)
    except Exception:
        pass


def start(snapshot_cb, config: dict = None) -> bool:
    """
    启动后台调度器。

    Args:
        snapshot_cb: 快照回调 (watch_date: str, label: str) -> None
        config: 调度配置 dict（不传则从文件读取）

    Returns:
        True=成功启动，False=APScheduler 不可用或已在运行
    """
    global _scheduler_instance, _snapshot_callback

    if not _APSCHEDULER_AVAILABLE:
        return False
    if _scheduler_instance is not None and _scheduler_instance.running:
        return True   # 已在运行

    _snapshot_callback = snapshot_cb
    cfg = config or load_config()
    times = cfg.get("times", DEFAULT_CONFIG["times"])

    _scheduler_instance = _APScheduler(timezone="Asia/Shanghai")
    for t in times:
        try:
            parts = t.split(":")
            hour, minute = int(parts[0]), int(parts[1])
            _scheduler_instance.add_job(
                _scheduled_job, "cron",
                hour=hour, minute=minute,
                args=[t],
                id=f"snap_{t}",
                replace_existing=True,
                misfire_grace_time=120,
            )
        except Exception:
            pass

    _scheduler_instance.start()
    return True


def stop():
    """停止后台调度器。"""
    global _scheduler_instance
    if _scheduler_instance is not None:
        try:
            if _scheduler_instance.running:
                _scheduler_instance.shutdown(wait=False)
        except Exception:
            pass
        _scheduler_instance = None


def is_running() -> bool:
    return (_scheduler_instance is not None and
            getattr(_scheduler_instance, "running", False))


def is_available() -> bool:
    """APScheduler 是否已安装。"""
    return _APSCHEDULER_AVAILABLE


def get_next_run_times() -> list:
    """返回各任务的下次执行时间（字符串列表）。"""
    if not is_running():
        return []
    result = []
    for job in _scheduler_instance.get_jobs():
        nrt = job.next_run_time
        if nrt:
            result.append(f"{job.id}: {nrt.strftime('%H:%M')}")
    return result
