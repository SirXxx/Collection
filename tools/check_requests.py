import sys
try:
    import requests
    print('IMPORT_OK')
    try:
        r = requests.get('https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=3&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12,f14,f2,f3,f5,f8,f9,f10,f20,f21', headers={'User-Agent':'Mozilla/5.0','Referer':'https://quote.eastmoney.com/'}, timeout=20)
        print('STATUS', r.status_code)
        print(r.text[:200])
    except Exception as e:
        print('REQ_ERR', repr(e))
except Exception as e:
    print('IMPORT_ERR', repr(e))
