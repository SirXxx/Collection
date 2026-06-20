import urllib.request

urls = [
    'https://qt.gtimg.cn/q=sh600000,sz000001',
    'https://hq.sinajs.cn/list=sh600000,sz000001',
]
headers = {'User-Agent':'Mozilla/5.0','Referer':'https://finance.qq.com/'}
for url in urls:
    print('URL=', url)
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode('gbk', errors='ignore')
        print('OK', text[:400])
    except Exception as e:
        print('ERR', repr(e))
