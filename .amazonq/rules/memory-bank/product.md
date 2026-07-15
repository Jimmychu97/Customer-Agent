# Product Overview

## Purpose
Agent-Customer is a Windows desktop AI customer service application for e-commerce platforms. It connects to Pinduoduo (拼多多) via WebSocket to receive real-time customer messages and uses an LLM-powered AI agent to automatically reply.

## Key Features
- **Real-time messaging**: WebSocket connection to Pinduoduo platform with auto-reconnect and heartbeat
- **AI auto-reply**: Custom Agent framework (no Agno dependency) with multi-turn tool calls and session context management
- **Dual knowledge base**: Customer service KB (pre/post-sale Q&A) + Product KB (LLM-extracted from product data)
- **Product sync**: Pulls product list from Pinduoduo API, uses multimodal LLM to extract product knowledge
- **Device verification routing**: After order completion, routes Apple devices to Zhenxin (iunlock8.com) and Android to GSX (gkdt.com), fetches inspection reports and sends summaries to users
- **Keyword-to-human transfer**: Detects user intent and triggers transfer to human agent
- **Message queue**: Async queue + handler chain for high-concurrency scenarios
- **Multi-account support**: Each account runs an independent AutoReplyThread with shared ConnectionStatusManager

## AI Agent Tools
| Tool | Description |
|------|-------------|
| `search_customer_service_knowledge` | Search customer service KB (pre-sale, product Q&A, after-sale policy) |
| `get_product_knowledge` | Get detailed product knowledge (ingredients, specs, usage, price) |
| `get_shop_products` | Get shop product list |
| `send_goods_link` | Send product card link to user |
| `transfer_conversation` | Transfer conversation to human agent |

## Target Users
- E-commerce shop owners/operators on Pinduoduo who want automated AI customer service
- Shops selling devices (phones, electronics) that require post-sale device verification

## Version
1.1.0 — requires Python >= 3.11, Windows OS
