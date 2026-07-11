# Product Overview: Agent-Customer

## Purpose
电商AI客服桌面应用程序，专为拼多多平台设计，通过 WebSocket 实时接收买家消息，集成 AI 大模型实现智能自动回复，减少人工客服工作量。

## Value Proposition
- 7×24 小时 AI 自动回复，降低人工客服成本
- 双知识库体系确保回复准确性（客服知识库 + 产品知识库）
- 双验机平台自动路由（苹果→真心验机，安卓→GSX验机）
- 支持多账号并发，消息队列异步处理高并发场景

## Key Features

### 消息接收
- 拼多多平台 WebSocket 实时消息接收
- 自动重连机制 + 心跳检测
- Playwright 自动登录/Cookie 刷新

### AI 智能回复
- 自研 Agent 框架（不依赖 Agno），多轮工具调用
- 会话上下文管理，支持多轮对话
- 工作时间控制（business_hours 配置）
- 关键词触发转人工服务

### AI Agent 工具集
| 工具 | 功能 |
|------|------|
| `search_customer_service_knowledge` | 搜索客服知识库（售前/售后/商品解答） |
| `get_product_knowledge` | 查询指定商品详细知识 |
| `get_shop_products` | 获取店铺商品列表 |
| `send_goods_link` | 向用户发送商品卡片 |
| `transfer_conversation` | 转接会话给人工客服 |
| `verify_voucher` | 核销验证 |
| `get_voucher_code` | 获取优惠券码 |

### 知识库管理
- 客服知识库：售前咨询、商品解答、售后政策
- 产品知识库：多模态 LLM 自动从拼多多 API 提取商品知识
- jieba 中文分词检索

### 验机集成
- 苹果设备：真心验机（iunlock8.com）
- 安卓设备：GSX 验机（gkdt.com）
- 核销完成后按设备类型自动路由

## Target Users
- 拼多多电商卖家（尤其是二手手机/数码设备类目）
- 需要 7×24 小时客服覆盖的中小商家
- 希望降低人工客服成本的店铺运营者

## Use Cases
1. 买家咨询商品信息 → AI 查询产品知识库自动回复
2. 买家询问售后政策 → AI 搜索客服知识库回复
3. 买家要求验机报告 → 核销后自动查询验机平台并发送报告
4. 买家要求转人工 → 关键词识别后自动转接
5. 客服主动推荐商品 → AI 获取商品列表并发送商品卡片
