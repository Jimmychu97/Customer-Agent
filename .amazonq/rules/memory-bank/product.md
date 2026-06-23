# Product Overview: Agent-Customer

## Purpose & Value Proposition

Agent-Customer is a Windows desktop application for e-commerce AI customer service, specifically targeting the Pinduoduo (拼多多) platform. It automates customer support by combining real-time WebSocket message receiving with an AI agent that can intelligently respond to customers, query product knowledge, and handle post-sale workflows.

The core value is reducing manual customer service workload while maintaining quality through: AI-powered auto-replies, dual knowledge base retrieval, GSX device inspection integration, and keyword-triggered escalation to human agents.

## Key Features & Capabilities

### Message Handling
- Real-time WebSocket connection to Pinduoduo merchant workbench
- Async message queue with handler chain (preprocessor → AI/keyword/voucher handlers)
- Auto-reconnect with heartbeat detection
- Business hours enforcement (default 08:00–23:00)

### AI Agent
- Custom-built Agent framework (not Agno-based)
- Multi-turn tool calling with session context management
- OpenAI-compatible API (supports DeepSeek, Doubao, Kimi, etc.)
- Proactive product recommendation (agent can fetch and send product cards)

### Knowledge Base (Dual System)
- Customer service knowledge base: pre-sale/post-sale/FAQ answers (jieba-segmented search)
- Product knowledge base: auto-extracted from Pinduoduo product list via multimodal LLM

### Agent Tools

| Tool | Function |
|------|----------|
| `search_customer_service_knowledge` | Search CS knowledge base (pre-sale, post-sale, FAQ) |
| `get_product_knowledge` | Get detailed product info (ingredients, specs, usage, price) |
| `get_shop_products` | Fetch shop product list |
| `send_goods_link` | Send product card to customer |
| `transfer_conversation` | Escalate to human agent |
| `verify_voucher` / `use_voucher` | Voucher verification and redemption |
| `coupon_query` | Query coupon information |
| `get_voucher_code` | Retrieve voucher codes |

### Integrations
- **GSX (gkdt.com)**: Device inspection report query — auto-triggered after voucher redemption
- **Pinduoduo Open API**: Product list sync, order/conversation management
- **Playwright**: Automated Pinduoduo login and cookie refresh (Chromium)

### UI (PyQt6 + pyqt6-fluent-widgets)
- Account management (add/login PDD accounts)
- Auto-reply control panel (start/stop per account)
- Product management with SKU sync and GSX ServerId mapping
- Knowledge base management (CS knowledge + product knowledge)
- Keyword management (trigger human handoff)
- Voucher management UI
- Settings page (LLM config, GSX config, business hours, prompt instructions)
- Log viewer

## Target Users

- Small-to-medium Pinduoduo merchants who want to automate customer service
- Shop owners needing AI-assisted pre-sale consultation and post-sale support
- Operations teams managing multiple PDD accounts simultaneously

## Use Cases

1. Automated pre-sale Q&A (product info, pricing, specs) using knowledge base
2. Post-sale handling (return policy, logistics status) via CS knowledge base
3. GSX device inspection report auto-delivery after voucher redemption
4. Keyword detection → automatic human agent escalation
5. Proactive product recommendations from shop catalog
