import re
import requests

url = 'https://www.nppa.gov.cn/images/srchTcdw.js'
r = requests.get(url, timeout=30, headers={'User-Agent':'Mozilla/5.0'})
r.raise_for_status()
r.encoding = r.apparent_encoding or r.encoding or 'utf-8'
t = r.text

keys = ['$.ajax', 'jQuery.ajax', 'type:', 'url:', 'success:', 'search.jsp', 'search.do', 'json']
spans = []
for kw in keys:
    for m in re.finditer(re.escape(kw), t, re.I):
        start = max(0, m.start()-400)
        end = min(len(t), m.end()+1200)
        spans.append((kw, t[start:end]))

m = re.search(r'function\s+doSearch\s*\(atype,\s*page\)\s*\{', t)
if m:
    start = m.start()
    end = min(len(t), start + 9000)
    spans.insert(0, ('doSearch', t[start:end]))

with open('extract_srchtcdw_details.txt', 'w', encoding='utf-8') as f:
    for i, (kw, s) in enumerate(spans, 1):
        f.write(f'===== {i} {kw} =====\n')
        f.write(s)
        f.write('\n\n')
print('written extract_srchtcdw_details.txt')
