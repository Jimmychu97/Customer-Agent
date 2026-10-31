"""
对客话术与结果常量。

改文案只需改这里，不碰流程代码。
"""

# 核销结果常量
_VOUCHER_PENDING = "pending"   # 信息不完整，引导补充

_VOUCHER_SUCCESS = "success"   # 核销成功

_VOUCHER_FAILED  = "failed"    # 核销失败（API返回失败）

_VOUCHER_ERROR   = "error"     # 处理异常

# 发货（无物流发货）结果常量与对客户安全的文案
_SHIP_SUCCESS, _SHIP_FAILED, _SHIP_ERROR = "success", "failed", "error"

_SHIP_SERIAL_GUIDE = ("\n\n本商品拍下后只需提供手机序列号/IMEI：\n\n"
                      "📱 IMEI码（推荐）\n拨号界面输入 *#06# 即可显示\n\n"
                      "🔢 序列号\n设置 → 通用 → 关于本机 → 序列号")

_SHIP_NEED_ORDER_TEXT = "请把订单卡片发我（点击聊天框右下角「+」→ 选择「订单」→ 发送订单卡片），我按订单帮您发货～"

_SHIP_ALREADY_DONE_TEXT = "该订单已发货，无需重复操作"

_SHIP_NOT_CONFIGURED_TEXT = "发货功能尚未配置完成，请联系人工处理"

_UPSELL_HINT = (
    "\n\n💡 想查这台机器的更多项目（整机深度报告 / 网络锁 / 激活保修等），或还有别的机器要查，"
    "都可以再拍一单，把序列号/IMEI 发我，我优先给您出报告～🙏"
)

_SHIP_IN_FLIGHT_TEXT = "⏳ 正在为您发货，请稍候，无需重复提交"

# 报告发完后的固定话术（含催确认收货）
_FIXED_REVIEW_TEXT = (
    "亲，五行好平+关注+收藏+截图+文字好平赏个。以后有关于这部机器的验机问题，都可以随时联系美高技术员，技术员将为您解答！非常感谢您选择我们,祝你购机愉快~\n"
    "亲，请确认收货"
)

_REPORT_ALREADY_SENT_TEXT = (
    "这台机器的验机报告我刚发过啦～往上翻一下就能看到🙏\n"
    "没收到的话回复「重发」，我再发一次。"
)
