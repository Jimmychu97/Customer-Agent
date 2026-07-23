# Project Structure

## Directory Layout

```
Customer-Agent/
├── app.py                  # Application entry point; controls global singleton init order
├── config.py               # Thread-safe config manager (Pydantic models + JSON persistence)
├── config.json             # Runtime config file (auto-generated on first run)
├── pyproject.toml          # Project metadata and dependencies (uv/hatchling)
│
├── Agent/                  # AI Agent module (custom framework)
│   └── CustomerAgent/
│       ├── custom/         # Core agent internals
│       │   ├── customer_agent.py   # Main agent orchestrator (multi-turn tool calling)
│       │   ├── llm_client.py       # OpenAI-compatible LLM client wrapper
│       │   ├── session_manager.py  # Conversation history + token window management
│       │   ├── tool_executor.py    # Tool dispatch and execution
│       │   ├── tool_decorator.py   # @tool decorator for registering agent tools
│       │   ├── message_builder.py  # Prompt/message construction utilities
│       │   └── agent_config.py     # AgentConfig Pydantic model (loaded from config.json)
│       ├── tools/          # Agent tool implementations
│       │   ├── search_customer_service_knowledge.py
│       │   ├── get_product_knowledge.py
│       │   ├── get_product_list.py
│       │   ├── send_goods_link.py
│       │   ├── move_conversation.py
│       │   ├── verify_voucher.py
│       │   ├── use_voucher.py
│       │   ├── get_voucher_code.py
│       │   └── coupon_query.py
│       └── bot.py          # Bot entry point / agent factory
│
├── Channel/                # Platform channel integrations
│   ├── channel.py          # Abstract channel base class
│   └── pinduoduo/
│       ├── pdd_channel.py      # PDDChannel: main channel orchestrator
│       ├── pdd_login.py        # Playwright-based login and Cookie acquisition
│       ├── pdd_message.py      # Message data models
│       ├── cookie_cache.py     # Cookie persistence layer
│       ├── cookie_utils.py     # Cookie parsing/validation utilities
│       ├── core/
│       │   ├── pdd_connection.py       # WebSocket connection management
│       │   ├── pdd_lifecycle.py        # Connection lifecycle (connect/reconnect/heartbeat)
│       │   ├── pdd_message_handler.py  # Raw message parsing and routing
│       │   ├── pdd_config.py           # PDD-specific config constants
│       │   ├── pdd_status.py           # Connection status tracking
│       │   └── pdd_utils.py            # Utility helpers
│       └── utils/API/      # Pinduoduo API wrappers (send message, product list, etc.)
│
├── Message/                # Message processing pipeline
│   ├── message.py          # Message data model
│   ├── core/
│   │   ├── queue.py        # Async message queue
│   │   ├── consumer.py     # Queue consumer (dispatches to handler chain)
│   │   └── handlers.py     # Handler chain orchestration
│   ├── handlers/           # Concrete message handlers
│   │   ├── base.py             # BaseHandler abstract class
│   │   ├── preprocessor.py     # Pre-processing (dedup, filtering, business hours check)
│   │   ├── keyword_handler.py  # Keyword matching → human escalation
│   │   ├── ai_handler.py       # AI agent invocation handler
│   │   └── voucher_handler.py  # Voucher redemption + device verification workflow
│   └── models/
│       └── queue_models.py     # Queue message data models
│
├── bridge/                 # Channel ↔ Message pipeline bridge
│   ├── context.py          # MessageContext: shared context object
│   └── reply.py            # Reply routing back to channel
│
├── core/                   # Core infrastructure
│   ├── di_container.py         # Dependency injection container
│   ├── service_providers.py    # Service registration (configure_standard_services)
│   ├── base_service.py         # BaseService abstract class
│   ├── connection_status.py    # ConnectionStatusManager singleton
│   └── cache.py                # In-memory cache manager
│
├── database/               # Data persistence layer
│   ├── db_manager.py           # SQLAlchemy session factory + DI proxy
│   ├── models.py               # ORM models (Account, Product, Order, Knowledge, etc.)
│   ├── knowledge_service.py    # Knowledge base CRUD + jieba-based search
│   ├── product_sync.py         # Product SKU sync from Pinduoduo API + LLM extraction
│   └── connection_pool.py      # SQLite connection pool configuration
│
├── ui/                     # PyQt6 UI layer
│   ├── main_ui.py              # MainWindow (navigation shell)
│   ├── auto_reply_ui.py        # Auto-reply page
│   ├── goods_ui.py             # Product management page
│   ├── knowledge_ui.py         # Knowledge base management page
│   ├── keyword_ui.py           # Keyword management page
│   ├── setting_ui.py           # Settings page
│   ├── user_ui.py              # Account management page
│   ├── log_ui.py               # Log viewer page
│   ├── message_ui.py           # Message viewer page
│   ├── voucher_ui.py           # Voucher management page
│   └── auto_reply/             # Auto-reply sub-components
│       ├── ui.py, card.py, manager.py, threads.py
│
├── utils/                  # Shared utilities
│   ├── logger_loguru.py        # Loguru logger factory (get_logger)
│   ├── logger_config.py        # Log file rotation config
│   ├── async_helper.py         # asyncio ↔ Qt thread bridge helpers
│   ├── path_utils.py           # Cross-platform path resolution
│   ├── runtime_path.py         # PyInstaller-aware runtime path
│   ├── resource_manager.py     # Bundled resource access
│   ├── file_validator.py       # File type/content validation
│   ├── encoding_helper.py      # Text encoding detection/conversion
│   ├── logging_context.py      # Contextual logging helpers
│   └── volcengine_models.py    # Volcengine (ByteDance) model helpers
│
└── scripts/                # Build and test scripts
    ├── build_win_exe.py        # PyInstaller build script (Windows exe)
    ├── build_exe.py            # Alternative build entry
    ├── agent_customer.spec     # PyInstaller spec file
    ├── install_playwright.py   # Playwright browser installer helper
    ├── test_gsx.py             # GSX API integration test
    └── test_sku.py             # SKU sync test
```

## Core Architectural Patterns

### 1. Initialization Order (app.py)
Strict singleton init sequence to avoid circular imports:
1. `config` (must be first)
2. `db_manager` (lazy DI proxy)
3. Logger
4. `configure_standard_services(config)` — registers all services into DI container
5. UI modules (lazy-loaded in `main()`)

### 2. Dependency Injection (core/di_container.py)
- Central DI container registers all services via `configure_standard_services()`
- Services retrieved by type or name; avoids direct module-level imports between business modules
- Lazy imports used throughout handlers to break circular dependency chains

### 3. Message Handler Chain (Message/)
```
WebSocket → PDDChannel → bridge/context → MessageQueue
  → Consumer → [Preprocessor → KeywordHandler → AIHandler → VoucherHandler]
```
Each handler in the chain can short-circuit (return early) or pass to the next handler.

### 4. Agent Tool System (Agent/CustomerAgent/)
- Tools registered via `@tool` decorator
- `ToolExecutor` dispatches tool calls from LLM responses
- `SessionManager` maintains per-user conversation history with token-window compression
- `LLMClient` wraps OpenAI-compatible API with async support

### 5. Channel Abstraction (Channel/)
- `channel.py` defines abstract base; `PDDChannel` is the concrete Pinduoduo implementation
- Each `AutoReplyThread` (UI) creates an independent `PDDChannel` instance
- All channels share the `ConnectionStatusManager` singleton

### 6. Database Layer (database/)
- SQLAlchemy ORM with SQLite backend
- `db_manager` exposed as DI proxy; actual session factory injected at startup
- `session_scope()` context manager for transactional operations
- `knowledge_service.py` uses jieba tokenization for Chinese full-text search

### 7. Config System (config.py)
- Pydantic `ConfigModel` for type-safe validation
- Thread-safe with `threading.RLock`
- Atomic file save via temp file + rename
- Dot-notation access: `config.get("llm.api_key")`
