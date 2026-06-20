import json, subprocess, sys
out = {}
try:
    r = subprocess.run([sys.executable, '-m', 'pip', '--version'], capture_output=True, text=True, timeout=20)
    out['pip'] = {'returncode': r.returncode, 'stdout': r.stdout.strip(), 'stderr': r.stderr.strip()}
except Exception as e:
    out['pip_error'] = repr(e)
print(json.dumps(out, ensure_ascii=False))
