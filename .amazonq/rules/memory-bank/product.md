# Product Overview: Agent-Customer

## Purpose
电商AI客服桌面应用程序，专为拼多多平台商家设计。通过 WebSocket 实时接收买家消息，结合 AI 大模型实现智能自动回复，减少人工客服工作量。

## Value Proposition
- 7×24 小时 AI 自动回复，降低人工客服成本
- 双知识库体系确保回复准确性（客服知识库 + 产品知识库）
- 双验机平台自动路由（苹果→真心验机，安卓→GSX验机），核销后自动发送验机报告
- 支持任意 OpenAI 兼容 LLM 接口，灵活切换模型

## Key Features

### 消息接收
- 拼多多平台 WebSocket 实时消息接收
- 自动重连机制 + 心跳检测
- Playwright 自动化登录/Cookie 刷新

### AI 智能回复
- 自研 Agent 框架（不依赖 Agno 等第三方框架）
- 多轮工具调用 + 会话上下文管理
- 支持 DeepSeek、豆包、Kimi 等 OpenAI 兼容接口

### AI Agent 工具集
| 工具 | 功能 |
|------|------|
| `search_customer_service_knowledge` | 搜索客服知识库（售前/售后/商品解答） |
| `get_product_knowledge` | 查询商品详细知识（成分/规格/用法/价格） |
| `get_shop_products` | 获取店铺商品列表 |
| `send_goods_link` | 向用户发送商品卡片 |
| `transfer_conversation` | 转接人工客服 |

### 验机平台集成
- 苹果设备 → 真心验机（iunlock8.com）
- 安卓设备 → GSX 验机（gkdt.com）
- 核销完成后自动查询报告并生成简析发送给用户

### 知识库管理
- 客服知识库：售前咨询、商品说明、售后政策
- 产品知识库：从拼多多 API 拉取商品，调用多模态 LLM 提取产品知识入库
- 基于 jieba 中文分词的知识检索

### 其他功能
- 关键词触发转人工服务
- 异步消息队列 + 处理器链（高并发支持）
- 商品 SKU 同步管理
- 营业时间配置（默认 08:00-23:00）

## Target Users
- 拼多多平台中小商家
- 需要降低客服人力成本的电商运营团队
- 销售二手手机/数码设备的商家（验机功能）

## Supported Platforms
- Windows 操作系统（桌面应用）
- Python >= 3.11
- 拼多多平台（WebSocket 接入）
