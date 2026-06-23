# Project Structure

## Directory Layout

```
Customer-Agent/
├── app.py                  # Application entry point; controls global singleton init order
├── config.py               # Thread-safe config manager (Pydantic-validated, JSON-backed)
├── config.json             # Runtime config file (auto-generated on first run)
├── pyproject.toml          # uv/hatchling project metadata and dependencies
│
├── Agent/                  # AI agent module
│   ├── bot.py              # Abstract Bot base class (reply / async_reply interface)
│   └── CustomerAgent/
│       ├── custom/         # Core agent engine
│       │   ├── customer_agent.py   # Main agent: multi-turn tool calling loop
│       │   ├── llm_client.py       # OpenAI-compatible LLM client
│       │   ├── session_manager.py  # Per-user conversation history
│       │   ├── tool_executor.py    # Tool dispatch and execution
│       │   ├── tool_decorator.py   # @tool decorator for registering tools
│       │   ├── message_builder.py  # LLM message construction
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
├── bridge/                 # Channel-agnostic message abstraction layer
│   ├── context.py          # Context, ContextType, ChannelType, PinduoduoKwargs (Pydantic)
│   └── reply.py            # Reply, ReplyType enums
│
├── Channel/                # Channel integrations
│   └── pinduoduo/
│       ├── channel.py      # Abstract Channel base class
│       ├── pdd_channel.py  # PDDChannel: WebSocket lifecycle + message dispatch
│       ├── pdd_login.py    # Playwright-based automated login and cookie management
│       ├── cookie_cache.py / cookie_utils.py
│       ├── pdd_message.py  # PDD message type parsing
│       ├── core/           # Low-level connection components
│       │   ├── pdd_connection.py   # WebSocket connect/reconnect
│       │   ├── pdd_lifecycle.py    # Start/stop lifecycle
│       │   ├── pdd_message_handler.py  # Raw message → Context conversion
│       │   ├── pdd_config.py       # PDD-specific config
│       │   ├── pdd_status.py       # Connection status
│       │   └── pdd_utils.py        # Utilities
│       └── utils/
│           ├── base_request.py     # HTTP request helper (cookie-based auth)
│           └── API/                # Pinduoduo API endpoint wrappers
│
├── Message/                # Message processing pipeline
│   ├── message.py          # Message model
│   ├── core/
│   │   ├── queue.py        # SimpleMessageQueue + QueueManager (module-level singleton)
│   │   ├── consumer.py     # MessageConsumer + MessageConsumerManager (module-level singleton)
│   │   └── handlers.py     # MessageHandler abstract base
│   ├── handlers/           # Concrete handler implementations
│   │   ├── base.py         # BaseHandler
│   │   ├── preprocessor.py # Pre-processing (business hours check, type filtering)
│   │   ├── ai_handler.py   # AI agent invocation
│   │   ├── keyword_handler.py  # Keyword matching → human escalation
│   │   └── voucher_handler.py  # Voucher redemption + GSX report
│   └── models/
│       └── queue_models.py # MessageWrapper, QueueStats, QueueConfig (Pydantic)
│
├── core/                   # Core infrastructure
│   ├── di_container.py     # DIContainer with SINGLETON/TRANSIENT/SCOPED lifetimes
│   ├── service_providers.py # _DIProxy: zero-migration lazy proxy to DI container
│   ├── connection_status.py # ConnectionStatusManager (shared across PDD channel instances)
│   ├── base_service.py     # BaseService abstract class
│   ├── cache.py            # CacheManager (module-level singleton)
│   └── service_providers.py
│
├── database/               # Data layer
│   ├── db_manager.py       # DatabaseManager: SQLAlchemy session factory
│   ├── models.py           # SQLAlchemy ORM models
│   ├── knowledge_service.py # KnowledgeService: jieba-indexed CS + product knowledge
│   ├── product_sync.py     # PDD product sync + multimodal LLM knowledge extraction
│   └── connection_pool.py  # Connection pool configuration
│
├── ui/                     # PyQt6 UI layer
│   ├── main_ui.py          # MainWindow: navigation shell
│   ├── auto_reply_ui.py    # Auto-reply main page
│   ├── auto_reply/         # Auto-reply sub-components
│   │   ├── card.py         # Account card widget
│   │   ├── manager.py      # AutoReply manager
│   │   ├── threads.py      # AutoReplyThread (QThread wrapping asyncio loop)
│   │   └── ui.py
│   ├── goods_ui.py         # Product management
│   ├── Knowledge_ui.py     # Knowledge base management
│   ├── keyword_ui.py       # Keyword management
│   ├── setting_ui.py       # Settings (LLM, GSX, prompt)
│   ├── user_ui.py          # Account management + Playwright login
│   ├── voucher_ui.py       # Voucher management
│   └── log_ui.py           # Log viewer
│
├── utils/                  # Shared utilities
│   ├── logger_loguru.py    # get_logger() factory (Loguru-based)
│   ├── logger_config.py    # Log rotation / sink configuration
│   ├── logging_context.py  # Structured logging context helpers
│   ├── async_helper.py     # asyncio helpers (run in thread, etc.)
│   ├── path_utils.py       # Path resolution utilities
│   ├── runtime_path.py     # PyInstaller-aware path resolution
│   ├── encoding_helper.py  # Encoding utilities
│   ├── file_validator.py   # File type validation
│   ├── resource_manager.py # Resource file management
│   └── volcengine_models.py # Volcengine model helpers
│
├── scripts/                # Build and utility scripts
│   ├── build_win_exe.py    # PyInstaller packaging script
│   ├── agent_customer.spec # PyInstaller spec file
│   └── test_gsx.py / test_sku.py
│
├── temp/                   # Runtime data
│   └── channel_shop.db     # SQLite database (auto-created)
├── logs/                   # Application logs
└── user_data/              # Playwright browser profiles (per phone number)
```

## Core Components & Relationships

### Initialization Flow (app.py)
```
config → DI container (configure_standard_services) → db_manager proxy
→ MainWindow → AutoReplyThread (per account)
```

### Message Processing Flow
```
PDD WebSocket
  → PDDChannel (pdd_message_handler: raw msg → Context)
  → bridge bridge.context.Context
  → SimpleMessageQueue (queue_manager)
  → MessageConsumer (_consume_loop)
  → Handler chain: PreprocessorHandler → KeywordHandler → AIHandler / VoucherHandler
  → PDD API (send reply)
```

### AI Agent Flow
```
AIHandler → CustomerAgent.async_reply(query, context)
  → SessionManager (load conversation history)
  → LLMClient (OpenAI-compatible call)
  → ToolExecutor (if tool_calls in response)
    → Tool implementations (search_knowledge, get_product, send_goods_link, ...)
  → SessionManager (save updated history)
  → Reply sent back through channel
```

### Dependency Injection Pattern
- `DIContainer` manages `ConnectionStatusManager`, `DatabaseManager`, `KnowledgeService` as singletons
- `_DIProxy` in `service_providers.py` wraps DI container lookups → legacy `from database import db_manager` imports continue to work unchanged
- Other singletons (`queue_manager`, `message_consumer_manager`) are module-level instances, imported directly

## Architectural Patterns

- **Layered architecture**: Channel → Bridge → Message pipeline → Agent → Database
- **Handler chain**: `MessageConsumer` iterates handlers; first `can_handle()` + successful `handle()` wins
- **DI container + proxy**: Gradual migration path — new code uses `container.get()`, legacy code uses module-level proxies
- **Asyncio + QThread**: Each `AutoReplyThread` runs its own `asyncio` event loop in a `QThread`; Qt UI stays on main thread
- **Pydantic everywhere**: `Context`, `PinduoduoKwargs`, `ConfigModel`, `QueueConfig`, `MessageWrapper` all use Pydantic models for validation
