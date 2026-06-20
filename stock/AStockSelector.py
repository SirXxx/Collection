# -*- coding: utf-8 -*-
"""
AStockSelector.py

功能：
1. 拉取 A 股实时行情（东方财富公开接口）
2. 根据条件筛选股票
3. 选出指定数量股票
4. 补充盈利/估值/最新财报信息
5. 控制台展示并导出 CSV

默认字段：
- 当前价格
- 涨跌幅
- 成交量
- 量比
- 换手率
- 总市值 / 流通市值
- 市盈率 PE
- 最新财报公告日
- 最新报告期
- 基本每股收益 EPS
- 营业总收入
- 归母净利润
- 营收同比
- 净利润同比

运行示例：
python AStockSelector.py
python AStockSelector.py --top-n 20 --min-price 5 --max-price 60 --min-turnover 2 --min-volume-ratio 1.2 --max-pe 80 --sort-by pct_chg
python AStockSelector.py --top-n 30 --min-market-cap 50 --min-eps 0.2 --min-profit-yoy 5 --sort-by volume_ratio

说明：
- 市值单位：亿元
- 成交量单位：手（东方财富实时字段）
- PE 使用东方财富实时字段，部分股票可能为 '-' 或空
- 数据源依赖公开接口，若接口变动需调整字段映射
"""

import argparse
import csv
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
REALTIME_URLS = [
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://82.push2.eastmoney.com/api/qt/clist/get",
]
REPORT_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"

# A股（沪深主板 + 创业板 + 科创板）
A_SHARE_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"


def http_get_json(url, timeout=15, retries=2, sleep_sec=0.4):
    last_err = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Referer": "https://quote.eastmoney.com/",
                    "Accept": "application/json,text/plain,*/*",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            return json.loads(raw)
        except Exception as e:
            last_err = e
            time.sleep(sleep_sec)
    raise last_err


def safe_float(v, default=None):
    if v in (None, "", "-", "--"):
        return default
    try:
        return float(v)
    except Exception:
        return default


def safe_int(v, default=None):
    if v in (None, "", "-", "--"):
        return default
    try:
        return int(float(v))
    except Exception:
        return default


def format_num(v, digits=2, default="-"):
    if v is None:
        return default
    try:
        return f"{float(v):,.{digits}f}"
    except Exception:
        return default


def now_str():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def fetch_realtime_all():
    """分页抓取全部 A 股实时行情。"""
    fields = "f12,f14,f2,f3,f5,f8,f9,f10,f20,f21"
    page = 1
    page_size = 1000
    all_rows = []
    total = None

    while True:
        params = {
            "pn": page,
            "pz": page_size,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": A_SHARE_FS,
            "fields": fields,
        }
        data = None
        last_err = None
        for base_url in REALTIME_URLS:
            try:
                url = base_url + "?" + urllib.parse.urlencode(params)
                data = http_get_json(url, timeout=20, retries=3, sleep_sec=1.0)
                if (((data or {}).get("data") or {}).get("diff")):
                    break
            except Exception as e:
                last_err = e
                data = None
        if data is None:
            raise last_err or RuntimeError("实时行情接口请求失败")
        diff = (((data or {}).get("data") or {}).get("diff")) or []
        if not diff:
            break
        all_rows.extend(diff)

        if total is None:
            total = (((data or {}).get("data") or {}).get("total")) or len(diff)

        if len(all_rows) >= total:
            break
        page += 1

    rows = []
    for item in all_rows:
        price = safe_float(item.get("f2"))
        pct_chg = safe_float(item.get("f3"))
        volume = safe_int(item.get("f5"))
        turnover = safe_float(item.get("f8"))
        pe = safe_float(item.get("f9"))
        volume_ratio = safe_float(item.get("f10"))
        total_mv = safe_float(item.get("f20"))
        circ_mv = safe_float(item.get("f21"))
        code = str(item.get("f12") or "").strip()
        name = str(item.get("f14") or "").strip()

        # 剔除异常/停牌/名称空
        if not code or not name or price is None or price <= 0:
            continue

        rows.append({
            "code": code,
            "name": name,
            "price": price,
            "pct_chg": pct_chg,
            "volume": volume,
            "volume_ratio": volume_ratio,
            "turnover": turnover,
            "pe": pe,
            "market_cap": (total_mv / 1e8) if total_mv is not None else None,
            "circ_market_cap": (circ_mv / 1e8) if circ_mv is not None else None,
        })
    return rows


def fetch_latest_report(code):
    """抓取单只股票最新财报信息。"""
    filter_text = f'(SECURITY_CODE="{code}")'
    params = {
        "reportName": "RPT_LICO_FN_CPD",
        "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,NOTICE_DATE,REPORTDATE,BASIC_EPS,TOTAL_OPERATE_INCOME,PARENT_NETPROFIT,YSTZ,SJLTZ",
        "filter": filter_text,
        "pageNumber": 1,
        "pageSize": 1,
        "sortTypes": -1,
        "sortColumns": "NOTICE_DATE",
        "source": "HSF10",
        "client": "PC",
    }
    url = REPORT_URL + "?" + urllib.parse.urlencode(params)
    data = http_get_json(url, timeout=20, retries=1, sleep_sec=0.2)
    rows = (((data or {}).get("result") or {}).get("data")) or []
    if not rows:
        return {
            "notice_date": None,
            "report_date": None,
            "eps": None,
            "revenue": None,
            "net_profit": None,
            "revenue_yoy": None,
            "profit_yoy": None,
        }
    r = rows[0]
    revenue = safe_float(r.get("TOTAL_OPERATE_INCOME"))
    net_profit = safe_float(r.get("PARENT_NETPROFIT"))
    return {
        "notice_date": r.get("NOTICE_DATE"),
        "report_date": r.get("REPORTDATE"),
        "eps": safe_float(r.get("BASIC_EPS")),
        "revenue": (revenue / 1e8) if revenue is not None else None,
        "net_profit": (net_profit / 1e8) if net_profit is not None else None,
        "revenue_yoy": safe_float(r.get("YSTZ")),
        "profit_yoy": safe_float(r.get("SJLTZ")),
    }


def merge_financials(candidates, max_workers=8, show_progress=True):
    if not candidates:
        return candidates

    total = len(candidates)
    finished = 0

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_map = {ex.submit(fetch_latest_report, item["code"]): item for item in candidates}
        for fut in as_completed(future_map):
            item = future_map[fut]
            try:
                finance = fut.result()
            except Exception:
                finance = {
                    "notice_date": None,
                    "report_date": None,
                    "eps": None,
                    "revenue": None,
                    "net_profit": None,
                    "revenue_yoy": None,
                    "profit_yoy": None,
                }
            item.update(finance)
            finished += 1
            if show_progress:
                print(f"\r补充财报信息: {finished}/{total}", end="", flush=True)
    if show_progress:
        print()
    return candidates


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


def print_table(rows):
    if not rows:
        print("未筛选到符合条件的股票。")
        return

    headers = [
        ("代码", 8),
        ("名称", 10),
        ("现价", 8),
        ("涨跌幅%", 9),
        ("成交量(手)", 12),
        ("量比", 8),
        ("换手%", 8),
        ("总市值亿", 11),
        ("PE", 8),
        ("EPS", 8),
        ("净利润亿", 10),
        ("利润同比%", 10),
        ("公告日", 12),
        ("报告期", 12),
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
            str((x.get("notice_date") or "-")[:10]).ljust(12),
            str((x.get("report_date") or "-")[:10]).ljust(12),
        ]
        print(" ".join(vals))


def export_csv(rows, path):
    fields = [
        "code", "name", "price", "pct_chg", "volume", "volume_ratio", "turnover",
        "market_cap", "circ_market_cap", "pe", "notice_date", "report_date",
        "eps", "revenue", "net_profit", "revenue_yoy", "profit_yoy"
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fields})



def build_arg_parser():
    p = argparse.ArgumentParser(description="A股实时筛选脚本")
    p.add_argument("--top-n", type=int, default=20, help="输出前 N 只股票")
    p.add_argument("--sort-by", type=str, default="pct_chg",
                   choices=["price", "pct_chg", "volume", "volume_ratio", "turnover", "market_cap", "pe", "eps", "revenue", "net_profit", "revenue_yoy", "profit_yoy"],
                   help="排序字段")
    p.add_argument("--ascending", action="store_true", help="升序排序，默认降序")

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

    p.add_argument("--candidate-multiplier", type=int, default=5,
                   help="先根据实时行情初筛后，再抓财报的候选倍数，默认 top_n*5")
    p.add_argument("--max-workers", type=int, default=8, help="并发抓财报线程数")
    p.add_argument("--csv", type=str, default="", help="自定义导出 CSV 文件名")
    return p



def main():
    args = build_arg_parser().parse_args()

    print("开始拉取 A 股实时行情...")
    rows = fetch_realtime_all()
    print(f"实时行情拉取完成，共 {len(rows)} 只股票")

    # 先按实时条件筛一轮（财报类条件此时先不管）
    prefiltered = []
    for x in rows:
        if args.min_price is not None and (x.get("price") is None or x["price"] < args.min_price):
            continue
        if args.max_price is not None and (x.get("price") is None or x["price"] > args.max_price):
            continue
        if args.min_pct_chg is not None and (x.get("pct_chg") is None or x["pct_chg"] < args.min_pct_chg):
            continue
        if args.max_pct_chg is not None and (x.get("pct_chg") is None or x["pct_chg"] > args.max_pct_chg):
            continue
        if args.min_volume is not None and (x.get("volume") is None or x["volume"] < args.min_volume):
            continue
        if args.min_volume_ratio is not None and (x.get("volume_ratio") is None or x["volume_ratio"] < args.min_volume_ratio):
            continue
        if args.max_volume_ratio is not None and (x.get("volume_ratio") is None or x["volume_ratio"] > args.max_volume_ratio):
            continue
        if args.min_turnover is not None and (x.get("turnover") is None or x["turnover"] < args.min_turnover):
            continue
        if args.max_turnover is not None and (x.get("turnover") is None or x["turnover"] > args.max_turnover):
            continue
        if args.min_market_cap is not None and (x.get("market_cap") is None or x["market_cap"] < args.min_market_cap):
            continue
        if args.max_market_cap is not None and (x.get("market_cap") is None or x["market_cap"] > args.max_market_cap):
            continue
        if args.min_pe is not None and (x.get("pe") is None or x["pe"] < args.min_pe):
            continue
        if args.max_pe is not None and (x.get("pe") is None or x["pe"] > args.max_pe):
            continue
        prefiltered.append(x)

    reverse = not args.ascending
    prefiltered.sort(key=lambda x: sort_value(x, args.sort_by), reverse=reverse)
    print(f"按实时条件初筛后剩余 {len(prefiltered)} 只股票")

    if not prefiltered:
        print("没有符合实时条件的股票。")
        return

    need_finance = any([
        args.min_eps is not None,
        args.min_net_profit is not None,
        args.min_revenue_yoy is not None,
        args.min_profit_yoy is not None,
        args.sort_by in {"eps", "revenue", "net_profit", "revenue_yoy", "profit_yoy"},
    ])

    candidate_count = max(args.top_n * max(1, args.candidate_multiplier), args.top_n)
    candidates = prefiltered[:candidate_count]

    print(f"准备处理候选股票 {len(candidates)} 只")
    if need_finance or True:
        # 无论如何补一次财报，满足用户要求输出最新财报信息
        merge_financials(candidates, max_workers=args.max_workers, show_progress=True)

    final_rows = [x for x in candidates if pass_filters(x, args)]
    final_rows.sort(key=lambda x: sort_value(x, args.sort_by), reverse=reverse)
    final_rows = final_rows[:args.top_n]

    print()
    print(f"最终选出 {len(final_rows)} 只股票：")
    print_table(final_rows)

    csv_name = args.csv.strip() if args.csv else f"a_stock_selected_{now_str()}.csv"
    csv_path = os.path.join(BASE_DIR, csv_name)
    export_csv(final_rows, csv_path)
    print()
    print(f"结果已导出到: {csv_path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已中断")
        sys.exit(1)
    except Exception as e:
        print(f"运行失败: {e}")
        sys.exit(2)
