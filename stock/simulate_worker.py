import sys, threading, traceback
sys.path.insert(0, '.')
import AStockSelector_AK as core
from types import SimpleNamespace

stop_event = threading.Event()

args = SimpleNamespace(
    top_n=10, sort_by='price', ascending=True, add_codes='', include_bj=False,
    min_price=None, max_price=None, min_pct_chg=None, max_pct_chg=None,
    min_volume=None, min_volume_ratio=None, max_volume_ratio=None,
    min_turnover=None, max_turnover=None, min_market_cap=None, max_market_cap=None,
    min_pe=None, max_pe=None, max_pb=None, min_roe=None,
    spot_retries=1, spot_retry_sleep=1, low_rising=False, price_at_high=False,
    fetch_highs_lows=False, fetch_indicators=False, fetch_roe=False,
    price_above_ma5=False, price_above_ma10=False, price_above_ma20=False,
    ma5_trend_up=False, tail_30min_positive=False, data_source='sina',
)

print('=== 测试 fetch_spot_data（重试次数=1 加快测试）===')
try:
    spot_df = core.fetch_spot_data(retries=1, sleep_sec=1, include_bj=False, data_source='sina')
    src = spot_df.attrs.get('spot_source', '-')
    print(f'成功！{len(spot_df)} 行，来源: {src}')
    print(spot_df[['code','name','price','pct_chg','market_cap']].head(5).to_string())
    print('ALL PASSED')
except Exception as e:
    print('ERROR:', e)
    traceback.print_exc()

