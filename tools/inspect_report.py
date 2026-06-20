import urllib.request, urllib.parse
url = 'https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_LICO_FN_CPD&columns=SECURITY_CODE,SECURITY_NAME_ABBR,NOTICE_DATE,REPORTDATE,BASIC_EPS,TOTAL_OPERATE_INCOME,PARENT_NETPROFIT,YSTZ,SJLTZ&filter=' + urllib.parse.quote('(SECURITY_CODE="000001")') + '&pageNumber=1&pageSize=1&sortTypes=-1&sortColumns=NOTICE_DATE&source=HSF10&client=PC'
req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0','Referer':'https://quote.eastmoney.com/'})
with urllib.request.urlopen(req, timeout=20) as r:
    text = r.read().decode('utf-8', errors='ignore')
open('report_out.txt','w',encoding='utf-8').write(text)
print('ok')
