# Development Guidelines

## Code Quality Standards

### Module-Level Docstrings
Every module starts with a triple-quoted docstring explaining its purpose. Multi-line docstrings describe responsibilities, key design decisions, and module context.

```python
"""
自定义 CustomerAgent 实现

完全自主实现，不依赖 Agno 框架。

本模块已重构，职责分离为：
- agent_config.py: 配置管理
- llm_client.py: LLM 客户端封装
"""
```

### Logging Pattern
Always use `get_logger` from `utils.logger_loguru`, never use Python's standard `logging` module directly.

```python
from utils.logger_loguru import get_logger
logger = get_logger("ModuleName")  # module-level logger, string name = logical component name
```

- Module-level logger instantiation (not per-method)
- Log messages in Chinese
- Use structured log levels: `logger.info(...)`, `logger.error(...)`, `logger.warning(...)`, `logger.debug(...)`
- Never log sensitive user content verbatim; truncate or mask

### Exception Handling
All public methods must catch exceptions, log with context, and return a meaningful fallback:

```python
try:
    result = some_operation()
    return result
except SomeSpecificError as e:
    logger.error(f"操作失败: {e}, context={context_info}")
    return fallback_value
except Exception as e:
    logger.error(f"意外异常: {e}")
    return error_string_or_none
```

Tools must return `str` error messages (never raise) so the LLM can read the failure reason.

---

## Structural Conventions

### Service Base Classes
All services inherit from one of these base classes in `core/base_service.py`:

| Base Class | Use When |
|---|---|
| `BaseService` | Any service with initialize/dispose lifecycle |
| `ConfigurableService` | Service that reads from config |
| `AsyncService` | Service with async initialize/dispose |
| `HealthCheckable` | Service needing health check endpoint |

Services always set `self.logger = get_logger(self.__class__.__name__)` (inherited automatically from `BaseService`).

### Handler Pattern (Chain of Responsibility)
Message handlers extend `BaseHandler` → `MessageHandler`:

```python
class MyHandler(BaseHandler):
    def can_handle(self, context: Context) -> bool:
        return context.type in self.supported_types

    async def handle(self, context: Context, metadata: Dict[str, Any]) -> bool:
        try:
            # ... process
            return True
        except Exception as e:
            self.logger.error(f"处理失败: {e}")
            return False  # or True to stop chain propagation
```

### Agent Tool Pattern
Tools use `@agent_tool` decorator with a `Pydantic BaseModel` for parameters. This is the ONLY supported way to register tools.

```python
from pydantic import BaseModel, Field
from Agent.CustomerAgent.custom.tool_decorator import agent_tool

class MyToolParams(BaseModel):
    shop_id: Optional[str] = Field(default=None, description="店铺ID")
    query: str = Field(description="查询内容")

@agent_tool(
    name="my_tool_name",
    description="工具描述（传给LLM，使用中文描述）",
    param_model=MyToolParams,
)
def my_tool(params: MyToolParams) -> str:
    # Always return str; return error message string on failure
    try:
        ...
        return "结果字符串"
    except Exception as e:
        logger.error(f"工具执行异常: {e}")
        return f"工具执行失败: {e}"
```

Tool registration is triggered by importing the tool module — use `# noqa: F401` comments to mark intentional side-effect imports.

### DI Container Usage
Services managed by DI container (`ConnectionStatusManager`, `DatabaseManager`, `KnowledgeService`) must be accessed via container:

```python
from core.di_container import container
from database.db_manager import DatabaseManager

db = container.get(DatabaseManager)
```

For backward-compatible module-level access, use the `_DIProxy` pattern (see `database/__init__.py`):

```python
# In module __init__.py
from core.service_providers import _create_proxy
db_manager = _create_proxy(DatabaseManager)  # proxy supports from database import db_manager
```

Do NOT create new singleton instances directly in business code. Register in `configure_standard_services()` in `core/di_container.py`.

### Configuration Access
Always use `config.get("section.key", default)` with dot-notation. Never access `config._config` directly.

```python
from config import config
api_key = config.get("llm.api_key", "")
```

Use `config.get_model()` when you need typed access via `ConfigModel`.

---

## Naming Conventions

| Pattern | Convention | Example |
|---|---|---|
| Classes | PascalCase | `CustomerAgent`, `BaseHandler` |
| Functions/methods | snake_case | `get_product_list`, `async_reply` |
| Module-level singletons | snake_case | `db_manager`, `queue_manager` |
| Private methods | `_` prefix | `_run_agent_loop`, `_get_user_info` |
| Tool param models | `{ToolName}Params` | `GetShopProductsParams` |
| Logger variable | always `logger` | `logger = get_logger("Name")` |
| Constants | UPPER_SNAKE | `DEFAULT_MAX_LOOPS`, `TOOL_REGISTRY` |

---

## Semantic Patterns

### Lazy / Deferred Initialization
Components use `_is_initialized` flag + `initialize_async()` to defer heavy initialization until first use:

```python
async def async_reply(self, query: str, context: Context) -> Reply:
    if not self._is_initialized:
        if not await self.initialize_async():
            return Reply(ReplyType.TEXT, "初始化失败")
    ...
```

### Lazy Imports to Break Circular Dependencies
Use local imports inside functions/methods to break circular dependency chains:

```python
async def _send_reply(self, ...):
    from Channel.pinduoduo.utils.API.send_message import SendMessage  # lazy import
    sender = SendMessage(shop_id, user_id)
```

### __init__.py as Public API
Each package's `__init__.py` explicitly defines its public API via `__all__` and re-exports from submodules:

```python
# Message/handlers/__init__.py
from .base import BaseHandler
from .ai_handler import AIReplyHandler
__all__ = ['BaseHandler', 'AIReplyHandler', 'MessagePreprocessor']
```

Keep `__init__.py` files minimal — only re-exports, no business logic.

### Pydantic for All Data Models
Use `Pydantic BaseModel` (v2 style) for:
- Config models (`config.py`)
- Bridge context/reply (`bridge/context.py`, `bridge/reply.py`)
- Tool parameters (every `@agent_tool` param_model)
- API response models

Use `ConfigDict(arbitrary_types_allowed=True)` when models contain non-standard types.

### Enum-Based Type Safety
Use `str, Enum` subclass pattern for type-safe constants shared across modules:

```python
class ContextType(str, Enum):
    TEXT = "text"
    IMAGE = "image"

    def __str__(self):
        return self.value
```

This allows `context.type == "text"` comparisons AND provides enum safety.

### Async/Sync Bridge for Qt
Qt UI runs on main thread; asyncio runs in a separate thread. Use `utils/async_helper.py` for crossing the boundary. Never call `asyncio.run()` inside an existing event loop — detect the situation:

```python
try:
    response = asyncio.run(self._llm_client.chat(...))
except RuntimeError:
    return "[降级处理]"  # already inside event loop
```

### Thread Safety
- Config uses `threading.RLock()` for all read/write operations
- DI container uses `threading.RLock()` with double-checked locking for singleton creation
- Module-level singletons are safe to import from multiple threads after initialization

---

## API Integration Conventions (Pinduoduo)

Before implementing any new PDD API call:
1. Test with curl/Python script to confirm real request params, headers, response structure
2. Field names are **camelCase** in API responses (e.g., `goodsId`, not `goods_id`)
3. Price values are in **fen (分)** → divide by 100 to get **yuan (元)**
4. Parse from confirmed field paths — do not guess field names

---

## File & Import Conventions

- Each functional area has its own `__init__.py` exporting public symbols
- Avoid wildcard imports (`from module import *`)
- Tool modules are imported in `customer_agent.py` with `# noqa: F401` to trigger `@agent_tool` registration
- `from __future__ import annotations` used in modules with forward references
- Scripts in `scripts/` are standalone and not imported by application code
