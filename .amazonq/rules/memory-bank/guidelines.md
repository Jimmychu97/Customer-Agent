# Development Guidelines

## Code Quality Standards

### File Structure
- Every module starts with a docstring explaining the module's purpose
- Module-level logger is always created at the top: `logger = get_logger("ModuleName")`
- Imports are grouped: stdlib → third-party → local (with lazy imports at function level to break circular deps)
- Section dividers use `# ========== Section Name ==========` for logical grouping within long files

### Naming Conventions
- Classes: PascalCase (`KnowledgeService`, `LifecycleMixin`, `CustomerAgent`)
- Methods and functions: snake_case
- Constants: UPPER_SNAKE_CASE (`DEFAULT_TOKEN_WINDOW`, `TOOL_REGISTRY`, `ZIP_SIGNATURE`)
- Connection keys: `f"{shop_id}_{user_id}"` string format, used consistently across all channel code
- Loggers: named after their class/module in Chinese-friendly English (`"KnowledgeService"`, `"CustomerAgent"`)

### Type Annotations
- All public methods use full type annotations
- Return types always annotated, including `Optional[T]`, `List[T]`, `Dict[str, Any]`
- `from __future__ import annotations` used in modules with forward references

### Docstrings
- Class docstrings describe responsibility and key behaviors
- Method docstrings include `Args:` and `Returns:` sections for non-trivial methods
- Inline comments use Chinese for business logic explanations

---

## Architectural Patterns

### 1. Module-Level Logger
Always get a per-module logger at import time:
```python
from utils.logger_loguru import get_logger
logger = get_logger("ModuleName")
```
Never use `print()` in production code — use `logger.info/warning/error/debug`.

### 2. DI Container Usage
Services managed by DI container are accessed via type-based resolution:
```python
from core.di_container import container
from database.knowledge_service import KnowledgeService
knowledge_service = container.get(KnowledgeService)
```
Backward-compatible access via `_DIProxy` is also used:
```python
from database import db_manager  # proxy, resolves to DatabaseManager singleton
db_manager.get_account(...)
```

### 3. Lazy Imports to Break Circular Dependencies
Imports that would create circular deps go inside functions or at the bottom of the file:
```python
# At bottom of pdd_lifecycle.py
from database import db_manager
from core.connection_status import ConnectionState
```
Or inside methods:
```python
async def _cleanup_resources(self, queue_name: str):
    from Message import message_consumer_manager  # lazy import
```

### 4. SQLAlchemy Session as Context Manager
All database operations use `with self.get_session() as session:` pattern.
Never store session objects across calls. Always commit before returning results,
and re-query after commit to return a session-attached object:
```python
def add_or_update_product(self, ...) -> ProductKnowledge:
    with self.get_session() as session:
        # ... do work ...
        session.commit()
        # Re-query to return attached object
        result = session.scalar(stmt)
        return result
```

### 5. Agent Tool Registration via Decorator
Tools are registered to `TOOL_REGISTRY` using `@agent_tool` decorator with a Pydantic param model:
```python
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
Tool modules are imported in `customer_agent.py` with `# noqa: F401` comments to trigger registration.

### 6. Async Task Lifecycle Pattern
Async background tasks (heartbeat, cookie health, reconnect) follow this pattern:
- Store tasks in `dict[connection_key, Task]`
- Cancel with `task.cancel()` + `await asyncio.wait_for(task, timeout=N)`
- Catch `(asyncio.CancelledError, asyncio.TimeoutError, asyncio.InvalidStateError)` silently
- Clean up dict entry in `finally` block of the task coroutine
- Always check stop event: `while not (self._stop_event and self._stop_event.is_set())`

### 7. Handler Chain Pattern
Message handlers inherit from `BaseHandler(MessageHandler)` and implement:
- `can_handle(context) -> bool` — filter check
- `handle(context, metadata) -> bool` — process and return True if handled

```python
class AIReplyHandler(BaseHandler):
    def can_handle(self, context: Context) -> bool:
        return context.type in self.auto_reply_types

    async def handle(self, context: Context, metadata: Dict[str, Any]) -> bool:
        ...
```

### 8. Validation Result Object Pattern
Validators return a `ValidationResult` dataclass instead of raising exceptions:
```python
@dataclass
class ValidationResult:
    is_valid: bool
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    suggestions: List[str] = None
    file_info: Dict = None
```
Always check `result.is_valid` before proceeding. Validators use inheritance and call `super().validate_basic()` first.

### 9. Config Access Pattern
- Use `config.get("llm.api_key")` for dot-notation nested access
- Use `config.get_model()` for Pydantic-validated `ConfigModel`
- Use `config.set("key", value, save=True)` for immediate persistence
- Use `with config.atomic_update():` for transactional multi-field updates
- Config saves atomically via temp file + rename to prevent corruption

### 10. Error Handling
- Service methods return `False` or `None` on failure, not exceptions (except for config errors)
- Async loops catch `asyncio.CancelledError` separately to handle cancellation cleanly
- LLM calls have fallback replies: if loop_count == 0, return error string; else return last assistant content
- All `except Exception as e:` blocks log the error with `logger.error(f"...: {e}")`

---

## Pydantic Usage

- Config models use `ConfigDict(arbitrary_types_allowed=True)` when needed
- Fields use `Field(default=..., description="...")` consistently
- `@field_validator` with `@classmethod` for input validation
- Tool param models use `Field(description=...)` — descriptions go to LLM

---

## Concurrency Patterns

- `asyncio.Semaphore` used for concurrent message processing limits (`message_semaphore`)
- `asyncio.to_thread()` used to run blocking sync code in async context (e.g. cookie validation, re-login)
- `threading.RLock()` used for thread-safe access to shared state in sync classes (config, DI container)
- Double-checked locking for singleton creation in DI container
- `asyncio.create_task()` + `task.add_done_callback(set.discard)` for fire-and-forget with tracking

---

## Logging Conventions

- Use `logger.debug()` for per-message/per-loop trace info
- Use `logger.info()` for lifecycle events (init success, connect, disconnect, data saved)
- Use `logger.warning()` for recoverable issues (connection closed, cookie invalid, fallback used)
- Use `logger.error()` for exceptions and failures
- Log key identifiers in messages: `f"shop_id={shop_id}, goods_id={goods_id}"`
- Use `log_with_ctx()` for cross-cutting logs that need shop/user context

---

## UI Conventions (PyQt6)

- UI pages are separate files in `ui/` with one class per file
- Complex pages have a sub-package (e.g., `ui/auto_reply/`)
- Worker threads go in `threads.py` within the sub-package
- DI container is accessed from UI to get services: `container.get(KnowledgeService)`
- Never do blocking I/O on the Qt main thread — use QThread or asyncio bridge

---

## Adding New Features

### New Agent Tool
1. Create `Agent/CustomerAgent/tools/my_tool.py`
2. Define Pydantic params model and decorate function with `@agent_tool`
3. Import the module in `customer_agent.py` with `# noqa: F401`

### New Message Handler
1. Create `Message/handlers/my_handler.py` inheriting `BaseHandler`
2. Implement `can_handle` and `handle`
3. Register in handler chain in `Message/core/handlers.py`

### New DB Service Method
1. Add method to `KnowledgeService` or `DatabaseManager`
2. Use `with self.get_session() as session:` 
3. Use SQLAlchemy `select()` + `session.scalar()` / `session.scalars()`
4. Log success/failure with `logger.info` / `logger.warning`
