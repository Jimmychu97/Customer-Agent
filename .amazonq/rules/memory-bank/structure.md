# Project Structure

## Directory Layout
```
Customer-Agent/
├── app.py                  # Entry point — initializes config, DI container, Qt app
├── config.py               # Thread-safe config manager (Pydantic-validated, JSON-backed)
├── config.json             # Runtime config file (auto-created on first run)
├── pyproject.toml          # Project metadata and dependencies (uv/hatchling)
│
├── Agent/                  # AI agent module
│   ├── bot.py              # Bot entry point
│   └── CustomerAgent/
│       ├── custom/         # Core agent framework
│       │   ├── customer_agent.py   # Main agent orchestrator
│       │   ├── llm_client.py       # OpenAI-compatible LLM client
│       │   ├── session_manager.py  # Per-conversation session/context management
│       │   ├── tool_executor.py    # Tool call execution engine
│       │   ├── tool_decorator.py   # @tool decorator for registering tools
│       │   ├── message_builder.py  # Builds LLM message arrays
│       │   └── agent_config.py     # Agent configuration
│       └── tools/          # Agent tool implementations
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
├── Channel/                # Platform channel integrations
│   ├── channel.py          # Abstract channel base class
│   └── pinduoduo/
│       ├── pdd_channel.py  # Main channel orchestrator
│       ├── pdd_login.py    # Playwright-based login & cookie refresh
│       ├── pdd_message.py  # Message model
│       ├── cookie_cache.py # In-memory cookie cache
│       ├── cookie_utils.py # Re-login guard (prevents concurrent re-logins)
│       ├── core/           # Connection internals
│       │   ├── pdd_connection.py    # WebSocket connection management
│       │   ├── pdd_lifecycle.py     # Connection lifecycle (connect/reconnect)
│       │   ├── pdd_message_handler.py # Raw message parsing
│       │   ├── pdd_config.py        # Channel config
│       │   ├── pdd_status.py        # Connection status
│       │   └── pdd_utils.py         # Utilities
│       └── utils/API/      # Pinduoduo API wrappers (extend BaseRequest)
│
├── Message/                # Message processing pipeline
│   ├── message.py          # Message model
│   ├── core/
│   │   ├── queue.py        # Async message queue (module-level singleton: queue_manager)
│   │   ├── consumer.py     # Queue consumer (module-level singleton: message_consumer_manager)
│   │   └── handlers.py     # Handler chain orchestrator
│   ├── handlers/           # Individual message handlers
│   │   ├── base.py         # Abstract handler base class
│   │   ├── preprocessor.py # Pre-processing (filtering, normalization)
│   │   ├── ai_handler.py   # AI agent invocation
│   │   ├── keyword_handler.py # Keyword-to-human-transfer detection
│   │   └── voucher_handler.py # Device verification routing
│   └── models/
│       └── queue_models.py # Queue message models
│
├── bridge/                 # Channel ↔ message pipeline bridge
│   ├── context.py          # Context and ChannelType/ContextType enums (Pydantic models)
│   └── reply.py            # Reply model
│
├── core/                   # Core infrastructure
│   ├── di_container.py     # DI container (singleton/transient/scoped lifetimes)
│   ├── service_providers.py # _DIProxy — lazy backward-compat proxies
│   ├── connection_status.py # ConnectionStatusManager singleton
│   ├── base_service.py     # Base service class
│   └── cache.py            # Cache manager (module-level singleton: cache_manager)
│
├── database/               # Data layer
│   ├── db_manager.py       # DatabaseManager — accounts, products, keywords
│   ├── models.py           # SQLAlchemy ORM models
│   ├── knowledge_service.py # Knowledge base CRUD + jieba search
│   ├── product_sync.py     # Pinduoduo product sync + LLM knowledge extraction
│   └── connection_pool.py  # SQLAlchemy connection pool config
│
├── ui/                     # PyQt6 + pyqt6-fluent-widgets UI
│   ├── main_ui.py          # Main window
│   ├── auto_reply/         # Auto-reply page (card, manager, threads, ui)
│   ├── auto_reply_ui.py    # Auto-reply UI entry
│   ├── goods_ui.py         # Product management page
│   ├── Knowledge_ui.py     # Knowledge base management page
│   ├── keyword_ui.py       # Keyword management page
│   ├── setting_ui.py       # Settings page
│   ├── user_ui.py          # Account management page
│   ├── message_ui.py       # Message display
│   ├── log_ui.py           # Log viewer
│   └── voucher_ui.py       # Voucher/verification page
│
├── utils/                  # Shared utilities
│   ├── logger_loguru.py    # Loguru logger factory (get_logger)
│   ├── logging_context.py  # Logging context helpers
│   ├── async_helper.py     # run_async_in_thread — bridges sync↔async
│   ├── path_utils.py       # Path resolution
│   ├── runtime_path.py     # PyInstaller-aware runtime paths
│   ├── resource_manager.py # Resource lifecycle management
│   ├── encoding_helper.py  # Encoding utilities
│   ├── file_validator.py   # File validation
│   └── volcengine_models.py # Volcengine model helpers
│
└── scripts/                # Build and test scripts
    ├── build_win_exe.py    # PyInstaller Windows build
    └── agent_customer.spec # PyInstaller spec file
```

## Core Architectural Patterns

### Initialization Order (app.py)
1. `config` — must be first; all modules depend on it
2. `db_manager` — DI proxy (lazy)
3. Logger
4. `configure_standard_services()` — registers ConnectionStatusManager, DatabaseManager, KnowledgeService into DI container
5. Qt app + MainWindow

### Dependency Injection
- `core/di_container.py` — custom DI container supporting singleton/transient/scoped lifetimes with double-checked locking
- `core/service_providers.py` — `_DIProxy` wraps DI container lookups so legacy `from database import db_manager` imports continue to work unchanged
- Module-level singletons (queue_manager, cache_manager) are used directly without DI registration

### Message Flow
```
WebSocket (PDDChannel)
  → bridge.Context
  → Message queue (queue_manager)
  → Handler chain: preprocessor → keyword_handler → voucher_handler → ai_handler
  → AI Agent (CustomerAgent) with tool calls
  → Reply sent back via channel API
```

### Channel Abstraction
- `Channel/channel.py` defines the abstract base
- `Channel/pinduoduo/pdd_channel.py` implements Pinduoduo-specific logic
- Each account runs in its own `AutoReplyThread` (ui/auto_reply/threads.py)
- All accounts share a single `ConnectionStatusManager` instance

### Lazy Imports
Business modules use `importlib.import_module()` or deferred `import` statements inside functions to break circular dependencies (e.g., `pdd_login` imported lazily in `BaseRequest._relogin_and_update_cookies`).
