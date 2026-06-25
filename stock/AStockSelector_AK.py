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

# ── 公司代理 SSL 兼容补丁 ──────────────────────────────────────────────────
# 公司网络通过透明代理做 HTTPS 拦截，代理根证书未被 Python 的 certifi 信任，
# 导致所有 HTTPS 请求抛出 SSLCertVerificationError。
# 在此全局关闭 SSL 验证（仅影响本进程），让代理证书可以被接受。
import ssl
import urllib3
import requests as _requests_mod

ssl._create_default_https_context = ssl._create_unverified_context  # noqa: SLF001
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_orig_session_request = _requests_mod.Session.request


def _no_verify_request(self, method, url, **kwargs):
    kwargs.setdefault("verify", False)
    return _orig_session_request(self, method, url, **kwargs)


_requests_mod.Session.request = _no_verify_request

# ── Windows SSPI 代理认证（腾讯财经接口专用）─────────────────────────────────
# 公司代理需要 Windows 集成认证（NTLM/Kerberos）。
# requests_negotiate_sspi 包利用当前登录的 Windows 凭证自动完成握手，
# 无需手动输入密码。
_SSPI_SESSION = None


def _get_sspi_session():
    global _SSPI_SESSION
    if _SSPI_SESSION is not None:
        return _SSPI_SESSION
    try:
        from requests_negotiate_sspi import HttpNegotiateAuth  # noqa: PLC0415
        sess = _requests_mod.Session()
        sess.auth = HttpNegotiateAuth()
        _SSPI_SESSION = sess
        return sess
    except ImportError:
        return None
# ─────────────────────────────────────────────────────────────────────────────

import akshare as ak

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_SOURCE_OPTIONS = ["auto", "eastmoney", "sina", "tencent"]
# auto      — 自动，依次尝试全部来源
# eastmoney — 东方财富（stock_zh_a_spot_em + push2 ulist 降级）
# sina      — 新浪财经（stock_zh_a_spot），历史K线备选网易163
# tencent   — 腾讯财经（qt.gtimg.cn HTTP + Windows SSPI），适合公司内网环境
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
    s = str(code).strip()
    # Strip exchange prefix (sh/sz/bj) if present, e.g. "sh600001" -> "600001"
    if len(s) > 6 and s[:2].isalpha() and s[2:].isdigit():
        s = s[2:]
    return s.zfill(6)


def is_a_share_code(code, include_bj=False):
    code = normalize_code(code)
    if code.startswith(A_CODE_PREFIX):
        return True
    if include_bj and code.startswith(BJ_CODE_PREFIX):
        return True
    return False


# ── 行情缓存工具 ────────────────────────────────────────────────────────────
SPOT_CACHE_DIR = os.path.join(BASE_DIR, "output", "cache")


def _save_spot_cache(df):
    """将行情数据保存到当天缓存文件。"""
    try:
        os.makedirs(SPOT_CACHE_DIR, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d")
        path = os.path.join(SPOT_CACHE_DIR, f"spot_{date_str}.csv")
        df.to_csv(path, index=False, encoding="utf-8-sig")
    except Exception:
        pass


def _load_spot_cache(max_days=3):
    """尝试加载最近 max_days 天内最新的行情缓存，返回 (df, date_str) 或 None。"""
    if not os.path.exists(SPOT_CACHE_DIR):
        return None
    from datetime import timedelta
    today = datetime.now()
    for delta in range(max_days):
        date_str = (today - timedelta(days=delta)).strftime("%Y%m%d")
        path = os.path.join(SPOT_CACHE_DIR, f"spot_{date_str}.csv")
        if os.path.exists(path):
            try:
                df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
                # 还原数值列
                for col in ("price", "pct_chg", "volume", "volume_ratio", "turnover",
                            "market_cap", "circ_market_cap", "pe", "pb",
                            "today_high", "today_low"):
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                df.attrs["spot_source"] = f"cache_{date_str}"
                return df, date_str
            except Exception:
                pass
    return None


def fetch_spot_data(retries=3, sleep_sec=3, include_bj=False, data_source="auto"):
    """使用 akshare 拉取实时行情。
    data_source: 'auto'=自动依次尝试, 'eastmoney'=东方财富优先, 'sina'=新浪财经优先。
    所有接口失败时自动降级到本地缓存（最近3天内）。
    财报数据始终来自东方财富接口。"""
    _em   = ("stock_zh_a_spot_em", getattr(ak, "stock_zh_a_spot_em", None))
    _sina = ("stock_zh_a_spot",    getattr(ak, "stock_zh_a_spot", None))
    if data_source == "tencent":
        # 直接使用腾讯财经接口（Windows SSPI），跳过其他接口
        print("数据来源: 腾讯财经（qt.gtimg.cn HTTP + Windows SSPI）")
        try:
            df = _fetch_spot_via_tencent(include_bj=include_bj)
            if df is not None and not df.empty:
                _save_spot_cache(df)
                return df
        except Exception as e:
            raise RuntimeError(
                f"腾讯财经接口失败: {e}\n"
                "请确认已安装 requests-negotiate-sspi 包（pip install requests-negotiate-sspi）"
            ) from e
    elif data_source == "eastmoney":
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
                result = standardize_spot_df(df, source=func_name, include_bj=include_bj)
                _save_spot_cache(result)
                return result
            except Exception as e:
                last_err = e
                attempts.append(f"{func_name} 第{i}次失败: {repr(e)}")
                time.sleep(sleep_sec)

    # 降级方案一：直接通过 push2 ulist.np/get 批量查询（所有模式均尝试）
    print("所选行情接口均不可用，尝试降级方案一: push2 ulist.np/get 批量查询...")
    for ulist_try in range(1, retries + 1):
        try:
            print(f"  ulist.np 尝试 第 {ulist_try}/{retries} 次...")
            df = _fetch_spot_via_ulist(include_bj=include_bj)
            if df is not None and not df.empty:
                _save_spot_cache(df)
                return df
        except Exception as e:
            last_err = e
            attempts.append(f"ulist.np 第{ulist_try}次失败: {repr(e)}")
            if ulist_try < retries:
                time.sleep(sleep_sec)

    # 降级方案二：腾讯财经 HTTP 接口（使用 Windows SSPI 认证，适合公司代理环境）
    print("尝试降级方案二: 腾讯财经 qt.gtimg.cn（Windows SSPI 认证）...")
    try:
        df = _fetch_spot_via_tencent(include_bj=include_bj)
        if df is not None and not df.empty:
            _save_spot_cache(df)
            return df
    except Exception as e:
        last_err = e
        attempts.append(f"腾讯财经失败: {repr(e)}")

    # 降级方案三：加载本地缓存（最近3天内）
    cached = _load_spot_cache(max_days=3)
    if cached is not None:
        cache_df, cache_date = cached
        print(f"⚠ 所有在线接口不可用，已加载本地缓存行情（{cache_date}），数据可能不是最新。")
        return cache_df

    msg = "\n".join(attempts[-10:]) if attempts else repr(last_err)
    source_hint = {"eastmoney": "东方财富", "sina": "新浪财经", "auto": "全部来源"}.get(data_source, data_source)
    raise RuntimeError(
        f"实时行情接口均不可用（数据来源：{source_hint}），且无本地缓存可用。\n"
        "可能原因：\n"
        "  1. 当前网络环境无法访问所选数据源\n"
        "  2. 服务器临时故障或接口变更\n"
        "建议：切换数据来源，或等待网络恢复后重试。\n"
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


def _fetch_spot_via_tencent(include_bj=False, batch_size=100):
    """通过腾讯财经 qt.gtimg.cn HTTP 接口批量拉取 A 股实时行情。
    使用 Windows SSPI 认证（requests_negotiate_sspi）绕过公司代理认证。
    字段映射（88字段格式）：
      [1]=名称, [2]=代码, [3]=现价, [4]=昨收, [6]=成交量(手),
      [31]=涨跌额, [39]=PE, [44]=总市值(亿), [45]=流通市值(亿), [46]=PB,
      [47]=涨停价, [48]=跌停价
    涨跌幅由 (现价-昨收)/昨收*100 计算。
    今日最高取自 f[35].split('/')[0]，今日最低在交易前为0（忽略）。
    """
    session = _get_sspi_session()
    if session is None:
        raise RuntimeError(
            "腾讯财经接口需要 requests_negotiate_sspi 包提供 Windows SSPI 认证。\n"
            "请运行：pip install requests-negotiate-sspi"
        )

    # 生成全部 A 股代码（腾讯格式：sh/sz + 6位数字）
    codes = []
    for prefix in ["600", "601", "603", "605", "688", "689"]:
        for i in range(1000):
            codes.append(f"sh{prefix}{i:03d}")
    for prefix in ["000", "001", "002", "003", "300", "301"]:
        for i in range(1000):
            codes.append(f"sz{prefix}{i:03d}")
    if include_bj:
        for prefix in ["430", "830", "831", "832", "833", "834", "835",
                        "836", "837", "838", "839", "870", "871", "872",
                        "873", "874", "875", "876", "877", "878", "879"]:
            for i in range(1000):
                codes.append(f"bj{prefix}{i:03d}")

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    records = []
    total_batches = math.ceil(len(codes) / batch_size)

    for batch_idx in range(total_batches):
        batch = codes[batch_idx * batch_size: (batch_idx + 1) * batch_size]
        url = "http://qt.gtimg.cn/q=" + ",".join(batch)
        try:
            r = session.get(url, timeout=10, headers=headers, verify=False)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            r.encoding = "gbk"
            for seg in r.text.strip().split(";"):
                seg = seg.strip()
                if not seg or "=" not in seg or not seg.startswith("v_"):
                    continue
                val = seg.split("=", 1)[1].strip('"')
                f = val.split("~")
                if len(f) < 45:
                    continue
                price = safe_float(f[3])
                if not price or price <= 0:
                    continue  # 未上市/停牌
                close_prev = safe_float(f[4])
                pct_chg = round((price - close_prev) / close_prev * 100, 2) if close_prev else None
                # 今日最高：f[35] 格式为 "high/vol/?" 或 "0.00"
                raw_high = f[35] if len(f) > 35 else ""
                today_high = safe_float(raw_high.split("/")[0]) if "/" in raw_high else safe_float(raw_high)
                if today_high == 0:
                    today_high = None
                records.append({
                    "code":           normalize_code(f[2]),
                    "name":           f[1],
                    "price":          price,
                    "pct_chg":        pct_chg,
                    "volume":         safe_float(f[6]),           # 手
                    "today_high":     today_high,
                    "pe":             safe_float(f[39]),           # 市盈率
                    "market_cap":     safe_float(f[44]),           # 总市值(亿元)
                    "circ_market_cap":safe_float(f[45]),           # 流通市值(亿元)
                    "pb":             safe_float(f[46]),           # 市净率
                })
        except Exception:
            continue
        time.sleep(random.uniform(0.05, 0.15))

    if not records:
        raise RuntimeError("腾讯财经 API 未获取到任何有效行情数据（可能被代理拦截）")

    print(f"腾讯财经成功获取 {len(records)} 只股票行情")
    df = pd.DataFrame(records)
    # 过滤 A 股代码（已 normalize）
    df = df[df["code"].apply(lambda c: is_a_share_code(c, include_bj=include_bj))]
    df.attrs["spot_source"] = "tencent_qq"
    return df



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
        "today_high": pick("最高", "今日最高"),
        "today_low": pick("最低", "今日最低"),
        "pb": pick("市净率"),
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

    for col in ["price", "pct_chg", "volume", "volume_ratio", "turnover", "pe",
                 "market_cap", "circ_market_cap", "today_high", "today_low", "pb"]:
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


def fetch_recent_highs_lows(code, days=5, data_source="auto"):
    """获取股票最近 days 个交易日的最高价和最低价。
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
                elif cs in ("最高", "最高价"):
                    col_map[c] = "high"
            df = df.rename(columns=col_map)
            if "date" not in df.columns:
                continue
            keep = ["date"] + [c for c in ("low", "high") if c in df.columns]
            if len(keep) < 2:
                continue
            df = df[keep].copy()
            df["date"] = df["date"].astype(str).str[:10]
            for col in ("low", "high"):
                if col in df.columns:
                    df[col] = df[col].map(safe_float)
            df = df.tail(days)
            return df.to_dict(orient="records")
        except Exception:
            continue
    return []


def fetch_recent_lows(code, days=5, data_source="auto"):
    """向后兼容包装，调用 fetch_recent_highs_lows。"""
    return fetch_recent_highs_lows(code, days=days, data_source=data_source)


def enrich_with_recent_lows(rows, days=5, max_workers=5, data_source="auto", stop_event=None):
    """为筛选结果批量补充近 days 个交易日最低价和最高价（low_1/high_1 最旧，low_N/high_N 最新），并发获取。
    stop_event: threading.Event，设置后提前终止并发拉取。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _worker(row):
        if stop_event and stop_event.is_set():
            return row
        code = row.get("code", "")
        records = fetch_recent_highs_lows(code, days=days, data_source=data_source)
        padded = [None] * (days - len(records)) + records
        for idx, entry in enumerate(padded, start=1):
            if entry:
                row[f"low_{idx}"]       = entry.get("low")
                row[f"low_date_{idx}"]  = entry.get("date", "")
                row[f"high_{idx}"]      = entry.get("high")
                row[f"high_date_{idx}"] = entry.get("date", "")
            else:
                row[f"low_{idx}"]       = None
                row[f"low_date_{idx}"]  = ""
                row[f"high_{idx}"]      = None
                row[f"high_date_{idx}"] = ""
        return row

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_worker, row) for row in rows]
        for f in as_completed(futures):
            if stop_event and stop_event.is_set():
                break
            try:
                f.result()
            except Exception:
                pass
    return rows


def pass_low_rising_filter(item):
    """检验近3天最低价是否单调递增 (low_3 < low_4 < low_5)。
    若数据完全未获取（low_1~5 均为 None，说明 K 线 API 不可用），返回 True（跳过过滤）。"""
    # 判断是否完全没有获取到数据
    any_low = any(item.get(f"low_{i}") is not None for i in range(1, 6))
    if not any_low:
        return True   # K 线 API 不可用，跳过该过滤
    v3 = safe_float(item.get("low_3"))
    v4 = safe_float(item.get("low_4"))
    v5 = safe_float(item.get("low_5"))
    if v3 is None or v4 is None or v5 is None:
        return False  # 数据部分缺失（不足5天），视为不满足
    return v3 < v4 < v5


def enrich_with_indicators(rows, fetch_tail=True, fetch_roe=False,
                           max_workers=5, progress_cb=None):
    """为筛选结果批量补充技术指标（MA、尾盘、高低点、ROE等）。
    需要 indicators.py 模块，每只股票约需 0.5-1s 的 API 请求时间。"""
    try:
        import indicators as ind
        ind.clear_cache()
        rows = ind.enrich_indicators(
            rows, fetch_tail=fetch_tail, fetch_roe=fetch_roe,
            max_workers=max_workers, progress_cb=progress_cb
        )
    except ImportError:
        pass   # indicators.py 不存在时静默跳过
    return rows


def pass_filters(item, args):
    def ge(v, threshold):
        return True if threshold is None else (v is not None and v >= threshold)

    def le(v, threshold):
        return True if threshold is None else (v is not None and v <= threshold)

    def flag_check(v, required):
        """required=True 时要求 v==1（price above MA）。
        若 v 为 None（指标未获取），则跳过该过滤条件（不淘汰股票）。"""
        if not required:
            return True
        if v is None:   # 指标未获取 → 不过滤
            return True
        return v == 1

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
        # 价格触及今日最高（当前价 ≥ 今日最高×99%）
        (True if not getattr(args, "price_at_high", False)
         else (item.get("price") is not None and item.get("today_high") is not None
               and safe_float(item["price"], 0) >= safe_float(item["today_high"], 0) * 0.99)),
        # 均线过滤（仅在已丰富指标后生效）
        flag_check(item.get("above_ma5"),  getattr(args, "price_above_ma5",  False)),
        flag_check(item.get("above_ma10"), getattr(args, "price_above_ma10", False)),
        flag_check(item.get("above_ma20"), getattr(args, "price_above_ma20", False)),
        # MA5 趋势（未获取时跳过）
        (True if not getattr(args, "ma5_trend_up", False)
         else (item.get("ma5_trend") is None or item.get("ma5_trend") == "up")),
        # 尾盘过滤（未获取时跳过）
        (True if not getattr(args, "tail_30min_positive", False)
         else (item.get("tail_30min_pct") is None or item["tail_30min_pct"] > 0)),
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
        ("PB", 8),
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
            format_num(x.get("pb"), 2).rjust(8),
        ]
        manual_mark = " [手动]" if x.get("_manual") else ""
        print(" ".join(vals) + manual_mark)


def export_csv(rows, path):
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=[
            "code", "name", "price", "pct_chg", "volume", "volume_ratio", "turnover",
            "market_cap", "circ_market_cap", "pe", "pb",
        ])
    # 不导出内部标记列
    if "_manual" in df.columns:
        df = df.drop(columns=["_manual"])
    df.to_csv(path, index=False, encoding="utf-8-sig")


def build_parser():
    p = argparse.ArgumentParser(description="基于 akshare 的 A 股筛选脚本")
    p.add_argument("--top-n", type=int, default=20, help="输出前 N 只股票")
    p.add_argument("--sort-by", type=str, default="pct_chg",
                   choices=["price", "pct_chg", "volume", "volume_ratio", "turnover",
                            "market_cap", "circ_market_cap", "pe", "pb", "roe",
                            "ma5", "ma10", "ma20", "tail_30min_pct"],
                   help="排序字段")
    p.add_argument("--ascending", action="store_true", help="升序排序，默认降序")
    p.add_argument("--csv", type=str, default="", help="自定义导出 CSV 文件名")
    p.add_argument("--add-codes", type=str, default="",
                   help="主动添加的股票代码（逗号分隔，如 000001,600519），跳过筛选和 TOP-N 限制直接附加")
    p.add_argument("--include-bj", action="store_true", help="包含北交所股票")

    # 行情条件
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
    p.add_argument("--max-pb", type=float, help="最大市净率")
    p.add_argument("--min-roe", type=float, help="最小 ROE%%（需开启 --fetch-indicators）")

    # 技术指标过滤（需开启 --fetch-indicators）
    p.add_argument("--fetch-indicators", action="store_true",
                   help="为筛选结果补充技术指标（MA、尾盘、近期高低点等），较慢")
    p.add_argument("--fetch-roe", action="store_true",
                   help="同时获取 ROE（需要 --fetch-indicators，更慢）")
    p.add_argument("--price-above-ma5",  action="store_true", help="价格在 MA5 上方")
    p.add_argument("--price-above-ma10", action="store_true", help="价格在 MA10 上方")
    p.add_argument("--price-above-ma20", action="store_true", help="价格在 MA20 上方")
    p.add_argument("--ma5-trend-up", action="store_true", help="MA5 趋势向上")
    p.add_argument("--tail-30min-positive", action="store_true",
                   help="尾盘30分钟涨跌幅为正（需 --fetch-indicators）")

    p.add_argument("--spot-retries", type=int, default=3)
    p.add_argument("--spot-retry-sleep", type=int, default=3)
    p.add_argument("--low-rising", action="store_true",
                   help="补充近5日最低价并只保留近3天最低价单调递增的股票")
    p.add_argument("--data-source", type=str, default="auto",
                   choices=DATA_SOURCE_OPTIONS,
                   help="数据来源: auto=自动, eastmoney=东方财富, sina=新浪财经")
    return p


def realtime_mode(args):
    data_source = getattr(args, "data_source", "auto")
    print(f"开始拉取 akshare 实时行情（数据来源: {data_source}）...")
    spot_df = fetch_spot_data(retries=args.spot_retries, sleep_sec=args.spot_retry_sleep,
                              include_bj=args.include_bj, data_source=data_source)
    print(f"实时行情拉取完成，共 {len(spot_df)} 只股票；来源: {spot_df.attrs.get('spot_source', '-')}")

    rows = spot_df.to_dict(orient="records")
    rows = [x for x in rows if pass_filters(x, args)]
    rows.sort(key=lambda x: sort_value(x, args.sort_by), reverse=not args.ascending)
    rows = rows[:args.top_n]

    # 主动添加的股票代码（跳过筛选）
    add_codes_str = getattr(args, "add_codes", "") or ""
    if add_codes_str.strip():
        add_codes = [c.strip().zfill(6) for c in add_codes_str.split(",") if c.strip()]
        existing_codes = {r["code"] for r in rows}
        added = spot_df[spot_df["code"].isin(add_codes)].to_dict(orient="records")
        for r in added:
            if r["code"] not in existing_codes:
                r["_manual"] = True
                rows.append(r)
                existing_codes.add(r["code"])
        not_found = [c for c in add_codes if c not in {r["code"] for r in rows}]
        if not_found:
            print(f"注意：以下主动添加的股票未在行情中找到：{not_found}")
        print(f"主动添加 {len([r for r in rows if r.get('_manual')])} 只股票")

    if getattr(args, "low_rising", False):
        print(f"正在补充 {len(rows)} 只股票近5日最低价（并发请求）...")
        rows = enrich_with_recent_lows(rows, days=5, data_source=getattr(args, "data_source", "auto"))
        before_low = len(rows)
        rows = [r for r in rows if pass_low_rising_filter(r)]
        skipped = sum(1 for r in rows if not any(r.get(f"low_{i}") is not None for i in range(1, 6)))
        if skipped == before_low:
            print(f"警告：K 线接口不可用，近3天最低价递增过滤已自动跳过")
        else:
            print(f"近3天最低价递增筛选后剩余 {len(rows)}/{before_low} 只")

    if getattr(args, "fetch_indicators", False):
        print(f"正在获取 {len(rows)} 只股票技术指标（MA、尾盘、高低点等）...")
        fetch_roe = getattr(args, "fetch_roe", False)
        rows = enrich_with_indicators(rows, fetch_tail=True, fetch_roe=fetch_roe,
                                      progress_cb=lambda c, t, code: print(f"  [{c}/{t}] {code}"))
        rows = [x for x in rows if pass_filters(x, args)]
        print(f"指标过滤后剩余 {len(rows)} 只")

    print()
    print(f"最终选出 {len(rows)} 只股票：")
    print_table(rows)

    out_dir = os.path.join(BASE_DIR, "output")
    os.makedirs(out_dir, exist_ok=True)
    csv_name = args.csv.strip() if args.csv else f"a_stock_selected_ak_{now_str()}.csv"
    csv_path = os.path.join(out_dir, csv_name)
    export_csv(rows, csv_path)
    print()
    print(f"结果已导出到: {csv_path}")


def main():
    args = build_parser().parse_args()
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
