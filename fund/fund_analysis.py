# -*- coding: utf-8 -*-
"""
fund/fund_analysis.py
场外基金（开放式基金）收益率排行 & 季度新增重仓股分析

【数据源说明】
  通过修改 fund/config.json 中的 "data_source" 字段切换：

  "sina_cninfo"  ← 默认，适用于公司网络受限环境
    - 新浪基金净值API : 场外基金历史净值 → 收益率排行（基于 fund_pool 基金池）
    - 新浪 + 东方财富  : 场内ETF行情 + 历史收益率（可选，include_etf=true）
    - 巨潮资讯        : 全市场基金重仓股季报聚合数据 + 行业配置

  "eastmoney"    ← 适用于无网络限制环境
    - 东方财富  : 开放式基金完整排行（股票型/混合型，含近1月/3月/6月/1年）
    - 东方财富  : 个基季度持仓明细（fund_portfolio_hold_em）
    - 新浪净值API : fund_pool 基金池补充对比（与东财排行对照）

【输出路径】
  fund/output/fund_analysis_result_YYYYMMDD.xlsx   （分析结果）
  fund/output/cache/                               （API 响应缓存，可删除重拉）

运行：
  C:\LegacyApp\Python\Python39\python.exe fund/fund_analysis.py
  python fund/fund_analysis.py
"""

import os
import sys
import time
import json
import warnings
from datetime import datetime, timedelta
from collections import defaultdict

# ── 标准输出编码 ──────────────────────────────────────────────────────────────
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── 路径定义 ──────────────────────────────────────────────────────────────────
FUND_DIR    = os.path.dirname(os.path.abspath(__file__))   # fund/
ROOT_DIR    = os.path.dirname(FUND_DIR)                    # collection/
OUTPUT_DIR  = os.path.join(FUND_DIR, "output")
CACHE_DIR   = os.path.join(OUTPUT_DIR, "cache")
CONFIG_PATH = os.path.join(FUND_DIR, "config.json")

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(CACHE_DIR,  exist_ok=True)

# ── 读取配置 ──────────────────────────────────────────────────────────────────
def _load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)

CFG = _load_config()

DATA_SOURCE = CFG.get("data_source",   "sina_cninfo")   # "eastmoney" | "sina_cninfo"
SSL_VERIFY  = CFG.get("ssl_verify",    False)
TOP_N       = CFG.get("top_n",         10)
FUND_POOL   = CFG.get("fund_pool",     [])
FUND_TYPES  = CFG.get("fund_types",    ["股票型", "混合型"])
INCLUDE_ETF = CFG.get("include_etf",   True)
ETF_SAMPLE  = CFG.get("etf_sample",    100)
Q1_DATE     = CFG.get("compare_dates", {}).get("current_quarter", "20250331")
Q4_DATE     = CFG.get("compare_dates", {}).get("prev_quarter",    "20241231")

TODAY   = datetime.now()
PERIODS = {
    "近1月": (TODAY - timedelta(days=31)) .strftime("%Y%m%d"),
    "近3月": (TODAY - timedelta(days=92)) .strftime("%Y%m%d"),
    "近6月": (TODAY - timedelta(days=183)).strftime("%Y%m%d"),
    "近1年": (TODAY - timedelta(days=365)).strftime("%Y%m%d"),
}

# ── SSL 配置 ──────────────────────────────────────────────────────────────────
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import requests
from requests.adapters import HTTPAdapter

if not SSL_VERIFY:
    _orig_send = HTTPAdapter.send
    def _patched_send(self, request, **kwargs):
        kwargs["verify"] = False
        return _orig_send(self, request, **kwargs)
    HTTPAdapter.send = _patched_send

# ── 依赖导入 ──────────────────────────────────────────────────────────────────
import pandas as pd
import akshare as ak
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

SLEEP = 1.2   # 接口调用间隔（秒）

# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────

def safe_float(v, default=None):
    if v is None:
        return default
    if isinstance(v, str):
        v = v.strip().rstrip("%")
        if v in ("", "-", "--", "nan", "None"):
            return default
    try:
        return float(v)
    except Exception:
        return default


def cpth(name):
    return os.path.join(CACHE_DIR, name)


def load_csv(name):
    p = cpth(name)
    return pd.read_csv(p, dtype=str) if os.path.exists(p) else None


def save_csv(name, df):
    df.to_csv(cpth(name), index=False, encoding="utf-8-sig")


def section(title):
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


# ─────────────────────────────────────────────────────────────────────────────
# 数据源 A：东方财富（eastmoney）
# ─────────────────────────────────────────────────────────────────────────────

def em_fetch_fund_rank():
    """
    东方财富：获取股票型 + 混合型开放式基金排行，合并返回。
    列包含：基金代码, 基金简称, 近1月, 近3月, 近6月, 近1年, 资产规模(亿元) 等
    """
    cache = load_csv("em_fund_rank.csv")
    if cache is not None:
        print(f"[缓存] 东方财富基金排行 {len(cache)} 条")
        return cache

    frames = []
    for ft in FUND_TYPES:
        print(f"[获取] 东方财富基金排行 - {ft} ...", flush=True)
        try:
            df = ak.fund_open_fund_rank_em(symbol=ft)
            df["_基金类型"] = ft
            frames.append(df)
            print(f"  → {ft}: {len(df)} 只")
        except Exception as e:
            print(f"  [错误] {ft}: {e}")
        time.sleep(1.5)

    if not frames:
        raise RuntimeError("东方财富基金排行接口失败，请检查网络或切换 data_source 为 sina_cninfo")

    result = pd.concat(frames, ignore_index=True)
    save_csv("em_fund_rank.csv", result)
    return result


def em_top_funds_by_period(rank_df):
    """从排行 DataFrame 中按4个时间段各取 TOP_N，返回 dict。"""
    cols = rank_df.columns.tolist()
    code_col  = next((c for c in cols if "代码" in c), None)
    name_col  = next((c for c in cols if "简称" in c or "名称" in c), None)
    scale_col = next((c for c in cols if "规模" in c or "资产" in c), None)

    period_col_map = {}
    for p in PERIODS:
        if p in cols:
            period_col_map[p] = p
        else:
            matched = [c for c in cols if p in c]
            if matched:
                period_col_map[p] = matched[0]

    results = {}
    for period, col in period_col_map.items():
        df = rank_df.copy()
        df["_r"] = df[col].apply(safe_float)
        top = df.dropna(subset=["_r"]).sort_values("_r", ascending=False).head(TOP_N)
        results[period] = [
            {
                "代码":       str(r[code_col]).strip() if code_col else "",
                "名称":       str(r[name_col]).strip() if name_col else "",
                "收益率":     r["_r"],
                "类型":       r.get("_基金类型", ""),
                "规模(亿元)": safe_float(r.get(scale_col)) if scale_col else None,
            }
            for _, r in top.iterrows()
        ]
    return results


def em_fetch_fund_portfolio(fund_code, quarter_date):
    """东方财富：获取单只基金指定季度的持仓（Top10股）。"""
    year = quarter_date[:4]
    cache = load_csv(f"em_hold_{fund_code}_{quarter_date}.csv")
    if cache is not None:
        return cache
    try:
        df = ak.fund_portfolio_hold_em(symbol=fund_code, date=year)
        time.sleep(1.5)
        if df is None or df.empty:
            return pd.DataFrame()
        date_col = next((c for c in df.columns if "报告" in c or "期" in c), None)
        if date_col:
            target = quarter_date[:6]
            mask = df[date_col].astype(str).str.replace("-", "").str.startswith(target)
            df_q = df[mask]
            if not df_q.empty:
                df = df_q
        save_csv(f"em_hold_{fund_code}_{quarter_date}.csv", df)
        return df
    except Exception as e:
        print(f"  [错误] {fund_code} 持仓({quarter_date}): {e}")
        return pd.DataFrame()


def em_analyze_new_stocks(top_by_period):
    """
    东方财富模式：对所有上榜基金，对比两季持仓找出新增股票。
    返回 changes_df (统一格式)
    """
    all_funds = {}
    for period, funds in top_by_period.items():
        for f in funds:
            c = f["代码"]
            if c and c not in all_funds:
                all_funds[c] = f

    stock_fund_count = defaultdict(list)
    stock_name_map   = {}

    for i, (code, info) in enumerate(all_funds.items(), 1):
        print(f"  [{i}/{len(all_funds)}] {code} {info.get('名称','')}", flush=True)
        q1_df = em_fetch_fund_portfolio(code, Q1_DATE)
        q4_df = em_fetch_fund_portfolio(code, Q4_DATE)

        def _codes(df):
            if df is None or df.empty: return set()
            c = next((x for x in df.columns if "代码" in x), None)
            return set(df[c].astype(str).str.strip()) if c else set()

        def _name_map(df):
            if df is None or df.empty: return {}
            cc = next((x for x in df.columns if "代码" in x), None)
            nc = next((x for x in df.columns if "名称" in x), None)
            return dict(zip(df[cc].astype(str).str.strip(), df[nc].astype(str).str.strip())) if cc and nc else {}

        q1_c, q4_c = _codes(q1_df), _codes(q4_df)
        new_stocks  = {s for s in q1_c - q4_c if s and s != "nan"}
        nm = _name_map(q1_df)
        for s in new_stocks:
            stock_fund_count[s].append(code)
            if nm.get(s) and nm[s] != "未知":
                stock_name_map[s] = nm[s]

    rows = []
    for sc, fund_codes in stock_fund_count.items():
        rows.append({
            "股票代码":       sc,
            "股票简称":       stock_name_map.get(sc, "未知"),
            "Q1基金覆盖家数": len(fund_codes),
            "Q4基金覆盖家数": 0,
            "覆盖家数变化":   len(fund_codes),
            "Q1持股市值(万)": None,
            "变化标签":       "新进重仓",
            "新增的基金":     "、".join(fund_codes),
        })
    changes = pd.DataFrame(rows).sort_values("Q1基金覆盖家数", ascending=False).reset_index(drop=True)
    return changes


# ─────────────────────────────────────────────────────────────────────────────
# 场外基金排行与持仓分析（新浪基金净值API + HTML页面解析）
# ─────────────────────────────────────────────────────────────────────────────

SINA_NAV_URL  = "https://stock.finance.sina.com.cn/fundInfo/api/openapi.php/CaihuiFundInfoService.getNav"
SINA_INFO_URL = "https://stock.finance.sina.com.cn/fundInfo/api/openapi.php/CaihuiFundInfoService.getBasicInfo"
SINA_FUND_PAGE = "https://finance.sina.com.cn/fund/quotes/{code}/bc.shtml"

# 日期工具 —— PERIODS 中为 YYYYMMDD 格式，NAV API 用 YYYY-MM-DD
_PERIOD_TARGETS = {}    # {period_name: datetime}
for _pn, _ps in PERIODS.items():
    _PERIOD_TARGETS[_pn] = datetime.strptime(_ps, "%Y%m%d")


# ── 基金池自动发现 ─────────────────────────────────────────────────────────

def sina_discover_fund_pool(seed_codes, max_depth=1):
    """
    从种子基金代码出发，爬取新浪基金HTML页面中的同类基金列表，
    自动扩展基金池。返回去重后的代码列表。
    """
    cache_path = cpth("discovered_fund_pool.json")
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)
            if len(cached) >= 20:
                print(f"[缓存] 基金池发现列表 {len(cached)} 只")
                return cached

    all_codes = set(seed_codes)
    to_visit  = list(seed_codes)
    visited   = set()

    for depth in range(max_depth + 1):
        batch = [c for c in to_visit if c not in visited]
        if not batch:
            break
        print(f"  [发现] 第{depth}轮：爬取 {len(batch)} 个基金页面...", flush=True)
        for code in batch:
            visited.add(code)
            try:
                url = SINA_FUND_PAGE.format(code=code)
                r = requests.get(url, timeout=20,
                                 headers={"User-Agent": "Mozilla/5.0"})
                r.encoding = "utf-8"
                found = set(re.findall(r'/fund/quotes/(\d{6})/', r.text))
                new_found = found - all_codes
                all_codes.update(found)
                if new_found:
                    to_visit.extend(new_found)
            except Exception:
                pass
            time.sleep(0.5)

    result = sorted(all_codes)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    print(f"  → 基金池扩展至 {len(result)} 只")
    return result


# ── 优化版收益率计算（定向日期请求） ───────────────────────────────────────

def _sina_fast_get_nav_at(fund_code, target_dt, window=10):
    """取 target_dt 附近 ±window 天的NAV，返回 target_dt 之前最近的 (date, nav)"""
    from_dt = (target_dt - timedelta(days=window)).strftime("%Y-%m-%d")
    to_dt   = (target_dt + timedelta(days=3)).strftime("%Y-%m-%d")
    try:
        r = requests.get(SINA_NAV_URL, params={
            "symbol": fund_code, "datefrom": from_dt, "dateto": to_dt,
            "page": 1, "num": 30
        }, timeout=20)
        items = r.json().get("result", {}).get("data", {}).get("data", [])
        if not items:
            return None
        # 选 target_dt 当天或之前最近的 NAV
        target_str = target_dt.strftime("%Y-%m-%d")
        candidates = [(str(it["fbrq"])[:10], safe_float(it.get("jjjz")))
                      for it in items if str(it["fbrq"])[:10] <= target_str]
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0]
        # 如果 target_dt 之前没数据，取最早的（基金可能成立不久）
        fallback = [(str(it["fbrq"])[:10], safe_float(it.get("jjjz"))) for it in items]
        fallback.sort(key=lambda x: x[0])
        return fallback[0] if fallback else None
    except Exception:
        return None


def _sina_fast_returns_single(fund_code):
    """快速计算单只基金各周期收益率，返回 dict 或 None"""
    cache_path = cpth(f"fast_ret_{fund_code}.json")
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)

    # 1. 获取最新净值
    latest = _sina_fast_get_nav_at(fund_code, TODAY, window=10)
    if not latest or not latest[1]:
        return None
    latest_date, latest_nav = latest

    # 2. 获取各周期历史净值
    rec = {"代码": fund_code, "名称": fund_code,
           "最新净值": latest_nav, "最新日期": latest_date,
           "规模(亿元)": None, "类型": "开放式基金"}
    for period_name, target_dt in _PERIOD_TARGETS.items():
        hist = _sina_fast_get_nav_at(fund_code, target_dt, window=10)
        time.sleep(0.2)
        if hist and hist[1] and hist[1] > 0:
            rec[period_name] = round((latest_nav - hist[1]) / hist[1] * 100, 2)
        else:
            rec[period_name] = None

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    return rec


def sina_calc_fund_pool_returns(fund_codes=None, max_workers=5):
    """
    对基金池中的所有基金并行计算收益率。
    返回 [{"代码", "名称", "最新净值", "近1月", "近3月", "近6月", "近1年", ...}, ...]
    """
    codes = fund_codes or FUND_POOL
    if not codes:
        return []

    results = []
    total   = len(codes)

    # 先检查有多少已缓存
    uncached = [c for c in codes if not os.path.exists(cpth(f"fast_ret_{c}.json"))]
    cached_count = total - len(uncached)
    if cached_count > 0:
        print(f"  [缓存] {cached_count} 只基金收益率已缓存", flush=True)

    if uncached:
        print(f"  [获取] {len(uncached)} 只基金收益率（{max_workers}线程并行）...", flush=True)

    done = 0
    failed = 0

    def _worker(code):
        return code, _sina_fast_returns_single(code)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker, c): c for c in codes}
        for future in as_completed(futures):
            code, ret = future.result()
            done += 1
            if ret:
                results.append(ret)
            else:
                failed += 1
            if done % 10 == 0 or done == total:
                print(f"    进度 {done}/{total} (成功={len(results)} 失败={failed})",
                      flush=True)

    print(f"  → 共计算 {len(results)} 只基金收益率")
    return results


def sina_top_funds_by_period(all_results):
    """从 fund_pool 收益率结果中按各周期排行 TOP_N，返回统一格式 dict。"""
    results = {}
    for period in PERIODS:
        sorted_funds = sorted(
            [r for r in all_results if r.get(period) is not None],
            key=lambda r: r[period], reverse=True
        )
        results[period] = [
            {
                "代码":       r["代码"],
                "名称":       r["名称"],
                "收益率":     r[period],
                "规模(亿元)": r.get("规模(亿元)"),
                "类型":       r.get("类型", "开放式基金"),
            }
            for r in sorted_funds[:TOP_N]
        ]
    return results


# ── 基金持仓解析（新浪HTML页面） ──────────────────────────────────────────

def sina_parse_fund_holdings(fund_code):
    """
    从新浪基金HTML页面解析基金详情：名称、十大重仓股、持仓变化。
    返回 dict:
      name, manager, data_date,
      holdings: [{stock_code, stock_name, pct, pct_change}, ...]
    """
    cache_path = cpth(f"fund_holdings_{fund_code}.json")
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)

    result = {"code": fund_code, "name": fund_code, "manager": "",
              "data_date": "", "holdings": []}
    try:
        url = SINA_FUND_PAGE.format(code=fund_code)
        r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        r.encoding = "utf-8"
        html = r.text

        # 基金名称
        m = re.search(r"\$FConfig\['fname'\]\s*=\s*'([^']+)'", html)
        if m:
            result["name"] = m.group(1)

        # 基金经理
        m = re.search(r"\$FConfig\['type3id'\]\s*=\s*'([^']+)'", html)
        result["type_id"] = m.group(1) if m else ""

        # 基金经理
        m = re.search(r'基金经理.*?<a[^>]*>([^<]+)</a>', html)
        if m:
            result["manager"] = m.group(1).strip()

        # 数据日期
        m = re.search(r'数据日期：<span>([^<]+)</span>', html)
        if m:
            result["data_date"] = m.group(1)

        # 十大重仓股代码
        m = re.search(r'id="fund_sdzc_table"\s+codelist="([^"]+)"', html)
        code_list = m.group(1).split(",") if m else []

        # 解析持仓表格行
        table_match = re.search(
            r'id="fund_sdzc_table"[^>]*>(.*?)</table>', html, re.S)
        if table_match:
            rows = re.findall(
                r'<tr>\s*<td><a[^>]*>([^<]+)</a></td>(.*?)</tr>',
                table_match.group(1), re.S)
            for i, (stock_name, rest) in enumerate(rows):
                cells = [re.sub(r'<[^>]+>', '', c).strip()
                         for c in re.findall(r'<td[^>]*>(.*?)</td>', rest, re.S)]
                # cells: [最新价, 涨跌幅, 持股比例, 较上期变化, 持股基金数, 基金变化, 咨询]
                sc = code_list[i] if i < len(code_list) else ""
                # 去掉 sh/sz 前缀
                sc_num = sc[2:] if len(sc) == 8 and sc[:2] in ("sh", "sz") else sc
                pct       = safe_float(cells[2].rstrip("%")) if len(cells) > 2 else None
                pct_chg   = cells[3] if len(cells) > 3 else ""
                pct_chg_v = safe_float(pct_chg.rstrip("%"))

                result["holdings"].append({
                    "stock_code":   sc_num,
                    "stock_name":   stock_name.strip(),
                    "pct":          pct,
                    "pct_change":   pct_chg,
                    "pct_change_v": pct_chg_v,
                })

    except Exception as e:
        result["_error"] = str(e)

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    return result


def update_fund_names(pool_results):
    """用 HTML 页面解析到的基金名称回填收益率结果和缓存"""
    updated = 0
    for rec in pool_results:
        code = rec["代码"]
        if rec["名称"] != code:
            continue  # 已有名称
        info = sina_parse_fund_holdings(code)
        if info and info["name"] != code:
            rec["名称"] = info["name"]
            # 回写缓存
            cp = cpth(f"fast_ret_{code}.json")
            if os.path.exists(cp):
                with open(cp, encoding="utf-8") as f:
                    cached = json.load(f)
                cached["名称"] = info["name"]
                with open(cp, "w", encoding="utf-8") as f:
                    json.dump(cached, f, ensure_ascii=False)
            updated += 1
    return updated


def cross_fund_stock_analysis(top_fund_details):
    """
    对多只基金的持仓做交叉分析，找出被多只基金新增或加仓的个股。
    top_fund_details: [{code, name, holdings: [{stock_code, stock_name, pct, pct_change_v}, ...]}, ...]
    返回 DataFrame
    """
    stock_map = defaultdict(lambda: {
        "stock_name": "", "funds_holding": [], "funds_new_or_add": [],
        "total_pct": 0, "max_pct": 0
    })

    for fd in top_fund_details:
        fund_label = f"{fd['name']}({fd['code']})"
        for h in fd.get("holdings", []):
            sc = h["stock_code"]
            if not sc:
                continue
            info = stock_map[sc]
            info["stock_name"] = h["stock_name"]
            info["funds_holding"].append(fund_label)
            pct = h.get("pct") or 0
            info["total_pct"] += pct
            info["max_pct"] = max(info["max_pct"], pct)
            # 新增或加仓判断
            pct_chg = h.get("pct_change_v")
            pct_chg_s = h.get("pct_change", "")
            if pct_chg_s == "-":
                # "-" 表示上期未持有（新增）
                info["funds_new_or_add"].append(f"{fund_label}[新增]")
            elif pct_chg is not None and pct_chg > 0:
                info["funds_new_or_add"].append(
                    f"{fund_label}[+{pct_chg:.1f}%]")

    rows = []
    for sc, info in stock_map.items():
        rows.append({
            "股票代码":     sc,
            "股票简称":     info["stock_name"],
            "持有基金数":   len(info["funds_holding"]),
            "新增加仓数":   len(info["funds_new_or_add"]),
            "平均仓位%":    round(info["total_pct"] / len(info["funds_holding"]), 2)
                            if info["funds_holding"] else 0,
            "最大仓位%":    info["max_pct"],
            "持有基金":     "、".join(info["funds_holding"]),
            "新增/加仓基金": "、".join(info["funds_new_or_add"]),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df.sort_values(["新增加仓数", "持有基金数"], ascending=[False, False], inplace=True)
        df.reset_index(drop=True, inplace=True)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# ETF 收益率（场内基金，可选）—— 新浪财经 + 东方财富历史数据
# ─────────────────────────────────────────────────────────────────────────────

def get_etf_spot():
    cache = load_csv("etf_spot.csv")
    if cache is not None:
        print(f"[缓存] ETF行情 {len(cache)} 只")
        return cache
    print("[获取] ETF实时行情 (fund_etf_spot_em)...", flush=True)
    df = ak.fund_etf_spot_em()
    save_csv("etf_spot.csv", df)
    print(f"  → 共 {len(df)} 只ETF")
    return df


def _etf_sina_code(code: str) -> str:
    """将纯6位代码转为新浪格式 sz/sh+code"""
    if len(code) == 8 and code[:2] in ("sh", "sz"):
        return code
    # 沪市ETF: 51xxxx, 58xxxx, 50xxxx
    if code[:2] in ("51", "58", "50"):
        return "sh" + code
    return "sz" + code


def fetch_etf_history_once(code: str):
    """
    一次性获取ETF近400天历史数据，返回带 date/close 列的 DataFrame。
    code 可为6位纯数字 ('159915') 或含前缀 ('sz159915')。
    优先用东方财富 fund_etf_hist_em（快），失败降级新浪财经。
    """
    # 归一化：取6位纯数字
    num_code = code[2:] if (len(code) == 8 and code[:2] in ("sh", "sz")) else code
    sina_code = _etf_sina_code(num_code)
    start_1y = (TODAY - timedelta(days=400)).strftime("%Y%m%d")
    end_today = TODAY.strftime("%Y%m%d")
    try:
        df = ak.fund_etf_hist_em(
            symbol=num_code, period="daily",
            start_date=start_1y, end_date=end_today, adjust=""
        )
        time.sleep(0.5)
        if df is not None and not df.empty and "日期" in df.columns and "收盘" in df.columns:
            df = df[["日期", "收盘"]].rename(columns={"日期": "date", "收盘": "close"})
            df["date"] = pd.to_datetime(df["date"])
            return df
    except Exception:
        pass
    # 降级到新浪（全历史，慢但可靠）
    try:
        df = ak.fund_etf_hist_sina(symbol=sina_code)
        time.sleep(0.5)
        if df is not None and not df.empty:
            df = df[["date", "close"]].copy()
            df["date"] = pd.to_datetime(df["date"])
            return df
    except Exception:
        pass
    return None


def get_price_at_date_from_df(hist_df: pd.DataFrame, target_date: str):
    """从已有历史DataFrame查找最近交易日收盘价"""
    if hist_df is None or hist_df.empty:
        return None
    td = pd.to_datetime(target_date, format="%Y%m%d")
    sub = hist_df[hist_df["date"] <= td]
    if sub.empty:
        return None
    return float(sub.iloc[-1]["close"])


def calc_etf_returns(spot_df: pd.DataFrame) -> pd.DataFrame:
    """对 ETF 列表计算各周期收益率，返回 DataFrame。"""
    cache = load_csv("etf_returns.csv")
    if cache is not None:
        print(f"[缓存] ETF收益率 {len(cache)} 只")
        return cache

    spot_df = spot_df.copy()
    spot_df["_cap"] = spot_df["总市值"].apply(safe_float)
    top_etfs = spot_df.dropna(subset=["_cap"]).sort_values("_cap", ascending=False).head(ETF_SAMPLE)

    print(f"[计算] 对市值前 {len(top_etfs)} 只ETF计算历史收益率（每只仅1次API请求）...", flush=True)

    rows = []
    total = len(top_etfs)
    for i, (_, row) in enumerate(top_etfs.iterrows(), 1):
        raw_code = str(row["代码"]).strip()   # e.g. 'sz159915'
        name     = str(row["名称"]).strip()
        cap      = safe_float(row["总市值"])
        cur_p    = safe_float(row["最新价"])

        if not raw_code or cur_p is None:
            continue

        rec = {
            "代码":       raw_code,
            "名称":       name,
            "规模(亿元)": round(cap / 1e8, 2) if cap else None,
            "最新价":     cur_p,
        }

        pkey = cpth(f"etf_prices_{raw_code}.json")
        if os.path.exists(pkey):
            with open(pkey, encoding="utf-8") as f:
                prices = json.load(f)
        else:
            # 只拉取一次历史数据
            hist_df = fetch_etf_history_once(raw_code)
            prices = {}
            for period_name, hist_date in PERIODS.items():
                prices[period_name] = get_price_at_date_from_df(hist_df, hist_date)
            with open(pkey, "w", encoding="utf-8") as f:
                json.dump(prices, f)

        for period_name, hist_p in prices.items():
            if hist_p and hist_p > 0 and cur_p:
                rec[period_name] = round((cur_p - hist_p) / hist_p * 100, 2)
            else:
                rec[period_name] = None

        rows.append(rec)
        if i % 10 == 0:
            print(f"  已处理 {i}/{total}", flush=True)

    result = pd.DataFrame(rows)
    save_csv("etf_returns.csv", result)
    return result


def top_etfs_by_period(returns_df: pd.DataFrame) -> dict:
    """返回 {期间: [{'代码', '名称', '收益率', '规模'},...]}"""
    result = {}
    for period in PERIODS:
        if period not in returns_df.columns:
            continue
        df = returns_df.copy()
        df["_r"] = df[period].apply(safe_float)
        top = df.dropna(subset=["_r"]).sort_values("_r", ascending=False).head(TOP_N)
        result[period] = [
            {
                "代码":       r["代码"],
                "名称":       r["名称"],
                "收益率":     r["_r"],
                "规模(亿元)": safe_float(r.get("规模(亿元)")),
            }
            for _, r in top.iterrows()
        ]
    return result


# ─── Part B: 基金重仓股对比（巨潮资讯） ─────────────────────────────────────

def get_fund_heavy_stocks(date_str: str) -> pd.DataFrame:
    """获取指定季报日的全市场基金重仓股数据（巨潮资讯）"""
    cache = load_csv(f"heavy_stocks_{date_str}.csv")
    if cache is not None:
        print(f"[缓存] {date_str} 基金重仓股 {len(cache)} 条")
        return cache
    print(f"[获取] {date_str} 基金重仓股 (cninfo)...", flush=True)
    df = ak.fund_report_stock_cninfo(date=date_str)
    time.sleep(SLEEP)
    save_csv(f"heavy_stocks_{date_str}.csv", df)
    print(f"  → {len(df)} 只股票")
    return df


def get_industry_allocation(date_str: str) -> pd.DataFrame:
    """获取指定季报日的基金行业配置（巨潮资讯）"""
    cache = load_csv(f"industry_alloc_{date_str}.csv")
    if cache is not None:
        return cache
    print(f"[获取] {date_str} 基金行业配置 (cninfo)...", flush=True)
    df = ak.fund_report_industry_allocation_cninfo(date=date_str)
    time.sleep(SLEEP)
    save_csv(f"industry_alloc_{date_str}.csv", df)
    return df


def analyze_new_additions(q1_df: pd.DataFrame, q4_df: pd.DataFrame) -> pd.DataFrame:
    """
    对比 Q1 与 Q4 基金重仓股，识别：
      - 新进重仓：Q4 不在列表，Q1 新出现
      - 大幅增持：基金覆盖家数（家数）显著增加
    """
    q1 = q1_df.copy()
    q4 = q4_df.copy()

    q1["基金覆盖家数"] = q1["基金覆盖家数"].apply(safe_float)
    q4["基金覆盖家数"] = q4["基金覆盖家数"].apply(safe_float)
    q1["持股总市值"]   = q1["持股总市值"].apply(safe_float)

    q4_codes = set(q4["股票代码"].astype(str).str.strip())
    q4_map   = q4.set_index(q4["股票代码"].astype(str).str.strip())["基金覆盖家数"].to_dict()

    rows = []
    for _, row in q1.iterrows():
        code   = str(row["股票代码"]).strip()
        name   = str(row["股票简称"]).strip()
        cnt_q1 = safe_float(row["基金覆盖家数"], 0)
        cnt_q4 = safe_float(q4_map.get(code), 0)
        delta  = cnt_q1 - cnt_q4

        if code not in q4_codes:
            label = "新进重仓"
        elif delta >= 100:
            label = f"大幅增持"
        elif delta > 0:
            label = f"增持"
        elif delta < -100:
            label = "大幅减持"
        else:
            continue

        rows.append({
            "股票代码":       code,
            "股票简称":       name,
            "Q1基金覆盖家数": int(cnt_q1),
            "Q4基金覆盖家数": int(cnt_q4),
            "覆盖家数变化":   int(delta),
            "Q1持股市值(万)": safe_float(row.get("持股总市值")),
            "变化标签":       label,
        })

    df_out = pd.DataFrame(rows)
    df_out.sort_values("覆盖家数变化", ascending=False, inplace=True)
    return df_out.reset_index(drop=True)


# ─── Part C: 股票行业查询（巨潮资讯） ────────────────────────────────────────

_ind_cache: dict = {}

def get_stock_industry(code: str) -> str:
    """通过巨潮资讯查询股票行业分类"""
    if code in _ind_cache:
        return _ind_cache[code]
    pkey = cpth(f"ind_{code}.json")
    if os.path.exists(pkey):
        with open(pkey, encoding="utf-8") as f:
            v = json.load(f)
        _ind_cache[code] = v
        return v
    latest = "未知"
    try:
        df = ak.stock_industry_change_cninfo(symbol=code)
        time.sleep(0.5)
        if not df.empty:
            latest = str(df.iloc[-1].get("行业名称", "")).strip() or "未知"
    except Exception:
        try:
            df2 = ak.stock_individual_info_em(symbol=code)
            time.sleep(0.5)
            row_map = dict(zip(df2.iloc[:, 0].astype(str), df2.iloc[:, 1].astype(str)))
            latest = row_map.get("行业", row_map.get("所属行业", "未知"))
        except Exception:
            latest = "未知"
    with open(pkey, "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False)
    _ind_cache[code] = latest
    return latest


# ─── 输出辅助函数 ─────────────────────────────────────────────────────────

def _print_cross_analysis(cross_df):
    """打印交叉持仓分析结果"""
    if cross_df is None or cross_df.empty:
        return
    section("Part A-3 · 多基金交叉持仓分析（被多只上榜基金重仓的个股）")
    multi = cross_df[cross_df["持有基金数"] >= 2].copy()
    if multi.empty:
        print("  (无多基金交叉持仓)")
        return
    print(f"\n  共 {len(multi)} 只股票被2只及以上上榜基金同时重仓\n")

    # 重点标注：被多基金新增或加仓的
    hot = multi[multi["新增加仓数"] >= 2]
    if not hot.empty:
        print(f"  🔥 被多只基金新增/加仓的个股（{len(hot)}只）：")
        print(f"    {'代码':<8} {'名称':<12} {'持有':>4} {'新增加仓':>8} {'平均仓位':>8} {'详情'}")
        print(f"    {'-'*80}")
        for _, r in hot.iterrows():
            print(f"    {r['股票代码']:<8} {r['股票简称']:<12} {r['持有基金数']:>4} "
                  f"{r['新增加仓数']:>8} {r['平均仓位%']:>7.2f}%  {r['新增/加仓基金']}")

    # 全部多基金持仓
    print(f"\n  全部多基金共同持仓（{len(multi)}只）：")
    print(f"    {'代码':<8} {'名称':<12} {'持有':>4} {'新增加仓':>8} {'最大仓位':>8} {'持有基金'}")
    print(f"    {'-'*80}")
    for _, r in multi.iterrows():
        marker = " 🔥" if r["新增加仓数"] >= 2 else ""
        print(f"    {r['股票代码']:<8} {r['股票简称']:<12} {r['持有基金数']:>4} "
              f"{r['新增加仓数']:>8} {r['最大仓位%']:>7.2f}%  {r['持有基金']}{marker}")


def _print_pool_results(pool_results, top_by_period=None):
    """打印基金池全部收益率明细"""
    if not pool_results:
        return
    print(f"\n  {'代码':<10} {'名称':<24} {'净值':>8} {'近1月':>8} {'近3月':>8} {'近6月':>8} {'近1年':>8}")
    print(f"  {'-'*80}")
    for rec in pool_results:
        vals = []
        for p in PERIODS:
            v = rec.get(p)
            vals.append(f"{v:>7.2f}%" if v is not None else "    N/A ")
        nav = rec.get("最新净值")
        nav_s = f"{nav:>7.4f}" if nav else "    N/A"
        print(f"  {rec['代码']:<10} {rec['名称']:<24} {nav_s}  {'  '.join(vals)}")


# ─── 主流程 ───────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'★' * 38}")
    print(f"  场外基金收益率排行 & 持仓分析")
    print(f"  分析日期：{TODAY.strftime('%Y-%m-%d')}")
    print(f"  数据来源：{DATA_SOURCE}  ← 修改 fund/config.json 可切换")
    print(f"  基金池种子：{len(FUND_POOL)} 只（将自动扩展同类基金）")
    print(f"{'★' * 38}")

    analyze_stocks = None
    changes_df     = None
    q1_df = q4_df  = None
    pool_results   = []
    etf_top        = {}
    top_fund_details = []   # 上榜基金的持仓详情
    cross_df       = None   # 交叉持仓分析

    # ════════════════════════════════════════════════════════
    if DATA_SOURCE == "eastmoney":
        # ── Part A-1: 东方财富排行 ────────────────────────────
        section("Part A-1 · 场外基金收益率排行（东方财富，开放式基金完整排行）")
        rank_df       = em_fetch_fund_rank()
        top_by_period = em_top_funds_by_period(rank_df)

        for period, funds in top_by_period.items():
            print(f"\n【{period}】收益率前{TOP_N}（股票型+混合型）")
            print(f"  {'排':<3} {'代码':<10} {'名称':<24} {'类型':<8} {'收益率':>8}  {'规模(亿元)':>10}")
            print(f"  {'-'*68}")
            for i, f in enumerate(funds, 1):
                s = f"{f['规模(亿元)']:.2f}" if f.get("规模(亿元)") else "  N/A"
                print(f"  {i:<3} {f['代码']:<10} {f['名称']:<24} {f.get('类型',''):<8} {f['收益率']:>7.2f}%  {s:>10}")

        # ── Part A-2: 上榜基金持仓分析（HTML） ─────────────
        all_top_codes = set()
        for funds in top_by_period.values():
            for f in funds:
                all_top_codes.add(f["代码"])

        section(f"Part A-2 · 上榜基金十大重仓股（{len(all_top_codes)}只基金）")
        for code in sorted(all_top_codes):
            info = sina_parse_fund_holdings(code)
            top_fund_details.append(info)
            print(f"\n  {info['name']}({code})  经理:{info.get('manager','')}  "
                  f"持仓日期:{info.get('data_date','')}")
            if info["holdings"]:
                print(f"    {'股票代码':<8} {'股票简称':<12} {'仓位%':>6} {'较上期':>10}")
                print(f"    {'-'*45}")
                for h in info["holdings"]:
                    pct_s = f"{h['pct']:.2f}" if h.get("pct") is not None else "N/A"
                    print(f"    {h['stock_code']:<8} {h['stock_name']:<12} {pct_s:>6} {h.get('pct_change',''):>10}")
            time.sleep(0.5)

        # ── Part A-3: 交叉持仓分析 ─────────────────────────
        cross_df = cross_fund_stock_analysis(top_fund_details)
        _print_cross_analysis(cross_df)

        # ── 基金池补充对比（新浪净值API）─────────────────────
        if FUND_POOL:
            section(f"基金池补充对比（{len(FUND_POOL)} 只，新浪净值API）")
            pool_results = sina_calc_fund_pool_returns()
            _print_pool_results(pool_results, top_by_period)

        # ── Part B: 个基持仓对比（东方财富） ──────────────────
        section("Part B · Q1 个基持仓对比（东方财富）")
        changes_df = em_analyze_new_stocks(top_by_period)

    # ════════════════════════════════════════════════════════
    else:  # sina_cninfo（默认）
        # ── 基金池扩展 ─────────────────────────────────────
        section("Part A · 场外基金收益率排行（新浪净值API）")
        if not FUND_POOL:
            print("""
[提示] fund_pool 为空！请在 fund/config.json 的 "fund_pool" 中添加场外基金代码。
       示例: "fund_pool": ["011369", "022364", "000001", ...]
""")

        # 自动发现同类基金，扩展基金池
        seed_codes = [str(c).strip() for c in FUND_POOL if str(c).strip()]
        if seed_codes:
            expanded = sina_discover_fund_pool(seed_codes)
        else:
            expanded = []

        # ── Part A-1: 计算收益率并排行 ────────────────────
        pool_results = sina_calc_fund_pool_returns(fund_codes=expanded)

        # 用 HTML 页面补全基金名称
        if pool_results:
            # 只对上榜可能的基金获取名称（先按近1年排序取前 TOP_N*2 + 每个周期TOP_N）
            name_candidates = set()
            for period in PERIODS:
                sorted_by = sorted(
                    [r for r in pool_results if r.get(period) is not None],
                    key=lambda r: r[period], reverse=True)
                for r in sorted_by[:TOP_N]:
                    name_candidates.add(r["代码"])

            print(f"\n  [补全] 获取 {len(name_candidates)} 只上榜基金名称...", flush=True)
            for code in name_candidates:
                info = sina_parse_fund_holdings(code)
                if info and info["name"] != code:
                    for rec in pool_results:
                        if rec["代码"] == code:
                            rec["名称"] = info["name"]
                            # 回写缓存
                            cp = cpth(f"fast_ret_{code}.json")
                            if os.path.exists(cp):
                                with open(cp, encoding="utf-8") as f:
                                    cached = json.load(f)
                                cached["名称"] = info["name"]
                                with open(cp, "w", encoding="utf-8") as f:
                                    json.dump(cached, f, ensure_ascii=False)
                            break
                time.sleep(0.3)

        top_by_period = sina_top_funds_by_period(pool_results)

        for period, funds in top_by_period.items():
            print(f"\n【{period}】收益率前{TOP_N}（场外基金，{len(pool_results)}只中排行）")
            print(f"  {'排':<3} {'代码':<10} {'名称':<28} {'收益率':>8}")
            print(f"  {'-'*58}")
            for i, f in enumerate(funds, 1):
                print(f"  {i:<3} {f['代码']:<10} {f['名称']:<28} {f['收益率']:>7.2f}%")

        # ── Part A-2: 上榜基金十大重仓股 ─────────────────
        all_top_codes = set()
        for funds in top_by_period.values():
            for f in funds:
                all_top_codes.add(f["代码"])

        section(f"Part A-2 · 上榜基金十大重仓股（{len(all_top_codes)}只基金）")
        for code in sorted(all_top_codes):
            info = sina_parse_fund_holdings(code)
            top_fund_details.append(info)
            print(f"\n  📊 {info['name']}({code})  经理:{info.get('manager','')}  "
                  f"持仓日期:{info.get('data_date','')}")
            if info["holdings"]:
                print(f"    {'股票代码':<8} {'股票简称':<12} {'仓位%':>6} {'较上期变化':>10}")
                print(f"    {'-'*45}")
                for h in info["holdings"]:
                    pct_s = f"{h['pct']:.2f}" if h.get("pct") is not None else "N/A"
                    chg = h.get("pct_change", "")
                    # 标注新增
                    if chg == "-":
                        chg = "★新增"
                    print(f"    {h['stock_code']:<8} {h['stock_name']:<12} {pct_s:>6} {chg:>10}")
            else:
                print(f"    (无持仓数据)")
            time.sleep(0.3)

        # ── Part A-3: 交叉持仓分析 ─────────────────────────
        cross_df = cross_fund_stock_analysis(top_fund_details)
        _print_cross_analysis(cross_df)

        # ── Part A-4: ETF排行（可选） ────────────────────────
        if INCLUDE_ETF:
            section("Part A-4 · ETF收益率排行（新浪+东财历史，市值前100只ETF）")
            spot_df    = get_etf_spot()
            returns_df = calc_etf_returns(spot_df)
            etf_top    = top_etfs_by_period(returns_df)

            for period, funds in etf_top.items():
                print(f"\n【{period}】收益率前{TOP_N} ETF基金")
                print(f"  {'排':<3} {'代码':<12} {'名称':<24} {'收益率':>8}  {'规模(亿元)':>10}")
                print(f"  {'-'*65}")
                for i, f in enumerate(funds, 1):
                    s = f"{f['规模(亿元)']:.2f}" if f.get("规模(亿元)") else "  N/A"
                    print(f"  {i:<3} {f['代码']:<12} {f['名称']:<24} {f['收益率']:>7.2f}%  {s:>10}")

        # ── Part B: 重仓股对比（巨潮资讯） ──────────────────
        section("Part B · Q1 2025 vs Q4 2024 全市场基金重仓股对比（巨潮资讯）")
        q1_df = get_fund_heavy_stocks(Q1_DATE)
        q4_df = get_fund_heavy_stocks(Q4_DATE)
        print(f"\n  Q1 重仓股列表：{len(q1_df)} 只")
        print(f"  Q4 重仓股列表：{len(q4_df)} 只")
        changes_df = analyze_new_additions(q1_df, q4_df)
        new_entries = changes_df[changes_df["变化标签"] == "新进重仓"]
        big_adds    = changes_df[changes_df["变化标签"] == "大幅增持"]
        print(f"\n  新进重仓（Q4未出现，Q1新进）：{len(new_entries)} 只")
        print(f"  大幅增持（覆盖家数增加≥100家）：{len(big_adds)} 只")

    # ════════════════════════════════════════════════════════
    # Part C: 行业查询（两种模式共用）
    # ════════════════════════════════════════════════════════
    section("Part C · 新增重仓股行业分布")

    if changes_df is not None and not changes_df.empty:
        new_e = changes_df[changes_df["变化标签"] == "新进重仓"]
        big_a = changes_df[changes_df["变化标签"] == "大幅增持"]
        analyze_stocks = pd.concat([new_e, big_a], ignore_index=True).head(60)

        print(f"\n[查询] 将查询 {len(analyze_stocks)} 只股票的行业信息...", flush=True)
        industries = []
        for j, (_, row) in enumerate(analyze_stocks.iterrows(), 1):
            industries.append(get_stock_industry(row["股票代码"]))
            if j % 10 == 0:
                print(f"  已查询 {j}/{len(analyze_stocks)}", flush=True)
        analyze_stocks = analyze_stocks.copy()
        analyze_stocks["行业"] = industries

        # 新进重仓
        section(f"Q1 {Q1_DATE[:4]} 新进重仓股（Q4未出现，Q1新进入）")
        ne = analyze_stocks[analyze_stocks["变化标签"] == "新进重仓"].sort_values(
            "Q1基金覆盖家数", ascending=False)
        if ne.empty:
            print("  (无新进重仓股)")
        else:
            print(f"\n  {'代码':<8} {'名称':<14} {'Q1覆盖家数':>10} {'Q1持股市值(万)':>14}  行业")
            print(f"  {'-'*70}")
            for _, r in ne.iterrows():
                mv = f"{r['Q1持股市值(万)']:,.0f}" if r["Q1持股市值(万)"] else "N/A"
                print(f"  {r['股票代码']:<8} {r['股票简称']:<14} {r['Q1基金覆盖家数']:>10}  {mv:>14}  {r['行业']}")

        # 大幅增持
        section(f"Q1 大幅增持（覆盖家数增加≥100家，共 {len(big_a)} 只）")
        ba = analyze_stocks[analyze_stocks["变化标签"] == "大幅增持"].sort_values(
            "覆盖家数变化", ascending=False)
        if ba.empty:
            print("  (无)")
        else:
            print(f"\n  {'代码':<8} {'名称':<14} {'Q4家数':>7} {'Q1家数':>7} {'新增家数':>8}  行业")
            print(f"  {'-'*70}")
            for _, r in ba.iterrows():
                print(f"  {r['股票代码']:<8} {r['股票简称']:<14}"
                      f" {r['Q4基金覆盖家数']:>7} {r['Q1基金覆盖家数']:>7} +{r['覆盖家数变化']:>6}  {r['行业']}")

        # 行业汇总
        section("新增重仓股板块汇总（新进 + 大幅增持）")
        ind_summary = (
            analyze_stocks.groupby("行业")
            .agg(股票数量=("股票代码","count"),
                 平均Q1覆盖家数=("Q1基金覆盖家数","mean"),
                 覆盖家数总变化=("覆盖家数变化","sum"))
            .sort_values("股票数量", ascending=False)
        )
        print(f"\n  {'行业':<28} {'股票数':>6} {'平均Q1覆盖':>10} {'覆盖总变化':>10}")
        print(f"  {'-'*62}")
        for ind, r in ind_summary.iterrows():
            print(f"  {ind:<28} {int(r['股票数量']):>6} {r['平均Q1覆盖家数']:>10.0f} {r['覆盖家数总变化']:>+10.0f}")

    # ════════════════════════════════════════════════════════
    # Part D: 行业配置对比（sina_cninfo 模式）
    # ════════════════════════════════════════════════════════
    if DATA_SOURCE != "eastmoney":
        section("Part D · Q1 2025 vs Q4 2024 基金行业配置对比（巨潮资讯）")
        try:
            ia_q1 = get_industry_allocation(Q1_DATE)
            ia_q4 = get_industry_allocation(Q4_DATE)

            ia_q1 = ia_q1.copy()
            ia_q4 = ia_q4.copy()
            ia_q1["占净资产比例"] = ia_q1["占净资产比例"].apply(safe_float)
            ia_q4["占净资产比例"] = ia_q4["占净资产比例"].apply(safe_float)
            ia_q4_map = ia_q4.set_index("证监会行业名称")["占净资产比例"].to_dict()

            rows_ia = []
            for _, row in ia_q1.iterrows():
                ind_name = str(row["证监会行业名称"]).strip()
                q1_pct   = safe_float(row["占净资产比例"], 0) or 0
                q4_pct   = safe_float(ia_q4_map.get(ind_name), 0) or 0
                delta    = q1_pct - q4_pct
                arrow    = "↑↑" if delta > 0.2 else ("↑" if delta > 0.05 else ("↓" if delta < -0.05 else "→"))
                rows_ia.append((delta, ind_name, q4_pct, q1_pct, arrow))

            print(f"\n  {'行业名称':<32} {'Q4占比':>8} {'Q1占比':>8} {'变化':>8} 趋势")
            print(f"  {'-'*65}")
            for delta, ind_name, q4_pct, q1_pct, arrow in sorted(rows_ia, key=lambda x: -x[0]):
                print(f"  {ind_name:<32} {q4_pct:>7.2f}%  {q1_pct:>7.2f}%  {delta:>+7.2f}% {arrow}")
        except Exception as e:
            print(f"  [行业配置获取失败] {e}")

    # ════════════════════════════════════════════════════════
    # 导出结果
    # ════════════════════════════════════════════════════════
    section("导出结果到Excel")
    date_str = TODAY.strftime("%Y%m%d")
    out_path = os.path.join(OUTPUT_DIR, f"fund_analysis_result_{date_str}.xlsx")

    rank_rows = []
    for period, funds in top_by_period.items():
        for rank, f in enumerate(funds, 1):
            rank_rows.append({
                "时间段":     period,
                "排名":       rank,
                "代码":       f["代码"],
                "名称":       f["名称"],
                "收益率(%)":  f["收益率"],
                "规模(亿元)": f.get("规模(亿元)"),
                "基金类型":   f.get("类型", ""),
            })

    pool_sheet_rows = []
    for rec in pool_results:
        row = {"代码": rec["代码"], "名称": rec["名称"],
               "最新净值": rec.get("最新净值"), "净值日期": rec.get("最新日期")}
        for p in PERIODS:
            row[p] = rec.get(p)
        pool_sheet_rows.append(row)

    etf_rows = []
    if etf_top:
        for period, funds in etf_top.items():
            for rank, f in enumerate(funds, 1):
                etf_rows.append({
                    "时间段": period, "排名": rank,
                    "代码": f["代码"], "名称": f["名称"],
                    "收益率(%)": f["收益率"],
                    "规模(亿元)": f.get("规模(亿元)"),
                })

    # 上榜基金持仓明细
    holdings_rows = []
    for fd in top_fund_details:
        for h in fd.get("holdings", []):
            holdings_rows.append({
                "基金代码":   fd["code"],
                "基金名称":   fd["name"],
                "基金经理":   fd.get("manager", ""),
                "持仓日期":   fd.get("data_date", ""),
                "股票代码":   h["stock_code"],
                "股票简称":   h["stock_name"],
                "仓位(%)":   h.get("pct"),
                "较上期变化":  h.get("pct_change", ""),
            })

    try:
        with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
            pd.DataFrame(rank_rows).to_excel(writer, sheet_name="场外基金排行", index=False)
            if pool_sheet_rows:
                pd.DataFrame(pool_sheet_rows).to_excel(writer, sheet_name="基金池收益明细", index=False)
            if holdings_rows:
                pd.DataFrame(holdings_rows).to_excel(writer, sheet_name="上榜基金持仓", index=False)
            if cross_df is not None and not cross_df.empty:
                cross_df.to_excel(writer, sheet_name="交叉持仓分析", index=False)
            if etf_rows:
                pd.DataFrame(etf_rows).to_excel(writer, sheet_name="ETF排行", index=False)
            if q1_df is not None:
                q1_df.head(500).to_excel(writer, sheet_name="Q1重仓股TOP500", index=False)
            if q4_df is not None:
                q4_df.head(500).to_excel(writer, sheet_name="Q4重仓股TOP500", index=False)
            if analyze_stocks is not None and not analyze_stocks.empty:
                analyze_stocks.to_excel(writer, sheet_name="新增重仓分析", index=False)
            if changes_df is not None and not changes_df.empty:
                changes_df.to_excel(writer, sheet_name="全量变化", index=False)
        print(f"\n  结果已保存: {out_path}")
    except Exception as e:
        csv_path = out_path.replace(".xlsx", ".csv")
        pd.DataFrame(rank_rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"\n  [Excel失败({e})，改存CSV]: {csv_path}")

    print("\n  分析完成！")


if __name__ == "__main__":
    main()
