# -*- coding: utf-8 -*-
"""
indicators.py — 技术指标计算模块

对筛选后的少量股票（Top N）进行指标丰富：
  - 日K线：MA5 / MA10 / MA20
  - 价格与均线相对位置（above/below）
  - MA5 趋势（up / down / flat）
  - 近5日最高价 / 最低价
  - 尾盘30分钟涨跌幅（需要分钟级K线，交易时间内有效）
  - ROE（单股财务指标接口，较慢）

使用方式：
  result = enrich_indicators(rows, fetch_tail=True, fetch_roe=True)

注意：接口调用有频率限制，批量时使用线程池+限速。
"""

from typing import Optional
import time
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import pandas as pd
import akshare as ak

# ── 全局内存缓存（单次运行周期内避免重复拉K线）────────────────────
_kline_cache: dict = {}          # code -> DataFrame
_min_cache: dict = {}            # code -> DataFrame (分钟K线)
_roe_cache: dict = {}            # code -> float


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _n_days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y%m%d")


# ── 日K线 ─────────────────────────────────────────────────────────

def get_kline(code: str, days: int = 35) -> pd.DataFrame:
    """获取最近 days 日的日K线（含收盘、最高、最低）。失败返回空 DataFrame。"""
    if code in _kline_cache:
        return _kline_cache[code]
    start = _n_days_ago(days + 15)   # 多取几天保证有足够交易日
    end = _today()
    df = pd.DataFrame()
    try:
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=start, end_date=end, adjust=""
        )
        if df is None or df.empty:
            df = pd.DataFrame()
        else:
            col_map = {}
            for c in df.columns:
                cs = str(c).strip()
                if cs == "日期":
                    col_map[c] = "date"
                elif cs == "收盘":
                    col_map[c] = "close"
                elif cs == "最高":
                    col_map[c] = "high"
                elif cs == "最低":
                    col_map[c] = "low"
                elif cs == "开盘":
                    col_map[c] = "open"
                elif cs == "成交量":
                    col_map[c] = "volume"
            df = df.rename(columns=col_map)
            needed = [c for c in ["date", "close", "high", "low"] if c in df.columns]
            df = df[needed].copy()
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df["high"] = pd.to_numeric(df.get("high", pd.Series(dtype=float)), errors="coerce")
            df["low"] = pd.to_numeric(df.get("low", pd.Series(dtype=float)), errors="coerce")
            df = df.dropna(subset=["close"]).tail(days).reset_index(drop=True)
    except Exception:
        df = pd.DataFrame()
    _kline_cache[code] = df
    return df


# ── 均线计算 ──────────────────────────────────────────────────────

def calc_ma(df: pd.DataFrame, periods: list = None) -> dict:
    """
    计算均线。
    df 需含 'close' 列，按时间升序排列。
    返回 {"ma5": float|None, "ma10": float|None, "ma20": float|None, ...}
    """
    if periods is None:
        periods = [5, 10, 20]
    result = {}
    if df.empty or "close" not in df.columns:
        return {f"ma{p}": None for p in periods}
    closes = df["close"].dropna()
    for p in periods:
        key = f"ma{p}"
        if len(closes) >= p:
            result[key] = round(float(closes.tail(p).mean()), 4)
        else:
            result[key] = None
    return result


def ma_trend(df: pd.DataFrame, period: int = 5, lookback: int = 3) -> str:
    """
    判断均线趋势。
    比较最近 lookback+1 个交易日，最新 MA 是否相对之前持续上升/下降。
    返回 'up' / 'down' / 'flat'
    """
    if df.empty or "close" not in df.columns:
        return "flat"
    closes = df["close"].dropna()
    total_needed = period + lookback
    if len(closes) < total_needed:
        return "flat"
    ma_values = closes.rolling(period).mean().dropna().values
    if len(ma_values) < lookback + 1:
        return "flat"
    recent = ma_values[-(lookback + 1):]
    diffs = [recent[i + 1] - recent[i] for i in range(lookback)]
    pos = sum(1 for d in diffs if d > 1e-6)
    neg = sum(1 for d in diffs if d < -1e-6)
    if pos == lookback:
        return "up"
    if neg == lookback:
        return "down"
    return "flat"


def price_vs_ma(price: float, ma_val: float) -> Optional[int]:
    """
    价格与均线相对位置。
    返回 1（price > ma）、0（price <= ma）、None（数据缺失）
    """
    if price is None or ma_val is None:
        return None
    return 1 if price > ma_val else 0


# ── 近N日高低点 ───────────────────────────────────────────────────

def recent_high_low(df: pd.DataFrame, days: int = 5) -> dict:
    """返回近 days 个交易日的最高价和最低价。"""
    result = {"day5_high": None, "day5_low": None}
    if df.empty:
        return result
    if "high" not in df.columns or "low" not in df.columns:
        return result
    sub = df.tail(days)
    highs = sub["high"].dropna()
    lows = sub["low"].dropna()
    if not highs.empty:
        result[f"day{days}_high"] = round(float(highs.max()), 4)
    if not lows.empty:
        result[f"day{days}_low"] = round(float(lows.min()), 4)
    return result


# ── 尾盘30分钟涨跌幅 ──────────────────────────────────────────────

def get_tail_30min_pct(code: str) -> Optional[float]:
    """
    获取当天最后30分钟（14:30-15:00）的涨跌幅。
    = (最后一根分钟K线收盘 - 14:30首根开盘) / 14:30首根开盘 * 100
    非交易时间或接口失败返回 None。
    """
    if code in _min_cache:
        df = _min_cache[code]
    else:
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            df = ak.stock_zh_a_hist_min_em(
                symbol=code, period="1",
                start_date=today + " 09:30:00",
                end_date=today + " 15:00:00",
                adjust=""
            )
            if df is None:
                df = pd.DataFrame()
        except Exception:
            df = pd.DataFrame()
        _min_cache[code] = df

    if df.empty:
        return None

    try:
        col_map = {}
        for c in df.columns:
            cs = str(c).strip()
            if cs == "时间":
                col_map[c] = "time"
            elif cs == "收盘":
                col_map[c] = "close"
            elif cs == "开盘":
                col_map[c] = "open"
        df2 = df.rename(columns=col_map)
        if "time" not in df2.columns or "close" not in df2.columns:
            return None

        df2["time_str"] = df2["time"].astype(str).str[-8:]   # "HH:MM:SS"
        tail = df2[df2["time_str"] >= "14:30:00"].copy()
        if tail.empty:
            return None

        start_open = pd.to_numeric(tail.iloc[0].get("open", tail.iloc[0]["close"]), errors="coerce")
        end_close = pd.to_numeric(tail.iloc[-1]["close"], errors="coerce")
        if pd.isna(start_open) or pd.isna(end_close) or start_open == 0:
            return None
        return round((end_close - start_open) / start_open * 100, 4)
    except Exception:
        return None


# ── ROE ───────────────────────────────────────────────────────────

def get_roe(code: str) -> Optional[float]:
    """
    获取最新年度 ROE。
    使用 akshare stock_financial_analysis_indicator 接口，较慢（~0.5s/只）。
    失败返回 None。
    """
    if code in _roe_cache:
        return _roe_cache[code]
    roe = None
    try:
        year = str(datetime.now().year - 1)
        df = ak.stock_financial_analysis_indicator(symbol=code, start_year=year)
        if df is not None and not df.empty:
            # 查找 ROE 列（净资产收益率）
            roe_col = None
            for c in df.columns:
                if "净资产收益率" in str(c) or "roe" in str(c).lower():
                    roe_col = c
                    break
            if roe_col:
                vals = pd.to_numeric(df[roe_col], errors="coerce").dropna()
                if not vals.empty:
                    roe = round(float(vals.iloc[-1]), 4)
    except Exception:
        pass
    _roe_cache[code] = roe
    return roe


# ── 单股综合指标 ──────────────────────────────────────────────────

def get_all_indicators(code: str, current_price: float,
                       fetch_tail: bool = True, fetch_roe: bool = False) -> dict:
    """
    获取单股所有技术指标，返回可直接合并到 row dict 的字段。
    """
    result = {}
    # K线 + 均线
    df = get_kline(code, days=35)
    ma_vals = calc_ma(df, [5, 10, 20])
    result.update(ma_vals)

    result["above_ma5"] = price_vs_ma(current_price, ma_vals.get("ma5"))
    result["above_ma10"] = price_vs_ma(current_price, ma_vals.get("ma10"))
    result["above_ma20"] = price_vs_ma(current_price, ma_vals.get("ma20"))
    result["ma5_trend"] = ma_trend(df, period=5, lookback=3)

    # 近5日高低
    hl = recent_high_low(df, days=5)
    result["day5_high"] = hl.get("day5_high")
    result["day5_low"] = hl.get("day5_low")

    # 尾盘
    if fetch_tail:
        result["tail_30min_pct"] = get_tail_30min_pct(code)

    # ROE
    if fetch_roe:
        result["roe"] = get_roe(code)

    return result


# ── 批量指标丰富 ──────────────────────────────────────────────────

def enrich_indicators(rows: list, fetch_tail: bool = True, fetch_roe: bool = False,
                      max_workers: int = 5, rate_sleep: float = 0.3,
                      progress_cb=None) -> list:
    """
    批量为筛选结果丰富技术指标。

    Args:
        rows: 筛选结果 list of dicts，需含 'code' 和 'price' 字段
        fetch_tail: 是否获取尾盘30分钟涨跌幅（分钟K线，较慢）
        fetch_roe: 是否获取 ROE（财务指标接口，更慢）
        max_workers: 并发线程数
        rate_sleep: 每次请求后的等待秒数（防止 API 限频）
        progress_cb: 进度回调 callback(current, total, code)
    """
    total = len(rows)
    if total == 0:
        return rows

    done_count = [0]

    def _worker(row):
        code = str(row.get("code", ""))
        price = row.get("price") or 0.0
        try:
            indicators = get_all_indicators(code, price, fetch_tail=fetch_tail, fetch_roe=fetch_roe)
            row.update(indicators)
        except Exception:
            pass
        done_count[0] += 1
        if progress_cb:
            try:
                progress_cb(done_count[0], total, code)
            except Exception:
                pass
        time.sleep(rate_sleep)
        return row

    with ThreadPoolExecutor(max_workers=max_workers) as exe:
        futures = [exe.submit(_worker, row) for row in rows]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception:
                pass

    return rows


def clear_cache():
    """清空本次运行的内存缓存（可在每次筛选开始时调用）。"""
    _kline_cache.clear()
    _min_cache.clear()
    _roe_cache.clear()


# ── 独立测试 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "000001"
    print(f"测试股票: {code}")
    df = get_kline(code)
    print(f"  K线 {len(df)} 行")
    if not df.empty:
        ma = calc_ma(df)
        print(f"  MA: {ma}")
        trend = ma_trend(df)
        print(f"  MA5趋势: {trend}")
        hl = recent_high_low(df, 5)
        print(f"  近5日高低: {hl}")
    tail = get_tail_30min_pct(code)
    print(f"  尾盘30min涨跌幅: {tail}")
    roe = get_roe(code)
    print(f"  ROE: {roe}")
