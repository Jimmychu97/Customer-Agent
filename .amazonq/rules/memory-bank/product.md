# Product Overview: Agent-Customer

## Purpose
电商AI客服桌面应用程序，专为拼多多平台设计。通过 WebSocket 实时接收买家消息，集成 AI 大模型实现智能自动回复，减少人工客服工作量。

## Value Proposition
- 7×24 小时 AI 自动回复，降低人工客服成本
- 双知识库体系确保回复准确性（客服知识库 + 产品知识库）
- GSX 验机报告自动查询与发送，提升售后效率
- 支持多账号并发，适合中大型电商卖家

## Key Features

### 核心功能
- **实时消息接收**：拼多多平台 WebSocket 连接，支持断线自动重连和心跳检测
- **AI 智能回复**：自研 Agent 框架，多轮工具调用 + 会话上下文管理
- **双知识库**：客服知识库（售前/售后/商品解答）+ 产品知识库（LLM多模态提取）
- **商品知识自动同步**：从拼多多 API 拉取商品，调用多模态 LLM 提取产品知识入库
- **GSX 验机查询**：核销完成后自动调用 GSX 接口查询验机报告并生成简析
- **关键词转人工**：自动识别用户意图，关键词触发转人工服务
- **AI 主动推荐**：客服代理可主动获取商品列表、发送商品卡片给用户

### AI Agent 工具集
| 工具 | 功能 |
|------|------|
| `search_customer_service_knowledge` | 搜索客服知识库（售前咨询、商品解答、售后政策） |
| `get_product_knowledge` | 查询指定商品详细知识（成分、规格、用法、价格） |
| `get_shop_products` | 获取店铺商品列表 |
| `send_goods_link` | 向用户发送商品卡片链接 |
| `transfer_conversation` | 转接会话给人工客服 |

### 消息处理链
1. **预处理器**（preprocessor）：消息过滤、格式化
2. **关键词处理器**（keyword_handler）：关键词匹配转人工
3. **核销处理器**（voucher_handler）：卡券核销 + GSX 验机
4. **AI 处理器**（ai_handler）：调用 Agent 生成回复

## Target Users
- 拼多多平台中大型电商卖家
- 需要 7×24 小时客服覆盖的店铺
- 销售需要验机服务商品（如二手手机）的卖家

## Supported Platforms
- LLM：任何 OpenAI 兼容接口（DeepSeek、豆包、Kimi 等）
- 操作系统：Windows
- 电商平台：拼多多（Pinduoduo）

## Version
当前版本：1.1.0
