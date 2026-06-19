"""临时脚本：测试 GoodsCommitAPI"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from Channel.pinduoduo.utils.API.goods_commit_api import GoodsCommitAPI

SHOP_ID = "294700238"
USER_ID = "91690729"
GOODS_ID = 282962911793

api = GoodsCommitAPI(shop_id=SHOP_ID, user_id=USER_ID)
print("anti_content:", api.cookies.get('anti_content') or api.cookies.get('anti-content', 'MISSING'))

url = "https://mms.pinduoduo.com/glide/v2/mms/query/commit/detail"
raw = api.post(url, json_data={"goods_id": GOODS_ID}, headers=api._build_headers())
print(json.dumps(raw, ensure_ascii=False, indent=2) if raw else "None")
