# Product Overview: Agent-Customer

## Purpose & Value Proposition

Agent-Customer is an AI-powered e-commerce customer service desktop application built with PyQt6. It targets Chinese e-commerce operators on the Pinduoduo (PDD) platform, automating customer interactions via AI to reduce manual support load while maintaining response quality.

## Key Features

- **Real-time Messaging**: WebSocket integration with Pinduoduo platform for live message reception and response
- **AI Auto-Reply**: Custom-built Agent framework (no Agno dependency) with multi-turn tool calling and session context management
- **Dual Knowledge Base**: Customer service KB (pre/post-sale, product Q&A) + Product KB (LLM-extracted from PDD API data)
- **Product Knowledge Auto-Sync**: Fetches product list from PDD API, uses multimodal LLM to extract and store product knowledge
- **GSX Verification**: Auto-queries GSX inspection reports after voucher redemption, generates summaries sent to users
- **Proactive Recommendations**: Agent can fetch product lists and send product cards to users
- **Keyword-Based Transfer**: Automatically detects user intent and triggers transfer to human agents
- **Async Message Queue**: High-concurrency message processing via async queue + handler chain
- **Auto-Reconnect**: WebSocket heartbeat detection and automatic reconnection

## AI Agent Tools

| Tool | Description |
|------|-------------|
| `search_customer_service_knowledge` | Search CS knowledge base (pre-sale, product Q&A, after-sale policies) |
| `get_product_knowledge` | Query detailed product knowledge (ingredients, specs, usage, price) |
| `get_shop_products` | Retrieve shop product list |
| `send_goods_link` | Send product card link to user |
| `transfer_conversation` | Transfer session to human agent |

## Target Users & Use Cases

- **Primary**: E-commerce shop owners/operators on Pinduoduo managing customer service
- **Use Cases**:
  - Automated 24/7 customer inquiry handling
  - Product information delivery
  - Post-sale support automation
  - GSX device inspection report delivery
  - Voucher/coupon management
  - Escalation to human agents when needed

## Configuration Requirements

| Config | Purpose | Required |
|--------|---------|----------|
| `llm.api_base` | LLM API endpoint (OpenAI-compatible) | ✅ |
| `llm.api_key` | LLM API key | ✅ |
| `llm.model_name` | Model name (e.g., `deepseek-chat`) | ✅ |
| `gsx.appid` / `gsx.secret` | GSX inspection platform credentials | ✅ |
| `business_hours.enabled` | Toggle AI auto-reply on/off | Optional |
| `prompt.instructions` | AI behavior instructions | Optional |

## Supported LLM Providers

Any OpenAI-compatible API: DeepSeek, Doubao (Volcengine), Kimi (Moonshot), and others.
