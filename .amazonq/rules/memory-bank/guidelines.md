# Development Guidelines

## Code Style & Conventions

### Module Docstrings
Every module starts with a triple-quoted docstring describing its purpose. Multi-line docstrings are common for complex modules:
```python
"""
自定义 Agent 工具系统

提供 @agent_tool 装饰器、全局工具注册表、LLM 工具格式转换和工具执行能力。
"""
```

### Class Docstrings
Classes include a docstring summarizing responsibilities. For complex classes, list sub-responsibilities:
```python
class CustomerAgent(Bot):
    """
    自定义客服 Agent

    核心循环：
    1. 加载历史消息
    2. 检查上下文压缩
    ...
    职责已分离到子模块：
    - AgentConfig: 配置管理
    """
```

### Naming Conventions
- Classes: `PascalCase` (e.g., `CustomerAgent`, `BaseHandler`, `DIContainer`)
- Functions/methods: `snake_case` (e.g., `get_tools_for_llm`, `async_reply`)
- Private methods: `_snake_case` prefix (e.g., `_run_agent_loop`, `_get_instance`)
- Constants: `UPPER_SNAKE_CASE` (e.g., `TOOL_REGISTRY`, `SESSION_EXPIRED_ERROR_CODE`)
- Module-level singletons: `snake_case` (e.g., `queue_manager`, `db_manager`, `container`)

### Type Annotations
All public methods and functions use full type annotations. `Optional`, `Dict`, `List`, `Any` from `typing` are used (Python 3.9+ generics also acceptable):
```python
def execute_tool(name: str, arguments: str, dependencies: Dict[str, str]) -> str:
async def async_reply(self, query: str, context: Context = None) -> Reply:
```

### `__all__` in `__init__.py`
Public packages define `__all__` to control exports:
```python
__all__ = ['BaseHandler', 'AIReplyHandler', 'MessagePreprocessor', 'BusinessHoursHandler']
```

---

## Architectural Patterns

### Logger Initialization
Every class initializes a logger in `__init__` using the class name:
```python
from utils.logger_loguru import get_logger

class MyClass:
    def __init__(self):
        self.logger = get_logger(self.__class__.__name__)
```
Module-level loggers (outside classes) use a descriptive name:
```python
logger = get_logger("agent_tools")
```

### Dependency Injection via DI Container
Services are registered in `core/di_container.py` and retrieved via `container.get(ServiceType)`. Prefer DI over direct instantiation for shared services:
```python
from core.di_container import container
from Agent.CustomerAgent.custom.customer_agent import CustomerAgent
bot = container.get(CustomerAgent)
```
Module-level singletons (queue_manager, cache_manager) are imported directly without DI.

### DI Proxy for Backward Compatibility
Shared services expose a `_DIProxy` so existing `from module import singleton` imports work unchanged:
```python
# database/__init__.py
db_manager = _create_proxy(DatabaseManager)
# Usage anywhere:
from database import db_manager
db_manager.get_account(...)
```

### Lazy Imports to Break Circular Dependencies
Use `importlib.import_module()` or deferred imports inside functions/methods when circular imports would otherwise occur:
```python
def _relogin_and_update_cookies(self) -> bool:
    pdd_login_module = importlib.import_module('Channel.pinduoduo.pdd_login')
    ...
```
Also used with inline `import` inside methods:
```python
async def _send_reply(self, context, reply, metadata):
    from Channel.pinduoduo.utils.API.send_message import SendMessage
    sender = SendMessage(shop_id, user_id)
```

### Async/Sync Bridge
Use `run_async_in_thread()` from `utils/async_helper.py` to run async coroutines from synchronous code without event loop conflicts:
```python
from utils.async_helper import run_async_in_thread

result = run_async_in_thread(some_async_func(), timeout=60.0)
```

### Handler Chain Pattern
Message handlers extend `BaseHandler` (which extends `MessageHandler`) and implement two methods:
```python
class MyHandler(BaseHandler):
    def can_handle(self, context: Context) -> bool:
        return context.type in self.supported_types

    async def handle(self, context: Context, metadata: Dict[str, Any]) -> bool:
        # return True = handled, False = pass to next handler
        ...
```

### Agent Tool Registration via Decorator
Tools are registered at import time using `@agent_tool`. Each tool defines a Pydantic param model:
```python
from pydantic import BaseModel, Field
from Agent.CustomerAgent.custom.tool_decorator import agent_tool

class SendGoodsParams(BaseModel):
    recipient_uid: str = Field(description="接收消息的用户UID")
    goods_id: int = Field(description="商品ID")

@agent_tool(
    name="send_goods_link",
    description="向用户发送商品卡片链接",
    param_model=SendGoodsParams,
)
def send_goods_link(params: SendGoodsParams) -> str:
    ...
```
Tools are imported in `customer_agent.py` with `# noqa: F401` comments to trigger registration:
```python
from Agent.CustomerAgent.tools import send_goods_link  # noqa: F401 — 注册 send_goods_link 工具
```

### Pydantic Models for Data Contracts
Use Pydantic `BaseModel` for all data transfer objects, API responses, and config models:
```python
from pydantic import BaseModel, Field

class Context(BaseModel):
    type: ContextType = Field(..., description="上下文类型")
    content: Optional[str] = Field(None, description="内容")
    kwargs: Any = Field(default_factory=dict)
```
Config models use `ConfigDict(arbitrary_types_allowed=True)` when needed.

### Enums as String Enums
Use `str, Enum` for enums that are serialized or compared to strings:
```python
class ContextType(str, Enum):
    TEXT = "text"
    IMAGE = "image"

    def __str__(self):
        return self.value
```

### Thread Safety
Use `threading.RLock()` for shared mutable state. Apply double-checked locking for singleton creation:
```python
self._lock = threading.RLock()

with self._lock:
    if key not in self._singletons:
        instance = factory()
        self._singletons[key] = instance
```

### Delayed Initialization Pattern
Complex components use a `_is_initialized` flag and an `initialize_async()` method called lazily on first use:
```python
async def async_reply(self, query: str, context: Context = None) -> Reply:
    if not self._is_initialized:
        if not await self.initialize_async():
            return Reply(ReplyType.TEXT, "初始化失败")
    ...
```

---

## Error Handling

### Broad Exception Catch with Logging
All `async def handle()` methods wrap their body in `try/except Exception` and log errors:
```python
async def handle(self, context: Context, metadata: Dict[str, Any]) -> bool:
    try:
        ...
        return True
    except Exception as e:
        self.logger.error(f"处理失败: {e}")
        return await self._handle_fallback(context, metadata)
```

### Graceful Fallback
Handlers provide fallback replies rather than propagating exceptions to users.

### Specific Exception Types for Config
Config errors use a custom hierarchy: `ConfigError` → `ConfigFileNotFoundError`, `ConfigParseError`, `ConfigValidationError`.

---

## Specific Idioms

### Content Preview in Logs (Privacy Protection)
Truncate content in logs to avoid logging full user messages:
```python
content_preview = str(context.content)[:50] + "..." if context.content else ""
self.logger.info(f"{self.name} {action} - {user_info} - {content_preview}")
```

### Atomic Config Save
Config is saved via temp file + rename to prevent corruption:
```python
temp_path = self.config_path.with_suffix('.tmp')
with open(temp_path, 'w', encoding='utf-8') as f:
    json.dump(self._config, f, ensure_ascii=False, indent=4)
temp_path.replace(self.config_path)
```

### Dot-Notation Config Access
Config supports nested key access with dot notation:
```python
config.get("llm.api_key")   # reads config["llm"]["api_key"]
config.set("llm.model_name", "deepseek-chat", save=True)
```

### Section Separators in Long Files
Use `# ===...===` comment blocks to visually separate major sections:
```python
# ==============================================================================
# 工具注册表
# ==============================================================================
```
