# -*- coding: utf-8 -*-
import json
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import akshare as ak

out = {}

try:
    spot_df = ak.stock_zh_a_spot_em()
    out['spot_ok'] = True
    out['spot_shape'] = list(spot_df.shape)
    out['spot_columns'] = list(spot_df.columns)
    out['spot_sample'] = spot_df.head(2).to_dict(orient='records')
except Exception as e:
    out['spot_ok'] = False
    out['spot_error'] = repr(e)

for date in ['20241231', '20240930', '20240630', '20240331', '20231231']:
    key = f'yjbb_{date}'
    try:
        df = ak.stock_yjbb_em(date=date)
        out[key] = {
            'ok': True,
            'shape': list(df.shape),
            'columns': list(df.columns),
            'sample': df.head(2).to_dict(orient='records')
        }
    except Exception as e:
        out[key] = {'ok': False, 'error': repr(e)}

print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
