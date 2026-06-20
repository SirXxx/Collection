# -*- coding: utf-8 -*-
"""
stats.py — 策略统计分析模块

统计"第一天筛选 → 第二天上涨"的成功率，并分析各筛选条件与次日涨幅的相关性，
为调整筛选策略提供数据支持。

核心概念：
  - 成功：某只股票在次日某快照时点的相对 add_price 涨幅 >= threshold_pct
  - 胜率 = 成功数 / 总数
  - 使用最后一个快照时点（或 "close"）作为次日收益判断基准
"""

from datetime import datetime, timedelta
import pandas as pd
import db
import tracker


# ── 单日统计 ──────────────────────────────────────────────────────

def calc_day_stats(watch_date: str, threshold_pct: float = 1.0,
                   prefer_label: str = "close") -> dict:
    """
    计算某个自选日期的策略表现。

    Args:
        watch_date:    自选日期 YYYY-MM-DD
        threshold_pct: 成功阈值（%），涨幅 >= 此值算成功
        prefer_label:  优先使用哪个时点的快照（"close" / "15:00" / "14:30" 等）
                       若不存在则使用最新快照

    Returns:
        {
            "date": str,
            "total": int,
            "success": int,
            "fail": int,
            "no_data": int,     # 无快照数据
            "win_rate": float,  # 成功数/有数据数
            "avg_pct": float,   # 平均涨幅
            "max_pct": float,
            "min_pct": float,
            "label": str,       # 实际使用的快照标签
        }
    """
    table = tracker.get_tracking_table(watch_date)
    if table.empty:
        return _empty_day_stats(watch_date)

    labels = db.get_snapshot_labels(watch_date)
    if not labels:
        return _empty_day_stats(watch_date)

    # 选标签
    use_label = prefer_label if prefer_label in labels else labels[-1]
    if use_label not in table.columns:
        return _empty_day_stats(watch_date)

    pcts = table[use_label]
    total = len(pcts)
    valid = pcts.dropna()
    no_data = total - len(valid)
    success = int((valid >= threshold_pct).sum())
    fail = int((valid < threshold_pct).sum())
    win_rate = round(success / len(valid) * 100, 1) if len(valid) > 0 else None
    avg_pct = round(float(valid.mean()), 2) if not valid.empty else None
    max_pct = round(float(valid.max()), 2) if not valid.empty else None
    min_pct = round(float(valid.min()), 2) if not valid.empty else None

    return {
        "date":     watch_date,
        "total":    total,
        "success":  success,
        "fail":     fail,
        "no_data":  no_data,
        "win_rate": win_rate,
        "avg_pct":  avg_pct,
        "max_pct":  max_pct,
        "min_pct":  min_pct,
        "label":    use_label,
    }


def _empty_day_stats(watch_date: str) -> dict:
    return {
        "date": watch_date, "total": 0, "success": 0, "fail": 0,
        "no_data": 0, "win_rate": None, "avg_pct": None,
        "max_pct": None, "min_pct": None, "label": "-",
    }


# ── 历史胜率趋势 ──────────────────────────────────────────────────

def get_history_stats(days: int = 30, threshold_pct: float = 1.0,
                      prefer_label: str = "close") -> pd.DataFrame:
    """
    返回最近 days 天的每日统计，按日期升序排列。

    返回 DataFrame 列：date, total, success, fail, win_rate, avg_pct
    """
    all_dates = db.get_watchlist_dates()
    if not all_dates:
        return pd.DataFrame()

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    dates = [d for d in all_dates if d >= cutoff]

    records = []
    for d in dates:
        stat = calc_day_stats(d, threshold_pct=threshold_pct, prefer_label=prefer_label)
        records.append(stat)

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df = df.sort_values("date").reset_index(drop=True)
    return df


# ── 条件相关性分析 ────────────────────────────────────────────────

# 可分析的连续型筛选条件字段
NUMERIC_CONDITION_FIELDS = [
    "add_pct_chg", "volume_ratio", "turnover", "market_cap",
    "pe", "pb", "eps", "revenue_yoy", "profit_yoy", "roe",
    "ma5", "ma10", "ma20", "tail_30min_pct",
]

# 可分析的布尔型条件字段
BOOL_CONDITION_FIELDS = [
    "above_ma5", "above_ma10", "above_ma20",
]


def _load_combined_data(days: int = 90, prefer_label: str = "close") -> pd.DataFrame:
    """
    合并 watchlist + tracking_snapshots，返回每行一只股票+其次日结果的宽表。
    包含字段：code, add_date, <condition_fields>, next_day_pct（次日涨幅）。
    """
    all_dates = db.get_watchlist_dates()
    if not all_dates:
        return pd.DataFrame()

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    dates = [d for d in all_dates if d >= cutoff]

    rows = []
    for watch_date in dates:
        watchlist = db.get_watchlist_by_date(watch_date)
        if not watchlist:
            continue
        table = tracker.get_tracking_table(watch_date)
        if table.empty:
            continue

        labels = db.get_snapshot_labels(watch_date)
        use_label = prefer_label if prefer_label in labels else (labels[-1] if labels else None)
        if not use_label or use_label not in table.columns:
            continue

        # 合并：watchlist 字段 + 次日涨幅
        for w in watchlist:
            code = w["code"]
            match = table[table["code"] == code]
            if match.empty:
                continue
            next_pct = match.iloc[0].get(use_label)
            row = {
                "code":      code,
                "add_date":  watch_date,
                "next_day_pct": float(next_pct) if next_pct is not None else None,
            }
            for f in NUMERIC_CONDITION_FIELDS + BOOL_CONDITION_FIELDS:
                row[f] = w.get(f)
            rows.append(row)

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def calc_condition_correlation(days: int = 90, prefer_label: str = "close") -> pd.DataFrame:
    """
    计算各筛选条件与次日涨幅的 Pearson 相关系数。

    返回 DataFrame 列：field, correlation, sample_size
    按 |correlation| 降序排列。
    """
    df = _load_combined_data(days=days, prefer_label=prefer_label)
    if df.empty or "next_day_pct" not in df.columns:
        return pd.DataFrame(columns=["field", "correlation", "sample_size"])

    target = df["next_day_pct"].dropna()
    records = []
    for field in NUMERIC_CONDITION_FIELDS + BOOL_CONDITION_FIELDS:
        if field not in df.columns:
            continue
        col = pd.to_numeric(df[field], errors="coerce")
        valid = col.notna() & df["next_day_pct"].notna()
        sample = int(valid.sum())
        if sample < 5:
            corr = None
        else:
            corr = round(float(col[valid].corr(df.loc[valid, "next_day_pct"])), 4)
        records.append({"field": field, "correlation": corr, "sample_size": sample})

    result = pd.DataFrame(records)
    if not result.empty and result["correlation"].notna().any():
        result = result.sort_values(
            by="correlation", key=lambda s: s.abs(), ascending=False, na_position="last"
        ).reset_index(drop=True)
    return result


def calc_winrate_by_condition(field: str, bins: int = 4,
                               threshold_pct: float = 1.0,
                               days: int = 90,
                               prefer_label: str = "close") -> pd.DataFrame:
    """
    按某个连续字段分箱，统计各箱的胜率和平均涨幅。
    用于柱状图展示"量比在X~Y范围内，胜率为Z%"。

    返回 DataFrame 列：range, total, win_rate, avg_pct
    """
    df = _load_combined_data(days=days, prefer_label=prefer_label)
    if df.empty or field not in df.columns:
        return pd.DataFrame(columns=["range", "total", "win_rate", "avg_pct"])

    col = pd.to_numeric(df[field], errors="coerce")
    target = df["next_day_pct"]
    valid = col.notna() & target.notna()
    if valid.sum() < 5:
        return pd.DataFrame(columns=["range", "total", "win_rate", "avg_pct"])

    df_v = pd.DataFrame({"x": col[valid], "y": target[valid]})
    df_v["bin"] = pd.cut(df_v["x"], bins=bins)

    records = []
    for interval, grp in df_v.groupby("bin", observed=True):
        total = len(grp)
        win = int((grp["y"] >= threshold_pct).sum())
        wr = round(win / total * 100, 1)
        avg = round(float(grp["y"].mean()), 2)
        records.append({
            "range":    str(interval),
            "total":    total,
            "win_rate": wr,
            "avg_pct":  avg,
        })
    return pd.DataFrame(records)


def generate_suggestions(days: int = 90, threshold_pct: float = 1.0,
                         prefer_label: str = "close",
                         top_n: int = 5) -> list:
    """
    自动生成策略调整建议文本列表。

    Returns: list of str
    """
    corr = calc_condition_correlation(days=days, prefer_label=prefer_label)
    if corr.empty:
        return ["数据不足，请积累更多跟踪记录后再分析。"]

    suggestions = []
    valid = corr[corr["correlation"].notna() & (corr["sample_size"] >= 5)]

    # 正相关最强的条件
    pos = valid[valid["correlation"] > 0].head(top_n)
    for _, row in pos.iterrows():
        suggestions.append(
            f"✅ 提高 [{row['field']}] 阈值：与次日涨幅正相关 (r={row['correlation']:.3f}，样本={row['sample_size']})"
        )

    # 负相关最强的条件（可能是反向指标）
    neg = valid[valid["correlation"] < 0].head(3)
    for _, row in neg.iterrows():
        suggestions.append(
            f"⚠️ 注意 [{row['field']}]：与次日涨幅负相关 (r={row['correlation']:.3f}，样本={row['sample_size']})"
        )

    # 总胜率
    hist = get_history_stats(days=days, threshold_pct=threshold_pct, prefer_label=prefer_label)
    if not hist.empty:
        valid_rates = hist["win_rate"].dropna()
        if not valid_rates.empty:
            overall = round(float(valid_rates.mean()), 1)
            suggestions.insert(0, f"📊 近{days}天整体胜率：{overall}%（成功阈值 {threshold_pct}%）")

    if not suggestions:
        suggestions = ["暂无足够数据生成建议。"]
    return suggestions
