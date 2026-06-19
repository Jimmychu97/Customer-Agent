# Project Overview: Agent-Customer

## Purpose and Value Proposition
Agent-Customer is a desktop AI customer service application for e-commerce platforms, built with PyQt6. It provides intelligent automated responses by integrating multiple platform channels and leveraging large language models. The system automates customer interactions while maintaining the ability to escalate to human agents when necessary.

## Key Features
- **Multi-platform Integration**: Currently supports Pinduoduo platform with WebSocket real-time message processing
- **AI-Powered Responses**: Custom-built Agent framework supporting multi-turn tool calls and conversation context management
- **Proactive Recommendations**: AI agents can proactively fetch product lists and send product cards to users
- **Dual Knowledge Base System**: Product knowledge base + customer service knowledge base for separate retrieval of product information and after-sales/logistics/refund policies
- **Automatic Product Knowledge Sync**: Pulls product lists from Pinduoduo API and uses multimodal LLMs to extract product knowledge for database storage
- **Keyword-based Human Escalation**: Automatically identifies user intent and supports keyword triggers to transfer to human service
- **Message Queue Processing**: Asynchronous message queue + handler chain supporting high-concurrency scenarios
- **Auto-reconnection Mechanism**: WebSocket connections with automatic reconnection and heartbeat detection

## Target Users
- E-commerce sellers on Pinduoduo platform
- Small to medium business owners managing customer service
- E-commerce operators needing automated customer support

## Use Cases
1. **Automated Customer Queries**: Handling common product questions, pricing inquiries, and basic support requests
2. **Product Recommendations**: Proactively suggesting relevant products based on conversation context
3. **Policy and FAQ Responses**: Providing instant answers to after-sales, shipping, and return policy questions
4. **Order Status Inquiries**: Automated tracking and status updates for customer orders
5. **24/7 Basic Support**: Providing basic customer service outside human working hours (8:00-23:00)