"""
voucher_handler 的拆分模块。

- parsers  纯文本解析（IMEI / 序列号 / 卡券号 / 订单号）
- intents  意图判定（确认 / 重发 / 问订单）
- state    跨消息的进程内状态与锁（全进程单实例）
- texts    对客话术与结果常量
- modes    服务模式取值
- io_exec  平台 IO 线程池
- report   验机报告结果格式化
"""
