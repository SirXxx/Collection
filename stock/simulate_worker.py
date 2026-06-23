import sys, threading, traceback
sys.path.insert(0, '.')
import AStockSelector_AK as core
from types import SimpleNamespace

stop_event = threading.Event()

args = SimpleNamespace(
    top_n=10, sort_by='price', ascending=True, add_codes='', include_bj=False,
    min_price=None, max_price=None, min_pct_chg=3.0, max_pct_chg=6.0,
    min_volume=None, min_volume_ratio=1.0, max_volume_ratio=5.0,
    min_turnover=4.0, max_turnover=10.0, min_market_cap=20.0, max_market_cap=200.0,
    min_pe=None, max_pe=None, max_pb=None, min_roe=None,
    spot_retries=3, spot_retry_sleep=3, low_rising=True, price_at_high=True,
    fetch_highs_lows=False, fetch_indicators=False, fetch_roe=False,
    price_above_ma5=True, price_above_ma10=False, price_above_ma20=False,
    ma5_trend_up=False, tail_30min_positive=False, data_source='sina',
)

print('Fetching spot data...')
try:
    spot_df = core.fetch_spot_data(retries=3, sleep_sec=3, include_bj=False, data_source='sina')
    src = spot_df.attrs.get('spot_source', '-')
    print(f'Got {len(spot_df)} rows, source: {src}')
    rows = spot_df.to_dict(orient='records')
    before = len(rows)
    rows = [x for x in rows if core.pass_filters(x, args)]
    rows.sort(key=lambda x: core.sort_value(x, args.sort_by), reverse=not args.ascending)
    rows = rows[:args.top_n]
    print(f'Filtered: {before} -> {len(rows)} rows')
    if rows:
        print('Calling enrich_with_recent_lows...')
        rows = core.enrich_with_recent_lows(rows, days=5, data_source='sina', stop_event=stop_event)
        print(f'Enrich done, {len(rows)} rows')
        filtered = [r for r in rows if core.pass_low_rising_filter(r)]
        print(f'pass_low_rising_filter: {len(filtered)} rows')
    print('ALL PASSED')
except Exception as e:
    print('ERROR:', e)
    traceback.print_exc()
