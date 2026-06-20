# -*- coding: utf-8 -*-
"""
AStockSelector_AK.py

基于 akshare 的 A 股筛选脚本。

功能：
1. 使用 akshare 拉取 A 股实时行情
2. 使用 akshare 拉取最新财报信息
3. 按条件筛选指定数量股票
4. 控制台打印并导出 CSV
5. 提供 finance-only 模式，便于在网络环境受限时先验证财报接口

示例：
python AStockSelector_AK.py
python AStockSelector_AK.py --top-n 20 --min-price 5 --max-price 50 --min-turnover 2 --min-volume-ratio 1.2 --max-pe 80 --sort-by pct_chg
python AStockSelector_AK.py --top-n 30 --min-market-cap 50 --min-eps 0.2 --min-profit-yoy 5 --sort-by volume_ratio
python AStockSelector_AK.py --finance-only --report-date 20241231 --top-n 20 --sort-by profit_yoy

说明：
- 市值单位：亿元
- 营收、净利润单位：亿元
- 成交量单位：手
- 若当前环境无法访问 akshare 的实时行情源，脚本会给出明确报错提示
"""

import argparse
import math
import os
import random
import sys
import time
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pandas as pd
import akshare as ak

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_SOURCE_OPTIONS = ["auto", "eastmoney", "sina"]
# auto      — 自动，依次尝试全部来源
# eastmoney — 东方财富（stock_zh_a_spot_em + push2 ulist 降级）
# sina      — 新浪财经（stock_zh_a_spot），历史K线备选网易163
A_CODE_PREFIX = (
    "000", "001", "002", "003",
    "300", "301",
    "600", "601", "603", "605",
    "688", "689",
)
BJ_CODE_PREFIX = ("430", "440", "830", "831", "832", "833", "834", "835", "836", "837", "838", "839", "870", "871", "872", "873", "874", "875", "876", "877", "878", "879", "880", "881", "882", "883", "884", "885", "886", "887", "888", "889")


def safe_float(v, default=None):
    if v is None:
        return default
    if isinstance(v, str):
        v = v.strip()
        if v in ("", "-", "--", "nan", "None"):
            return default
    try:
        if pd.isna(v):
            return default
    except Exception:
        pass
    try:
        return float(v)
    except Exception:
        return default


def now_str():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def recent_report_dates(max_count=8):
    today = datetime.now()
    y = today.year
    candidate = [
        f"{y}1231", f"{y}0930", f"{y}0630", f"{y}0331",
        f"{y-1}1231", f"{y-1}0930", f"{y-1}0630", f"{y-1}0331",
        f"{y-2}1231", f"{y-2}0930", f"{y-2}0630", f"{y-2}0331",
    ]
    uniq = []
    for x in candidate:
        if x not in uniq:
            uniq.append(x)
    return uniq[:max_count]


def normalize_code(code):
    return str(code).strip().zfill(6)


def is_a_share_code(code, include_bj=False):
    code = normalize_code(code)
    if code.startswith(A_CODE_PREFIX):
        return True
    if include_bj and code.startswith(BJ_CODE_PREFIX):
        return True
    return False


def fetch_spot_data(retries=3, sleep_sec=3, include_bj=False, data_source="auto"):
    """使用 akshare 拉取实时行情。
    data_source: 'auto'=自动依次尝试, 'eastmoney'=东方财富优先, 'sina'=新浪财经优先。
    财报数据始终来自东方财富接口。"""
    _em   = ("stock_zh_a_spot_em", getattr(ak, "stock_zh_a_spot_em", None))
    _sina = ("stock_zh_a_spot",    getattr(ak, "stock_zh_a_spot", None))
    if data_source == "eastmoney":
        funcs = [_em]
    elif data_source == "sina":
        funcs = [_sina, _em]
    else:  # auto
        funcs = [_em, _sina]
    attempts = []

    last_err = None
    for func_name, func in funcs:
        if func is None:
            attempts.append(f"{func_name}: 不存在")
            continue
        for i in range(1, retries + 1):
            try:
                print(f"尝试实时行情接口: {func_name} (第 {i}/{retries} 次)")
                df = func()
                if df is None or df.empty:
                    raise RuntimeError("返回空数据")
                return standardize_spot_df(df, source=func_name, include_bj=include_bj)
            except Exception as e:
                last_err = e
                attempts.append(f"{func_name} 第{i}次失败: {repr(e)}")
                time.sleep(sleep_sec)

    # 降级方案：直接通过 push2 ulist.np/get 批量查询（仅限东方财富/自动模式）
    if data_source != "sina":
        print("所选行情接口均不可用，尝试降级方案: push2 ulist.np/get 批量查询...")
        for ulist_try in range(1, retries + 1):
            try:
                print(f"  ulist.np 尝试 第 {ulist_try}/{retries} 次...")
                df = _fetch_spot_via_ulist(include_bj=include_bj)
                if df is not None and not df.empty:
                    return df
            except Exception as e:
                last_err = e
                attempts.append(f"ulist.np 第{ulist_try}次失败: {repr(e)}")
                if ulist_try < retries:
                    time.sleep(sleep_sec)

    msg = "\n".join(attempts[-10:]) if attempts else repr(last_err)
    source_hint = {"eastmoney": "东方财富", "sina": "新浪财经", "auto": "全部来源"}.get(data_source, data_source)
    raise RuntimeError(
        f"实时行情接口均不可用（数据来源：{source_hint}）。\n"
        "可能原因：\n"
        "  1. 当前网络环境无法访问所选数据源\n"
        "  2. 服务器临时故障或接口变更\n"
        "建议：切换其他数据来源，或使用 --finance-only 模式仅获取财报数据。\n"
        f"最近失败记录：\n{msg}"
    ) from last_err


def _generate_a_share_secids(include_bj=False):
    """生成所有 A 股 secids（market.code 格式）。"""
    secids = []
    # 沪市 (market=1): 600xxx, 601xxx, 603xxx, 605xxx, 688xxx, 689xxx
    for prefix in ["600", "601", "603", "605", "688", "689"]:
        for i in range(1000):
            secids.append(f"1.{prefix}{i:03d}")
    # 深市 (market=0): 000xxx, 001xxx, 002xxx, 003xxx, 300xxx, 301xxx
    for prefix in ["000", "001", "002", "003", "300", "301"]:
        for i in range(1000):
            secids.append(f"0.{prefix}{i:03d}")
    if include_bj:
        for prefix in ["430", "830", "831", "832", "833", "834", "835",
                        "836", "837", "838", "839", "870", "871", "872",
                        "873", "874", "875", "876", "877", "878", "879",
                        "880", "881", "882", "883", "884", "885", "886",
                        "887", "888", "889"]:
            for i in range(1000):
                secids.append(f"0.{prefix}{i:03d}")
    return secids


def _fetch_spot_via_ulist(include_bj=False, batch_size=500):
    """通过 push2.eastmoney.com/api/qt/ulist.np/get 批量查询行情（降级方案）。"""
    import requests as _requests

    all_secids = _generate_a_share_secids(include_bj=include_bj)
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://quote.eastmoney.com/center/gridlist.html",
    }
    fields = ("f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,"
              "f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,f115,f152")

    all_diffs = []
    fail_count = 0
    total_batches = math.ceil(len(all_secids) / batch_size)
    for batch_idx in range(total_batches):
        batch = all_secids[batch_idx * batch_size : (batch_idx + 1) * batch_size]
        params = {
            "secids": ",".join(batch),
            "fields": fields,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
        }
        try:
            r = _requests.get(url, params=params, headers=headers, timeout=20)
            if r.status_code != 200:
                fail_count += 1
                if fail_count >= 3:
                    raise RuntimeError(f"push2 ulist.np 连续返回 HTTP {r.status_code}，服务器可能宕机")
                time.sleep(1)
                continue
            data = r.json()
            if data.get("data") and data["data"].get("diff"):
                for item in data["data"]["diff"]:
                    if item.get("f2") not in (None, "-", ""):
                        all_diffs.append(item)
                fail_count = 0  # 重置失败计数
        except _requests.exceptions.RequestException as e:
            fail_count += 1
            if fail_count >= 3:
                raise RuntimeError(f"push2 ulist.np 连续请求失败: {e}") from e
            time.sleep(1)
            continue
        if batch_idx < total_batches - 1:
            time.sleep(random.uniform(0.3, 0.8))

    if not all_diffs:
        raise RuntimeError("ulist.np 批量查询未获取到任何有效行情数据")

    print(f"ulist.np 降级方案成功获取 {len(all_diffs)} 只股票行情")

    df = pd.DataFrame(all_diffs)
    col_map = {
        "f12": "代码", "f14": "名称", "f2": "最新价", "f3": "涨跌幅",
        "f4": "涨跌额", "f5": "成交量", "f6": "成交额", "f7": "振幅",
        "f8": "换手率", "f9": "市盈率-动态", "f10": "量比",
        "f11": "5分钟涨跌", "f15": "最高", "f16": "最低", "f17": "今开",
        "f18": "昨收", "f20": "总市值", "f21": "流通市值", "f22": "涨速",
        "f23": "市净率", "f24": "60日涨跌幅", "f25": "年初至今涨跌幅",
    }
    df.rename(columns=col_map, inplace=True)
    df = standardize_spot_df(df, source="ulist.np_fallback", include_bj=include_bj)
    return df


def standardize_spot_df(df, source, include_bj=False):
    cols = {str(x).strip(): x for x in df.columns}

    def pick(*names):
        for n in names:
            if n in cols:
                return cols[n]
        return None

    mapping = {
        "code": pick("代码", "股票代码", "symbol", "证券代码"),
        "name": pick("名称", "股票简称", "证券简称"),
        "price": pick("最新价", "现价", "当前价", "trade"),
        "pct_chg": pick("涨跌幅", "涨跌幅%", "changepercent"),
        "volume": pick("成交量", "volume"),
        "volume_ratio": pick("量比"),
        "turnover": pick("换手率", "turnoverratio"),
        "pe": pick("市盈率-动态", "市盈率", "per"),
        "market_cap": pick("总市值", "总市值(元)", "mktcap"),
        "circ_market_cap": pick("流通市值", "流通市值(元)", "nmc"),
    }

    required = ["code", "name", "price"]
    missing = [k for k in required if mapping[k] is None]
    if missing:
        raise RuntimeError(f"实时行情字段缺失: {missing}; 实际字段: {list(df.columns)}")

    use_cols = [v for v in mapping.values() if v is not None]
    out = df[use_cols].copy()
    rename_map = {v: k for k, v in mapping.items() if v is not None}
    out.rename(columns=rename_map, inplace=True)

    out["code"] = out["code"].map(normalize_code)
    out["name"] = out["name"].astype(str).str.strip()

    for col in ["price", "pct_chg", "volume", "volume_ratio", "turnover", "pe", "market_cap", "circ_market_cap"]:
        if col in out.columns:
            out[col] = out[col].map(safe_float)

    if "market_cap" in out.columns:
        out["market_cap"] = out["market_cap"].map(lambda x: x / 1e8 if x is not None else None)
    if "circ_market_cap" in out.columns:
        out["circ_market_cap"] = out["circ_market_cap"].map(lambda x: x / 1e8 if x is not None else None)

    out = out[out["code"].map(lambda x: is_a_share_code(x, include_bj=include_bj))]
    out = out[out["name"].str.len() > 0]
    out = out[(out["price"].notna()) & (out["price"] > 0)]
    out = out.reset_index(drop=True)
    out.attrs["spot_source"] = source
    return out


def fetch_report_data(report_date="", max_try_dates=8, include_bj=False):
    """使用 akshare 财报接口拉取最新报告。"""
    dates = [report_date] if report_date else recent_report_dates(max_try_dates)
    last_err = None

    for d in dates:
        try:
            print(f"尝试财报日期: {d}")
            df = ak.stock_yjbb_em(date=d)
            if df is None or df.empty:
                continue
            out = standardize_report_df(df, report_date=d, include_bj=include_bj)
            if not out.empty:
                return out, d
        except Exception as e:
            last_err = e
            continue

    if report_date:
        raise RuntimeError(f"无法获取指定财报日期 {report_date} 的数据: {repr(last_err)}") from last_err
    raise RuntimeError(f"无法获取可用财报数据，最近错误: {repr(last_err)}") from last_err


def standardize_report_df(df, report_date, include_bj=False):
    cols = {str(x).strip(): x for x in df.columns}

    def pick(*names):
        for n in names:
            if n in cols:
                return cols[n]
        return None

    mapping = {
        "code": pick("股票代码", "代码"),
        "name": pick("股票简称", "名称"),
        "eps": pick("每股收益"),
        "revenue": pick("营业总收入-营业总收入", "营业收入-营业收入"),
        "revenue_yoy": pick("营业总收入-同比增长", "营业收入-同比增长"),
        "net_profit": pick("净利润-净利润"),
        "profit_yoy": pick("净利润-同比增长"),
        "industry": pick("所处行业", "行业"),
        "notice_date": pick("最新公告日期", "公告日期"),
    }

    if mapping["code"] is None:
        raise RuntimeError(f"财报字段缺失 code；实际字段: {list(df.columns)}")

    use_cols = [v for v in mapping.values() if v is not None]
    out = df[use_cols].copy()
    rename_map = {v: k for k, v in mapping.items() if v is not None}
    out.rename(columns=rename_map, inplace=True)

    out["code"] = out["code"].map(normalize_code)
    if "name" in out.columns:
        out["name"] = out["name"].astype(str).str.strip()

    for col in ["eps", "revenue", "revenue_yoy", "net_profit", "profit_yoy"]:
        if col in out.columns:
            out[col] = out[col].map(safe_float)

    if "revenue" in out.columns:
        out["revenue"] = out["revenue"].map(lambda x: x / 1e8 if x is not None else None)
    if "net_profit" in out.columns:
        out["net_profit"] = out["net_profit"].map(lambda x: x / 1e8 if x is not None else None)

    if "notice_date" not in out.columns:
        out["notice_date"] = None

    out["report_date"] = report_date
    out = out[out["code"].map(lambda x: is_a_share_code(x, include_bj=include_bj))]
    out = out.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)
    return out


def fetch_recent_lows(code, days=5, data_source="auto"):
    """获取股票最近 days 个交易日的最低价。
    data_source: 'eastmoney'=东方财富 K线, 'sina'=尝试网易163降级, 'auto'=EM优先+163备用。"""
    from datetime import timedelta
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

    _hist_163 = getattr(ak, "stock_zh_a_hist_163", None)
    if data_source == "eastmoney":
        hist_funcs = [("em", ak.stock_zh_a_hist)]
    elif data_source == "sina":
        hist_funcs = [("163", _hist_163), ("em", ak.stock_zh_a_hist)] if _hist_163 else [("em", ak.stock_zh_a_hist)]
    else:  # auto
        hist_funcs = [("em", ak.stock_zh_a_hist)] + ([("163", _hist_163)] if _hist_163 else [])

    for src_name, func in hist_funcs:
        if func is None:
            continue
        try:
            if src_name == "em":
                df = func(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="")
            else:  # 163
                df = func(symbol=code, start_date=start_date, end_date=end_date)
            if df is None or df.empty:
                continue
            col_map = {}
            for c in df.columns:
                cs = str(c).strip()
                if cs == "日期":
                    col_map[c] = "date"
                elif cs in ("最低", "最低价"):
                    col_map[c] = "low"
            df = df.rename(columns=col_map)
            if "date" not in df.columns or "low" not in df.columns:
                continue
            df = df[["date", "low"]].copy()
            df["date"] = df["date"].astype(str).str[:10]
            df["low"] = df["low"].map(safe_float)
            df = df[df["low"].notna()].tail(days)
            return df.to_dict(orient="records")
        except Exception:
            continue
    return []


def enrich_with_recent_lows(rows, days=5, max_workers=5, data_source="auto"):
    """为筛选结果批量补充近 days 个交易日最低价（low_1 最旧，low_N 最新），并发获取。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _worker(row):
        code = row.get("code", "")
        lows = fetch_recent_lows(code, days=days, data_source=data_source)
        padded = [None] * (days - len(lows)) + lows
        for idx, entry in enumerate(padded, start=1):
            if entry:
                row[f"low_{idx}"] = entry["low"]
                row[f"low_date_{idx}"] = entry["date"]
            else:
                row[f"low_{idx}"] = None
                row[f"low_date_{idx}"] = ""
        return row

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_worker, row) for row in rows]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception:
                pass
    return rows


def pass_low_rising_filter(item):
    """检验近3天最低价是否单调递增 (low_3 < low_4 < low_5)，数据不足返回 False。"""
    v3 = safe_float(item.get("low_3"))
    v4 = safe_float(item.get("low_4"))
    v5 = safe_float(item.get("low_5"))
    if v3 is None or v4 is None or v5 is None:
        return False
    return v3 < v4 < v5


def pass_filters(item, args):
    def ge(v, threshold):
        return True if threshold is None else (v is not None and v >= threshold)

    def le(v, threshold):
        return True if threshold is None else (v is not None and v <= threshold)

    checks = [
        ge(item.get("price"), args.min_price),
        le(item.get("price"), args.max_price),
        ge(item.get("pct_chg"), args.min_pct_chg),
        le(item.get("pct_chg"), args.max_pct_chg),
        ge(item.get("volume"), args.min_volume),
        ge(item.get("volume_ratio"), args.min_volume_ratio),
        le(item.get("volume_ratio"), args.max_volume_ratio),
        ge(item.get("turnover"), args.min_turnover),
        le(item.get("turnover"), args.max_turnover),
        ge(item.get("market_cap"), args.min_market_cap),
        le(item.get("market_cap"), args.max_market_cap),
        ge(item.get("pe"), args.min_pe),
        le(item.get("pe"), args.max_pe),
        ge(item.get("eps"), args.min_eps),
        ge(item.get("net_profit"), args.min_net_profit),
        ge(item.get("revenue_yoy"), args.min_revenue_yoy),
        ge(item.get("profit_yoy"), args.min_profit_yoy),
    ]
    return all(checks)


def sort_value(item, sort_by):
    v = item.get(sort_by)
    if v is None:
        return -math.inf
    return v


def format_num(v, digits=2, default="-"):
    if v is None:
        return default
    try:
        return f"{float(v):,.{digits}f}"
    except Exception:
        return default


def print_table(rows):
    if not rows:
        print("未筛选到符合条件的股票。")
        return

    headers = [
        ("代码", 8),
        ("名称", 10),
        ("现价", 8),
        ("涨跌幅%", 9),
        ("成交量", 12),
        ("量比", 8),
        ("换手%", 8),
        ("总市值亿", 11),
        ("PE", 8),
        ("EPS", 8),
        ("净利润亿", 10),
        ("利润同比%", 10),
        ("公告日", 12),
        ("报告期", 10),
    ]
    line = " ".join(str(title).ljust(width) for title, width in headers)
    print(line)
    print("-" * len(line))

    for x in rows:
        vals = [
            str(x.get("code", ""))[:8].ljust(8),
            str(x.get("name", ""))[:10].ljust(10),
            format_num(x.get("price"), 2).rjust(8),
            format_num(x.get("pct_chg"), 2).rjust(9),
            format_num(x.get("volume"), 0).rjust(12),
            format_num(x.get("volume_ratio"), 2).rjust(8),
            format_num(x.get("turnover"), 2).rjust(8),
            format_num(x.get("market_cap"), 2).rjust(11),
            format_num(x.get("pe"), 2).rjust(8),
            format_num(x.get("eps"), 2).rjust(8),
            format_num(x.get("net_profit"), 2).rjust(10),
            format_num(x.get("profit_yoy"), 2).rjust(10),
            str(x.get("notice_date") or "-")[:10].ljust(12),
            str(x.get("report_date") or "-")[:10].ljust(10),
        ]
        print(" ".join(vals))


def export_csv(rows, path):
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=[
            "code", "name", "price", "pct_chg", "volume", "volume_ratio", "turnover",
            "market_cap", "circ_market_cap", "pe", "eps", "revenue", "net_profit",
            "revenue_yoy", "profit_yoy", "industry", "notice_date", "report_date"
        ])
    df.to_csv(path, index=False, encoding="utf-8-sig")


def build_parser():
    p = argparse.ArgumentParser(description="基于 akshare 的 A 股筛选脚本")
    p.add_argument("--top-n", type=int, default=20, help="输出前 N 只股票")
    p.add_argument("--sort-by", type=str, default="pct_chg",
                   choices=["price", "pct_chg", "volume", "volume_ratio", "turnover", "market_cap", "circ_market_cap", "pe", "eps", "revenue", "net_profit", "revenue_yoy", "profit_yoy"],
                   help="排序字段")
    p.add_argument("--ascending", action="store_true", help="升序排序，默认降序")
    p.add_argument("--csv", type=str, default="", help="自定义导出 CSV 文件名")
    p.add_argument("--report-date", type=str, default="", help="指定财报日期，如 20241231；不传则自动尝试最近可用报告期")
    p.add_argument("--finance-only", action="store_true", help="仅测试/输出财报数据，不拉取实时行情")
    p.add_argument("--include-bj", action="store_true", help="包含北交所股票")

    p.add_argument("--min-price", type=float)
    p.add_argument("--max-price", type=float)
    p.add_argument("--min-pct-chg", type=float)
    p.add_argument("--max-pct-chg", type=float)
    p.add_argument("--min-volume", type=float)
    p.add_argument("--min-volume-ratio", type=float)
    p.add_argument("--max-volume-ratio", type=float)
    p.add_argument("--min-turnover", type=float)
    p.add_argument("--max-turnover", type=float)
    p.add_argument("--min-market-cap", type=float, help="单位：亿元")
    p.add_argument("--max-market-cap", type=float, help="单位：亿元")
    p.add_argument("--min-pe", type=float)
    p.add_argument("--max-pe", type=float)

    p.add_argument("--min-eps", type=float)
    p.add_argument("--min-net-profit", type=float, help="单位：亿元")
    p.add_argument("--min-revenue-yoy", type=float, help="营收同比下限 %")
    p.add_argument("--min-profit-yoy", type=float, help="净利润同比下限 %")

    p.add_argument("--spot-retries", type=int, default=3, help="实时行情接口重试次数")
    p.add_argument("--spot-retry-sleep", type=int, default=3, help="实时行情接口重试间隔秒数")
    p.add_argument("--low-rising", action="store_true",
                   help="补充近5日最低价并只保留近3天最低价单调递增的股票")
    p.add_argument("--data-source", type=str, default="auto",
                   choices=DATA_SOURCE_OPTIONS,
                   help="数据来源: auto=自动, eastmoney=东方财富, sina=新浪财经（财报始终来自东方财富）")
    return p


def finance_only_mode(args):
    report_df, used_date = fetch_report_data(report_date=args.report_date, include_bj=args.include_bj)
    rows = report_df.to_dict(orient="records")
    rows = [x for x in rows if pass_filters(x, args)]
    rows.sort(key=lambda x: sort_value(x, args.sort_by), reverse=not args.ascending)
    rows = rows[:args.top_n]

    if getattr(args, "low_rising", False):
        print(f"正在补充 {len(rows)} 只股票近5日最低价（并发请求）...")
        rows = enrich_with_recent_lows(rows, days=5, data_source=getattr(args, "data_source", "auto"))
        before_low = len(rows)
        rows = [r for r in rows if pass_low_rising_filter(r)]
        print(f"近3天最低价递增筛选后剩余 {len(rows)}/{before_low} 只")

    print()
    print(f"财报模式：使用报告期 {used_date}，筛选出 {len(rows)} 只股票")
    print_table(rows)

    csv_name = args.csv.strip() if args.csv else f"a_stock_finance_only_{used_date}_{now_str()}.csv"
    csv_path = os.path.join(BASE_DIR, csv_name)
    export_csv(rows, csv_path)
    print()
    print(f"结果已导出到: {csv_path}")


def realtime_mode(args):
    data_source = getattr(args, "data_source", "auto")
    print(f"开始拉取 akshare 实时行情（数据来源: {data_source}）...")
    spot_df = fetch_spot_data(retries=args.spot_retries, sleep_sec=args.spot_retry_sleep,
                              include_bj=args.include_bj, data_source=data_source)
    print(f"实时行情拉取完成，共 {len(spot_df)} 只股票；来源: {spot_df.attrs.get('spot_source', '-')}")

    print("开始拉取 akshare 财报数据...")
    report_df, used_date = fetch_report_data(report_date=args.report_date, include_bj=args.include_bj)
    print(f"财报数据拉取完成，共 {len(report_df)} 条；使用报告期: {used_date}")

    merged = pd.merge(spot_df, report_df, on="code", how="left", suffixes=("", "_report"))
    if "name_report" in merged.columns:
        merged["name"] = merged["name"].fillna(merged["name_report"])
        merged.drop(columns=["name_report"], inplace=True)

    rows = merged.to_dict(orient="records")
    rows = [x for x in rows if pass_filters(x, args)]
    rows.sort(key=lambda x: sort_value(x, args.sort_by), reverse=not args.ascending)
    rows = rows[:args.top_n]

    if getattr(args, "low_rising", False):
        print(f"正在补充 {len(rows)} 只股票近5日最低价（并发请求）...")
        rows = enrich_with_recent_lows(rows, days=5, data_source=getattr(args, "data_source", "auto"))
        before_low = len(rows)
        rows = [r for r in rows if pass_low_rising_filter(r)]
        print(f"近3天最低价递增筛选后剩余 {len(rows)}/{before_low} 只")

    print()
    print(f"最终选出 {len(rows)} 只股票：")
    print_table(rows)

    csv_name = args.csv.strip() if args.csv else f"a_stock_selected_ak_{now_str()}.csv"
    csv_path = os.path.join(BASE_DIR, csv_name)
    export_csv(rows, csv_path)
    print()
    print(f"结果已导出到: {csv_path}")


def main():
    args = build_parser().parse_args()
    if args.finance_only:
        finance_only_mode(args)
    else:
        realtime_mode(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已中断")
        sys.exit(1)
    except Exception as e:
        print(f"运行失败: {e}")
        sys.exit(2)
