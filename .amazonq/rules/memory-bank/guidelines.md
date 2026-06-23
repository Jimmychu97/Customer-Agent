# Development Guidelines

## Code Quality Standards

### Module-level Docstrings
Every module starts with a triple-quoted docstring describing its purpose, responsibilities, and key design notes. Keep it concise but informative:
```python
"""
AI回复处理器
专注的AI处理，移除复杂预处理和发送逻辑
"""
```

### Logger Initialization Pattern
Always use `get_logger` from `utils.logger_loguru` at the module or class level. Never use `print()` for operational output:
```python
from utils.logger_loguru import get_logger

logger = get_logger("ModuleName")          # module-level
self.logger = get_logger("ClassName")      # class-level (in __init__)
```

### Exception Handling
All public methods and async handlers must catch and log exceptions. Handlers return `False` (not raise) on failure so the consumer can try the next handler:
```python
try:
    ...
except Exception as e:
    self.logger.error(f"操作失败: {e}")
    return False
```

Use `self.logger.opt(exception=True).error(...)` when you need the full traceback:
```python
self.logger.opt(exception=True).error(f"[核销] GSX查询失败: {e}")
```

---

## Structural Conventions

### Handler Pattern (Chain of Responsibility)
All message handlers extend `BaseHandler` → `MessageHandler`. Implement exactly two methods:

```python
class MyHandler(BaseHandler):
    def can_handle(self, context: Context) -> bool:
        """Fast check — no side effects, no I/O"""
        return context.type == ContextType.TEXT

    async def handle(self, context: Context, metadata: Dict[str, Any]) -> bool:
        """Return True = handled (stops chain). Return False = pass to next handler."""
        ...
```

`metadata` carries: `shop_id`, `user_id`, `from_uid`, `user_key`.

### Tool Registration Pattern
Tools use the `@agent_tool` decorator with a Pydantic `BaseModel` for parameters. Import the tool module in `customer_agent.py` to trigger registration:

```python
from pydantic import BaseModel, Field
from Agent.CustomerAgent.custom.tool_decorator import agent_tool

class MyToolParams(BaseModel):
    shop_id: str = Field(description="店铺ID")
    query: str = Field(description="搜索关键词")

@agent_tool(
    name="my_tool_name",
    description="工具功能描述（会传给 LLM）",
    param_model=MyToolParams,
)
def my_tool(params: MyToolParams) -> str:
    ...
```

Tool functions receive a single validated Pydantic model instance and return a string result.

### DI Container Usage
Register singletons in `core/di_container.py` → `configure_standard_services()`. Retrieve via `container.get(ServiceType)`:

```python
from core.di_container import container
from database.db_manager import DatabaseManager

db = container.get(DatabaseManager)
```

Legacy imports continue to work via `_DIProxy` — `from database import db_manager` still resolves through DI without modification.

For new handlers that need a DI service but want a fallback:
```python
try:
    from core.di_container import container
    from Agent.CustomerAgent.custom.customer_agent import CustomerAgent
    bot = container.get(CustomerAgent)
except Exception as e:
    get_logger("Handler").warning(f"从DI容器获取失败: {e}, 将使用无Bot模式")
```

### Lazy Imports to Avoid Circular Dependencies
Use deferred imports inside functions/methods instead of top-level imports when importing between major modules (Channel, Agent, Message, database):

```python
async def _send(self, ...):
    from Channel.pinduoduo.utils.API.send_message import SendMessage  # lazy
    sender = SendMessage(shop_id, user_id)
```

### Module `__init__.py` Exports
Package `__init__.py` files should expose a clean public API using `__all__` and re-export the most commonly used names:

```python
from .base import BaseHandler
from .ai_handler import AIReplyHandler

__all__ = ['BaseHandler', 'AIReplyHandler']
```

---

## Naming Conventions

- Classes: `PascalCase` — `CustomerAgent`, `VoucherDetectionHandler`
- Functions/methods: `snake_case` — `can_handle`, `async_reply`
- Private helpers: `_snake_case` prefix — `_extract_imei`, `_do_verify`, `_send`
- Constants: `UPPER_SNAKE_CASE` — `TOOL_REGISTRY`, `_VOUCHER_SUCCESS`
- Logger names: match class or module name — `get_logger("VoucherDetectionHandler")`
- Log prefixes for domain isolation: `[核销]`, `[GSX]`, `[AI]` — makes grep filtering easy

---

## Pydantic Usage

Pydantic v2 is used throughout. Key patterns:

```python
from pydantic import BaseModel, Field, field_validator, ConfigDict

class MyConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: str = Field(default="", description="描述")

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        ...
```

- Always add `description=` to `Field()` for tool parameter models (LLM uses these)
- Use `Optional[str] = None` for nullable fields
- Context, config models, queue models, and tool params all use Pydantic

---

## Async Patterns

### Thread-blocking Operations
Use `asyncio.to_thread()` for blocking I/O (HTTP API calls, DB operations in sync code):

```python
result = await asyncio.to_thread(self._query_and_verify, sn, coupon_no, ...)
```

### Database Access
Always use the context manager `session_scope()` for database operations. Never hold sessions across awaits:

```python
from database import db_manager

with db_manager.session_scope() as session:
    order = session.query(Order).filter_by(id=order_id).first()
    order.status = 1
    session.flush()
```

### Semaphore for Concurrency
Use `asyncio.Semaphore` in consumers to cap concurrent message processing:
```python
self.semaphore = asyncio.Semaphore(max_concurrent)
async with self.semaphore:
    await self._process_message(wrapper)
```

### Task Lifecycle Tracking
Track tasks with a set and `done_callback` to enable graceful shutdown:
```python
task = asyncio.create_task(self._process(wrapper))
self._tasks.add(task)
task.add_done_callback(self._tasks.discard)
```

---

## Config Access

Use dot-notation via the global `config` instance:
```python
from config import config

api_key = config.get("llm.api_key", "")
config.set("llm.api_key", new_value, save=True)
```

For batch updates with rollback support:
```python
with config.atomic_update():
    config.set("llm.api_key", key, save=False)
    config.set("llm.model_name", model, save=False)
# saves automatically on context exit
```

---

## Sending Replies

All handlers send replies via `SendMessage` from `Channel.pinduoduo.utils.API.send_message`. Always check the result:

```python
from Channel.pinduoduo.utils.API.send_message import SendMessage

result = SendMessage(shop_id, user_id).send_text(from_uid, reply_text)
success = isinstance(result, dict) and result.get("success", False)
```

---

## Frequently Used Idioms

### Extract user info from Context
```python
shop_id = metadata.get("shop_id")
user_id = metadata.get("user_id")
from_uid = metadata.get("from_uid")

if not all([shop_id, user_id, from_uid]):
    self.logger.warning("缺少必要参数")
    return False
```

### Session ID construction
```python
session_id = f"{context.channel_type.value}{context.kwargs.user_id}"
# e.g. "pinduoduo123456789"
```

### Guard against double-initialization
```python
if self._is_initialized:
    return True
# ... initialize ...
self._is_initialized = True
```

### Enum `__str__` override
All enums in this codebase override `__str__` to return `.value` for clean serialization:
```python
class ChannelType(str, Enum):
    PINDUODUO = "pinduoduo"
    def __str__(self):
        return self.value
```

---

## Anti-patterns to Avoid

- Do NOT use `print()` — always use the logger
- Do NOT import between sibling modules at the top level if it creates circular dependencies — use lazy imports inside functions
- Do NOT hold SQLAlchemy sessions across `await` boundaries — keep sessions synchronous and use `asyncio.to_thread` if needed
- Do NOT skip `can_handle()` logic — keep it pure and fast with no side effects
- Do NOT register the same service twice in DI — always guard with `container.is_registered()`
- Do NOT create new `asyncio` event loops from async context — use `asyncio.to_thread` instead
