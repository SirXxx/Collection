import json, urllib.request

headers={
    'User-Agent':'Mozilla/5.0',
    'Referer':'https://quote.eastmoney.com/'
}

def get_json(url):
    req=urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        text=r.read().decode('utf-8', errors='ignore')
    return json.loads(text)

out={}
out['realtime']=get_json('https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=3&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12,f14,f2,f3,f5,f8,f9,f10,f20,f21')
for code in ['SZ000001','SH600000']:
    try:
        req=urllib.request.Request(f'https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/MainTargetAjax?type=0&code={code}', headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            text=r.read().decode('utf-8', errors='ignore')
        out[f'finance_{code}']=json.loads(text)
    except Exception as e:
        out[f'finance_{code}_error']=repr(e)

with open('inspect_output.json','w',encoding='utf-8') as f:
    json.dump(out,f,ensure_ascii=False,indent=2)
print('ok')
