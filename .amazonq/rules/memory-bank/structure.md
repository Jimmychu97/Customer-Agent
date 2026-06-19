# Project Structure and Architecture

## Directory Structure Overview
```
Agent-Customer/
├── Agent/                  # AI Agent Module (Custom Agent Framework)
│   └── CustomerAgent/
│       ├── custom/         # Custom implementation: LLM client, session management, tool executor
│       └── tools/          # Agent toolset (product/knowledge/human transfer)
├── Channel/                # Channel Integration
│   └── pinduoduo/
│       ├── core/           # Connection, lifecycle, status, message handling split modules
│       └── utils/API/      # Pinduoduo API wrappers
├── Message/                # Message Processing (Queue + Handler Chain)
│   ├── core/               # consumer / handlers / queue
│   └── handlers/           # preprocessor, AI, keyword handlers
├── bridge/                 # Bridge Module (Context/Reply)
├── core/                   # Core Services (DI container, cache, connection status)
├── database/               # Database (SQLAlchemy + knowledge service + product sync)
├── ui/                     # PyQt6 User Interface
├── utils/                  # Utility Modules (logging, paths, etc.)
├── scripts/                # Build scripts
└── app.py                  # Application entry point
```

## Core Components and Relationships

### 1. Agent Module (Agent/)
- **CustomerAgent/custom/**: Custom-built Agent framework components
  - `agent_config.py`: Agent configuration management
  - `customer_agent.py`: Main AI customer service agent
  - `llm_client.py`: LLM API client implementation
  - `session_manager.py`: Conversation session management
  - `tool_executor.py`: Tool execution and orchestration
- **CustomerAgent/tools/**: AI Agent tools (10+ specialized tools)
  - Product query tools (`get_product_knowledge.py`, `get_product_list.py`)
  - Knowledge search tools (`search_customer_service_knowledge.py`)
  - Customer interaction tools (`send_goods_link.py`, `move_conversation.py`)
  - Voucher management tools (`coupon_query.py`, `get_voucher_code.py`)

### 2. Channel Integration (Channel/)
- **pinduoduo/**: Pinduoduo platform-specific integration
  - **core/**: Connection management and message handling
    - `pdd_connection.py`: WebSocket connection management
    - `pdd_message_handler.py`: Message processing logic
    - `pdd_lifecycle.py`: Connection lifecycle management
  - **utils/API/**: Pinduoduo API wrappers (11+ API modules)
    - Product management (`product_manager.py`, `goods_commit_api.py`)
    - User and shop information (`get_user_info.py`, `get_shop_info.py`)
    - Message sending (`send_message.py`)
    - Authentication (`get_token.py`)

### 3. Message Processing System (Message/)
- **core/**: Message processing infrastructure
  - `queue.py`: Asynchronous message queue implementation
  - `consumer.py`: Message consumer management
  - `handlers.py`: Handler registration and orchestration
- **handlers/**: Specialized message handlers
  - `ai_handler.py`: AI response generation
  - `keyword_handler.py`: Keyword detection and processing
  - `preprocessor.py`: Message preprocessing
  - `voucher_handler.py`: Voucher-related message handling

### 4. Core Services (core/)
- `di_container.py`: Dependency injection container for service management
- `base_service.py`: Base service class with common functionality
- `connection_status.py`: Connection state management
- `cache.py`: Application-level caching
- `service_providers.py`: Service provider registrations

### 5. Data Layer (database/)
- `db_manager.py`: Database connection and session management
- `models.py`: SQLAlchemy ORM models
- `knowledge_service.py`: Knowledge base CRUD operations
- `product_sync.py`: Product data synchronization from API

### 6. UI Layer (ui/)
- `main_ui.py`: Main application window
- Specialized UI modules for different features (goods, keywords, knowledge, etc.)
- **auto_reply/**: Auto-reply system UI components

## Architectural Patterns

### 1. Dependency Injection
- Central DI container (`core/di_container.py`) manages all service lifecycles
- Services are registered via `configure_standard_services()`
- Prevents circular dependencies through lazy loading

### 2. Message Queue Pattern
- Asynchronous message processing via handler chain
- Each message passes through multiple handlers (preprocessor → AI → keyword)
- Supports high-concurrency scenarios

### 3. Modular Channel Architecture
- Channel-agnostic base with platform-specific implementations
- Each channel manages its own connection lifecycle
- Shared connection status monitoring

### 4. Agent Framework
- Custom-built AI agent framework (not dependent on LangChain/AutoGen)
- Tool-based execution with multi-turn conversation context
- Configurable prompt templates and instructions

### 5. Configuration Management
- Type-safe configuration with Pydantic validation
- Thread-safe configuration access
- Automatic configuration file creation and validation

## Component Relationships
```
app.py (Entry Point)
    ↓
Config System → DI Container
    ↓
DB Manager → Message Queue
    ↓
Channel Manager → Agent System
    ↓
UI Components ↔ Message Handlers
```

## Data Flow
1. **Incoming Messages**: WebSocket → Channel → Message Queue → Handlers
2. **AI Processing**: Handler → Agent → Tools → Knowledge Base → Response
3. **Outgoing Messages**: Response → Channel → Platform API/WebSocket