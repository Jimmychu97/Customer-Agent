# Project Structure

## Directory Layout

```
Customer-Agent/
├── app.py                      # Application entry point, global initialization order
├── config.py                   # Thread-safe config manager (Pydantic models + JSON file)
├── pyproject.toml              # uv/hatchling project definition
│
├── Agent/                      # AI Agent module
│   └── CustomerAgent/
│       ├── bot.py              # High-level bot interface
│       ├── custom/             # Custom Agent framework internals
│       │   ├── customer_agent.py   # Main agent orchestrator (tool loop)
│       │   ├── agent_config.py     # Agent configuration
│       │   ├── llm_client.py       # OpenAI-compatible LLM client
│       │   ├── message_builder.py  # Prompt/message construction
│       │   ├── session_manager.py  # Per-conversation session context
│       │   ├── tool_decorator.py   # @tool decorator for registering tools
│       │   └── tool_executor.py    # Tool dispatch and execution
│       └── tools/              # Agent tool implementations
│           ├── search_customer_service_knowledge.py
│           ├── get_product_knowledge.py
│           ├── get_product_list.py
│           ├── send_goods_link.py
│           ├── move_conversation.py
│           ├── verify_voucher.py
│           ├── use_voucher.py
│           ├── get_voucher_code.py
│           └── coupon_query.py
│
├── Channel/                    # Platform channel integrations
│   ├── channel.py              # Abstract channel base class
│   └── pinduoduo/
│       ├── pdd_channel.py      # Main PDD channel (entry per account)
│       ├── pdd_login.py        # Playwright-based automated login
│       ├── pdd_message.py      # Message data models
│       ├── cookie_cache.py     # Cookie persistence
│       ├── cookie_utils.py     # Cookie helpers
│       ├── core/               # WebSocket lifecycle
│       │   ├── pdd_connection.py     # WebSocket connection management
│       │   ├── pdd_lifecycle.py      # Connect/disconnect/reconnect logic
│       │   ├── pdd_message_handler.py # Incoming message parsing
│       │   ├── pdd_config.py         # Channel configuration
│       │   ├── pdd_status.py         # Connection state enum
│       │   └── pdd_utils.py          # Utility helpers
│       └── utils/API/          # Pinduoduo API wrappers
│
├── Message/                    # Message processing pipeline
│   ├── message.py              # Message model
│   ├── core/
│   │   ├── queue.py            # Async message queue (module-level singleton)
│   │   ├── consumer.py         # Message consumer manager
│   │   └── handlers.py         # Handler chain execution
│   ├── handlers/               # Concrete message handlers
│   │   ├── base.py             # Abstract handler base class
│   │   ├── preprocessor.py     # Message preprocessing
│   │   ├── ai_handler.py       # AI reply handler
│   │   ├── keyword_handler.py  # Keyword-triggered escalation
│   │   └── voucher_handler.py  # GSX verification handler
│   └── models/
│       └── queue_models.py     # Queue item models
│
├── bridge/                     # Channel ↔ Message pipeline bridge
│   ├── context.py              # Shared context between channel and handlers
│   └── reply.py                # Reply dispatch back to channel
│
├── core/                       # Core infrastructure
│   ├── di_container.py         # DI container (SINGLETON/TRANSIENT/SCOPED)
│   ├── service_providers.py    # Lazy _DIProxy for backward-compatible access
│   ├── base_service.py         # Base class for services
│   ├── cache.py                # Module-level cache manager singleton
│   ├── connection_status.py    # ConnectionStatusManager (cross-channel shared)
│   └── __init__.py
│
├── database/                   # Data persistence layer
│   ├── db_manager.py           # SQLAlchemy session factory + CRUD
│   ├── models.py               # ORM models (channels, products, knowledge, keywords)
│   ├── knowledge_service.py    # Knowledge base search (jieba + embedding)
│   ├── product_sync.py         # PDD API → DB product sync
│   └── connection_pool.py      # DB connection pool config
│
├── ui/                         # PyQt6 user interface
│   ├── main_ui.py              # Main window / navigation
│   ├── auto_reply_ui.py        # Auto-reply page
│   ├── auto_reply/             # Auto-reply sub-components
│   │   ├── ui.py, card.py, manager.py, threads.py
│   ├── goods_ui.py             # Product management
│   ├── Knowledge_ui.py         # Knowledge base management
│   ├── keyword_ui.py           # Keyword management
│   ├── user_ui.py              # Account management
│   ├── setting_ui.py           # Settings
│   ├── log_ui.py               # Log viewer
│   └── voucher_ui.py           # Voucher/verification page
│
├── utils/                      # Shared utilities
│   ├── logger_loguru.py        # Loguru-based logger factory
│   ├── logger_config.py        # Logger configuration
│   ├── logging_context.py      # Contextual logging helpers
│   ├── async_helper.py         # asyncio bridge helpers
│   ├── path_utils.py           # Path resolution
│   ├── runtime_path.py         # PyInstaller-aware path resolution
│   ├── resource_manager.py     # Resource lifecycle management
│   ├── file_validator.py       # File type/size validation
│   ├── encoding_helper.py      # Text encoding utilities
│   └── volcengine_models.py    # Volcengine model helpers
│
└── scripts/                    # Build and test scripts
    ├── build_win_exe.py        # PyInstaller build script
    ├── agent_customer.spec     # PyInstaller spec
    ├── test_gsx.py             # GSX API test
    └── test_sku.py             # SKU sync test
```

## Core Architectural Patterns

### 1. Dependency Injection Container (`core/di_container.py`)
- Custom DI container with SINGLETON, TRANSIENT, SCOPED lifetimes
- Double-checked locking for thread-safe singleton creation
- `configure_standard_services()` called once in `app.py` before any imports
- `_DIProxy` in `service_providers.py` provides backward-compatible lazy access: `from database import db_manager`

### 2. Message Pipeline
```
PDD WebSocket → PddChannel → bridge/context → MessageQueue
                                                    ↓
                                         Consumer → HandlerChain
                                         [preprocessor → keyword → ai → voucher]
                                                    ↓
                                          bridge/reply → PDD API send
```

### 3. AI Agent Tool Loop (`Agent/CustomerAgent/custom/`)
- `CustomerAgent` orchestrates the LLM ↔ tool call loop
- `SessionManager` maintains per-conversation message history
- `ToolExecutor` dispatches tool calls to registered tool functions
- Tools decorated with `@tool` decorator for auto-registration

### 4. Module-Level Singletons
Services not in DI container use module-level singleton pattern:
- `Message/core/queue.py` → `queue_manager`
- `Message/core/consumer.py` → `message_consumer_manager`
- `core/cache.py` → `cache_manager`

### 5. Initialization Order (app.py)
1. `config` (must be first)
2. `db_manager` (DI proxy, lazy)
3. `logger`
4. `configure_standard_services(config)` — registers DI services
5. UI (lazy, in `main()`)

## Component Relationships
- `Channel` → `bridge` → `Message` (one-way pipeline flow)
- `Message/handlers/ai_handler` → `Agent/CustomerAgent` → tools → `database`
- `ui` → `core/di_container` to resolve services
- All modules → `config` (global singleton, loaded first)
- All modules → `utils/logger_loguru` for logging
