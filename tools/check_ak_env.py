import json
result = {}
for name in ['akshare', 'pandas', 'requests']:
    try:
        mod = __import__(name)
        result[name] = {'ok': True, 'version': getattr(mod, '__version__', 'unknown')}
    except Exception as e:
        result[name] = {'ok': False, 'error': repr(e)}
print(json.dumps(result, ensure_ascii=False))
