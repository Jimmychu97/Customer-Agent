# -*- coding: utf-8 -*-
import hashlib, sys, os
os.environ['PYTHONIOENCODING'] = 'utf-8'

appid  = 'ac926292463764'
secret = 'svXHWijleKXkMbZ5qF1p4ddb5v2XYZZX'
code   = 'VQ627F3QQH'
key    = '10127'
style  = '11'
ts     = '1782107221'
expected = '44c20d369c9f8986cc2c8e378c300865'

params = {'appid': appid, 'code': code, 'key': key, 'style': style, 'time': ts}
sorted_str = '&'.join(f'{k}={v}' for k, v in sorted(params.items()) if v)
sign_raw   = sorted_str + f'&secret={secret}'
computed   = hashlib.md5(sign_raw.encode('utf-8')).hexdigest()

print("sign_raw :", sign_raw)
print("computed :", computed)
print("expected :", expected)
print("match    :", computed == expected)
print()

sys.path.insert(0, r'c:\Works\Customer-Agent')
from Channel.pinduoduo.utils.API.gsx_api import query_gsx

result = query_gsx(code='VQ627F3QQH', key='10127')
print("query_gsx result:", result)
