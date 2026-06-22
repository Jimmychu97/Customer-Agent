# Product Overview: Agent-Customer

## Purpose
电商AI客服桌面应用程序，面向拼多多商家，通过AI大模型实现智能自动客服回复，减少人工客服工作量，提升响应效率。

## Value Proposition
- 7×24小时智能客服，非工作时间自动回复客户
- 多工具调用的AI Agent，能主动查询商品信息、发送商品卡片
- 双知识库体系保证回答的准确性（产品知识库 + 客服知识库）
- 关键词识别转人工，保证复杂问题的服务质量

## Key Features

### AI Agent 能力
- 自研 Agent 框架（不依赖 Agno/LangChain），基于 OpenAI 兼容 API
- 多轮工具调用 + 会话上下文管理（Session 级别隔离）
- 5 个内置工具：商品列表查询、商品卡片发送、商品知识查询、客服知识搜索、转接人工

### 渠道集成
- 拼多多平台 WebSocket 实时消息接收
- 自动重连机制 + 心跳检测
- Cookie 管理与自动刷新（Playwright 自动化登录）

### 知识库系统
- 产品知识库：从拼多多 API 拉取商品，调用多模态 LLM 提取知识入库
- 客服知识库：支持导入 PDF/Word/Excel 等文档，jieba 中文分词检索
- 向量嵌入模型支持，语义相似度检索

### 消息处理
- 异步消息队列（asyncio）
- 处理器链模式：预处理 → 关键词判断 → AI回复 → 券码处理
- 支持高并发多会话并行处理

### 用户界面
- PyQt6 + pyqt6-fluent-widgets 现代化桌面 UI
- 实时日志面板
- 商品管理、知识库管理、关键词设置、优惠券管理等功能页

## Target Users
- 拼多多中小商家，需要自动化客服的运营人员
- 对AI客服有定制需求的技术团队

## Use Cases
1. 客户咨询商品详情 → Agent 调用 get_product_knowledge 返回准确信息
2. 客户询问退换货政策 → Agent 调用 search_customer_service_knowledge 检索知识库
3. 客户要买推荐商品 → Agent 调用 send_goods_link 发送商品卡片
4. 客户投诉或复杂问题 → 关键词触发 transfer_conversation 转人工
5. 商家配置AI指令、上传知识文档 → 通过桌面UI管理

## Version
当前版本：1.1.0
