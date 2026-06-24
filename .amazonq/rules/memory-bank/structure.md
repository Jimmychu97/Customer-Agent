# Project Structure: Agent-Customer

## Directory Layout

```
Customer-Agent/
├── Agent/                      # AI Agent module (custom framework)
│   ├── CustomerAgent/
│   │   ├── custom/             # LLM client, session management, tool executor
│   │   │   ├── agent_config.py
│   │   │   ├── customer_agent.py
│   │   │   ├── llm_client.py
│   │   │   ├── message_builder.py
│   │   │   ├── session_manager.py
│   │   │   ├── tool_decorator.py
│   │   │   └── tool_executor.py
│   │   └── tools/              # Agent tool implementations
│   │       ├── search_customer_service_knowledge.py
│   │       ├── get_product_knowledge.py
│   │       ├── get_product_list.py
│   │       ├── send_goods_link.py
│   │       ├── move_conversation.py
│   │       ├── verify_voucher.py
│   │       └── use_voucher.py
│   └── bot.py                  # Agent entry point
├── Channel/                    # Channel integrations
│   └── pinduoduo/
│       ├── core/               # WebSocket connection, lifecycle, message handling
│       │   ├── pdd_connection.py
│       │   ├── pdd_lifecycle.py
│       │   ├── pdd_message_handler.py
│       │   ├── pdd_config.py
│       │   └── pdd_status.py
│       ├── utils/API/          # PDD API wrappers (voucher, products, etc.)
│       ├── pdd_channel.py      # Main channel orchestrator
│       ├── pdd_login.py        # Playwright-based auto-login & cookie refresh
│       ├── pdd_message.py      # Message model
│       └── cookie_utils.py
├── Message/                    # Message processing pipeline
│   ├── core/
│   │   ├── consumer.py         # Message consumer manager
│   │   ├── handlers.py         # Handler chain orchestration
│   │   └── queue.py            # Async message queue
│   ├── handlers/               # Individual handler implementations
│   │   ├── base.py             # Abstract base handler
│   │   ├── preprocessor.py     # Pre-processing (dedup, validation)
│   │   ├── ai_handler.py       # AI response handler
│   │   ├── keyword_handler.py  # Keyword → human transfer
│   │   └── voucher_handler.py  # Voucher/GSX handler
│   └── models/
│       └── queue_models.py     # Queue message models
├── bridge/                     # Channel ↔ Message pipeline bridge
│   ├── context.py              # Shared context object
│   └── reply.py                # Reply dispatch
├── core/                       # Core infrastructure
│   ├── di_container.py         # Dependency injection container
│   ├── service_providers.py    # DI service registration
│   ├── base_service.py         # Base service class
│   ├── connection_status.py    # Connection state manager
│   └── cache.py                # In-memory cache
├── database/                   # Data persistence layer
│   ├── models.py               # SQLAlchemy ORM models
│   ├── db_manager.py           # DB session management
│   ├── connection_pool.py      # Connection pool
│   ├── knowledge_service.py    # Knowledge base CRUD + search
│   └── product_sync.py         # Product sync from PDD API + LLM extraction
├── ui/                         # PyQt6 user interface
│   ├── main_ui.py              # Main window (navigation)
│   ├── auto_reply/             # Auto-reply panel (cards, threads, manager)
│   ├── auto_reply_ui.py
│   ├── goods_ui.py             # Product management
│   ├── keyword_ui.py           # Keyword management
│   ├── Knowledge_ui.py         # Knowledge base management
│   ├── log_ui.py               # Log viewer
│   ├── setting_ui.py           # Settings
│   ├── user_ui.py              # Account management
│   └── voucher_ui.py           # Voucher management
├── utils/                      # Shared utilities
│   ├── logger_loguru.py        # Loguru logging setup
│   ├── path_utils.py           # Path resolution
│   ├── runtime_path.py         # Runtime/frozen path support
│   ├── async_helper.py         # asyncio ↔ Qt bridge helpers
│   ├── file_validator.py       # File type validation
│   └── encoding_helper.py      # Text encoding utilities
├── scripts/                    # Build & utility scripts
│   ├── build_exe.py            # PyInstaller build script
│   ├── build_win_exe.py        # Windows exe packaging
│   └── agent_customer.spec     # PyInstaller spec file
├── app.py                      # Application entry point
├── config.py                   # Config manager (Pydantic-based, thread-safe)
├── config.json                 # Runtime config (auto-generated on first run)
└── pyproject.toml              # uv/hatchling project definition
```

## Core Components & Relationships

### Initialization Order (app.py)
```
config → DI container (configure_standard_services) → db_manager → logger
→ queue_manager → message_consumer_manager → status_manager → cache_manager
→ UI (lazy-loaded in main())
```

### Message Flow
```
PDD WebSocket → PDDChannel → bridge/context.py → Message Queue
→ Handler Chain: preprocessor → keyword_handler → ai_handler / voucher_handler
→ bridge/reply.py → PDDChannel → WebSocket reply
```

### AI Agent Flow
```
ai_handler → CustomerAgent (session_manager, message_builder)
→ LLMClient (OpenAI-compatible API) → tool_executor → Agent tools
→ knowledge_service / PDD API / GSX API → LLM response → reply
```

## Architectural Patterns

- **Dependency Injection**: `core/di_container.py` is a lightweight DI container; all services registered via `configure_standard_services()` and retrieved via `container.get(ServiceType)`
- **Handler Chain**: Message handlers inherit `base.py`, chained sequentially; each can short-circuit (return reply) or pass to next
- **QThread Workers**: Long-running async tasks (product sync, auto-reply) use `QThread` subclasses to avoid blocking the Qt event loop
- **Lazy Loading**: UI tabs use lazy-load flags (`_product_loaded`, `_cs_loaded`) to defer data fetching until first tab visit
- **Async/Sync Bridge**: `utils/async_helper.py` bridges `asyncio` coroutines with the Qt event loop; QThread workers create their own `asyncio` event loops via `asyncio.new_event_loop()`
- **Atomic Config Saves**: Config writes use temp file + rename to prevent corruption on crash
