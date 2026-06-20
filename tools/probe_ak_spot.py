# -*- coding: utf-8 -*-
import json
import sys
import time

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import akshare as ak

out = {}

for name in ["stock_zh_a_spot_em", "stock_zh_a_spot"]:
    fn = getattr(ak, name, None)
    if fn is None:
        out[name] = {"exists": False}
        continue
    result = {"exists": True, "tries": []}
    for i in range(3):
        try:
            df = fn()
            result["ok"] = True
            result["shape"] = list(df.shape)
            result["columns"] = list(df.columns)
            result["sample"] = df.head(2).to_dict(orient='records')
            break
        except Exception as e:
            result["tries"].append(repr(e))
            time.sleep(2)
    else:
        result["ok"] = False
    out[name] = result

print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
