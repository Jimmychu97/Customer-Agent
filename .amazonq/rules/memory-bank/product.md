# Product Overview: Agent-Customer

## Purpose
Agent-Customer is a desktop AI customer service application for e-commerce, built with PyQt6. It connects to Pinduoduo (PDD) via WebSocket for real-time message receiving and uses LLM-powered AI agents to automate customer support.

## Value Proposition
- Reduces manual customer service workload through intelligent AI auto-replies
- Maintains contextual, multi-turn conversations with customers
- Integrates platform-specific APIs (Pinduoduo) for seamless shop operations
- Dual knowledge base system ensures accurate and relevant responses

## Key Features

### Messaging & Communication
- Pinduoduo WebSocket real-time message receiving
- Async message queue with handler chain for high-concurrency scenarios
- Auto-reconnect with heartbeat detection
- Keyword-triggered escalation to human agents

### AI Agent Capabilities
- Custom Agent framework (no external Agent library dependency)
- Multi-turn tool calling with session context management
- Available tools:
  - `search_customer_service_knowledge` — search pre/post-sale knowledge base
  - `get_product_knowledge` — retrieve detailed product info (ingredients, specs, usage, price)
  - `get_shop_products` — fetch shop product listing
  - `send_goods_link` — send product card to customer
  - `transfer_conversation` — escalate to human agent

### Knowledge Management
- Customer service knowledge base (pre-sale, post-sale, product Q&A)
- Product knowledge base with automatic sync from PDD API
- Multimodal LLM extraction populates product knowledge automatically
- Chinese word segmentation (jieba) for knowledge retrieval

### Platform Integrations
- Pinduoduo: WebSocket messaging, open API, automated login via Playwright
- GSX (gkdt.com): Device inspection report query, auto-triggered after verification
- OpenAI-compatible LLM APIs (DeepSeek, Doubao, Kimi, etc.)

### UI Features (PyQt6 + fluent-widgets)
- Account management (login, cookie refresh)
- Product management & SKU sync
- Knowledge base management
- Keyword management
- Auto-reply control panel
- Settings & log viewer

## Target Users
- E-commerce shop owners and operators on Pinduoduo
- Customer service teams needing AI-assisted or fully automated replies
- Shops selling electronics needing GSX device inspection integration

## Deployment
- Windows desktop application
- Packaged as standalone `.exe` via PyInstaller (no Python required on target machine)
- Runtime config via `config.json` (auto-generated on first run)
- Supports any OpenAI-compatible API endpoint
