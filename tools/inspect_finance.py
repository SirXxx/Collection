import urllib.request, json
headers={'User-Agent':'Mozilla/5.0','Referer':'https://quote.eastmoney.com/'}
for code in ['SZ000001','SH600000']:
    url=f'https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/MainTargetAjax?type=0&code={code}'
    req=urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        text=r.read().decode('utf-8-sig', errors='ignore')
    data=json.loads(text)
    with open(f'finance_{code}.json','w',encoding='utf-8') as f:
        json.dump(data[:3] if isinstance(data,list) else data, f, ensure_ascii=False, indent=2)
print('ok')
