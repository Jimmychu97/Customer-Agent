# Product Overview: Agent-Customer

## Purpose
电商AI客服桌面应用程序，专为拼多多平台商家设计。通过 WebSocket 实时接收买家消息，结合 AI 大模型实现智能自动回复，并集成验机报告查询（苹果/安卓双平台）和核销流程。

## Value Proposition
- 7×24 小时 AI 自动回复，减少人工客服工作量
- 双知识库体系确保回复准确性（客服知识库 + 产品知识库）
- 验机报告自动查询并发送给用户，提升售后效率
- 支持多账号并发，适合多店铺运营

## Key Features

### 核心功能
- **实时消息接收**：拼多多平台 WebSocket 长连接，支持断线自动重连和心跳检测
- **AI 智能回复**：自研 Agent 框架，多轮工具调用 + 会话上下文管理，不依赖第三方 Agent 库
- **双知识库**：客服知识库（售前/售后/商品解答）+ 产品知识库（多模态 LLM 提取）
- **商品知识自动同步**：从拼多多 API 拉取商品列表，调用视觉 LLM 提取产品知识入库

### 验机集成
- **苹果设备**：真心验机平台（iunlock8.com），通过 `zhenxin_api.apikey` 配置
- **安卓设备**：GSX 验机平台（gkdt.com），通过 `gsx.appid` + `gsx.secret` 配置
- 核销完成后按商品「设备类型」字段自动路由到对应验机平台

### AI Agent 工具集
| 工具 | 功能 |
|------|------|
| `search_customer_service_knowledge` | 搜索客服知识库 |
| `get_product_knowledge` | 查询商品详细知识 |
| `get_shop_products` | 获取店铺商品列表 |
| `send_goods_link` | 向用户发送商品卡片 |
| `transfer_conversation` | 转接人工客服 |

### 辅助功能
- **关键词转人工**：自动识别用户意图，关键词触发转人工
- **消息队列处理**：异步消息队列 + 处理器链，支持高并发
- **图片回复**：收到图片时自动回复引导文字（可配置）
- **营业时间控制**：可配置 AI 自动回复的工作时间段

## Target Users
- 拼多多平台中小商家，尤其是经营二手手机/数码设备的店铺
- 需要 7×24 小时客服覆盖但人力有限的商家
- 需要自动化验机报告查询流程的商家

## Supported Platforms
- 渠道：拼多多（Pinduoduo）
- 操作系统：Windows
- LLM：任何 OpenAI 兼容接口（DeepSeek、豆包、Kimi 等）
