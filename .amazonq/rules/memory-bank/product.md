# Product Overview

## Project Purpose
Agent-Customer is an e-commerce AI customer service desktop application built with PyQt6. It integrates with the Pinduoduo (拼多多) platform via WebSocket for real-time message reception and uses AI large language models to provide intelligent automated replies.

## Value Proposition
- Automates customer service responses for e-commerce merchants on Pinduoduo
- Reduces manual workload through AI-powered replies backed by a dual knowledge base
- Supports device verification (验机) workflows for second-hand electronics sellers
- Enables proactive product recommendations via AI agent tools

## Key Features

### Messaging & Communication
- Real-time WebSocket message reception from Pinduoduo platform
- Async message queue with handler chain for high-concurrency scenarios
- Auto-reconnect with heartbeat detection for WebSocket connections
- Keyword-triggered escalation to human agents

### AI Agent System (Custom Framework, no Agno dependency)
- Multi-turn tool calling with session context management
- Available tools:
  - `search_customer_service_knowledge` — pre/post-sale knowledge base search
  - `get_product_knowledge` — detailed product info (ingredients, specs, usage, price)
  - `get_shop_products` — fetch shop product list
  - `send_goods_link` — send product card to user
  - `transfer_conversation` — escalate to human agent

### Dual Knowledge Base
- Customer service knowledge base (pre-sale, post-sale, product Q&A)
- Product knowledge base (auto-extracted via multimodal LLM from Pinduoduo API)

### Device Verification (验机) Workflows
- Apple devices → Zhenxin (真心验机, iunlock8.com) or Lingsu (凌速) API
- Android devices → GSX verification (gkdt.com)
- LLM-generated analysis report sent to customer after verification
- Automatic routing based on device type field in product SKU config

### Product Management
- Sync product SKUs from Pinduoduo API
- Configure ServerId, device type, and verification platform per SKU
- Auto-extract product knowledge using multimodal LLM

### UI (PyQt6 + pyqt6-fluent-widgets)
- Account management (Pinduoduo login, Cookie management)
- Auto-reply management (start/stop per account)
- Product management (SKU sync, verification config)
- Knowledge base management (add/edit customer service knowledge)
- Keyword management (configure human-escalation triggers)
- Settings page (LLM config, business hours, prompt instructions)
- Log viewer, message viewer, voucher management

## Target Users
- Pinduoduo merchants selling second-hand electronics (phones, tablets)
- Small-to-medium e-commerce businesses needing automated customer service
- Merchants requiring device verification report delivery automation

## Business Hours & Control
- Configurable AI auto-reply hours (default 08:00–23:00)
- Toggle for image-triggered auto-reply
- Per-account enable/disable of auto-reply

## Deployment
- Windows desktop application
- Packaged as standalone exe via PyInstaller (no Python required on target machine)
- Playwright Chromium used for Pinduoduo login and Cookie refresh
