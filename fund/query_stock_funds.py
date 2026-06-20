# -*- coding: utf-8 -*-
"""
查询某只个股被哪些基金持有（东方财富网-基金持仓明细）。
用法：
  python query_stock_funds.py 002015
  python query_stock_funds.py 002015 20241231
"""
import sys
import json

# ── SSL 配置（沿用主脚本：公司自签证书环境关闭校验） ──
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from requests.adapters import HTTPAdapter

_orig_send = HTTPAdapter.send
def _patched_send(self, request, **kwargs):
    kwargs["verify"] = False
    return _orig_send(self, request, **kwargs)
HTTPAdapter.send = _patched_send

import akshare as ak
import pandas as pd

pd.set_option("display.max_rows", None)
pd.set_option("display.width", 200)
pd.set_option("display.unicode.east_asian_width", True)


def query(stock_code):
    print(f"查询股票 {stock_code} 的基金持股（新浪财经-股本股东-基金持股）...\n")
    df = ak.stock_fund_stock_holder(symbol=stock_code)
    if df is None or df.empty:
        print("未查询到数据（该股票暂无披露的基金持股，或接口未更新）。")
        return
    print(f"共 {len(df)} 条基金持股记录：\n")
    print(df.to_string(index=False))
    out = f"output/funds_holding_{stock_code}.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n已保存：{out}")


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "002015"
    query(code)
