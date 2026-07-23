# Development Guidelines

## Code Quality Standards

### Module Docstrings
Every module starts with a triple-quoted docstring describing its purpose:
```python
"""
依赖注入容器 - 解决全局单例问题，提供更好的依赖管理
"""
```

### Class Docstrings
All classes have a one-line Chinese docstring:
```python
class ProductSyncService:
    """产品知识自动同步服务"""
```

### Method Docstrings (Args/Returns style)
Public methods use explicit Args/Returns sections in Chinese:
```python
def sync_shop(self, shop_id: int, ...) -> SyncProgress:
    """
    同步店铺产品知识（两阶段同步）

    Args:
        shop_id: 店铺ID（拼多多的shop_id）
        is_full_sync: True=全量同步，False=增量同步

    Returns:
        最终同步进度
    """
```

### Type Annotations
All function signatures use full type annotations:
```python
def validate_basic(self, file_path: str) -> ValidationResult:
async def sync_shop(self, shop_id: int, shop_db_id: int, user_id: str,
                    is_full_sync: bool = False,
                    progress_callback: Optional[Callable[[SyncProgress], None]] = None) -> SyncProgress:
```

---

## Naming Conventions

- Classes: `PascalCase` (e.g., `DIContainer`, `ProductSyncService`, `ExcelValidator`)
- Methods/functions: `snake_case`
- Private methods: `_snake_case` prefix (e.g., `_reset_cancellation`, `_extract_product_knowledge`)
- Constants/class-level config: `UPPER_SNAKE_CASE` (e.g., `SUPPORTED_EXTENSIONS`, `ZIP_SIGNATURE`)
- Module-level singletons: lowercase noun (e.g., `container`, `logger`)
- Database columns: `snake_case`; exception for legacy columns use `Column('SeverId', ...)` with mapped attribute name

---

## Structural Conventions

### Dataclasses for Value Objects
Use `@dataclass` for simple data containers with optional fields defaulting to `None`:
```python
@dataclass
class SyncProgress:
    total: int
    current: int
    success: int
    failed: int
    current_goods_name: str
    cancelled: bool = False
    phase: str = "fetching"
```

Use `__post_init__` to initialize mutable defaults:
```python
@dataclass
class ValidationResult:
    is_valid: bool
    suggestions: List[str] = None

    def __post_init__(self):
        if self.suggestions is None:
            self.suggestions = []
```

### Pydantic Models for Config
All configuration objects use Pydantic `BaseModel` with `Field` and Chinese descriptions:
```python
class LLMConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    model_name: str = Field(default="", description="模型名称")
    api_key: str = Field(default="", description="API密钥")
```

### SQLAlchemy ORM Models
- All models inherit from `Base = declarative_base()`
- Every column has a `comment=` kwarg in Chinese
- Relationships use `back_populates` (bidirectional) or `backref` (unidirectional)
- Unique constraints defined in `__table_args__` with explicit `name=`:
```python
__table_args__ = (
    UniqueConstraint('shop_id', 'goods_id', name='uix_product_knowledge_shop_goods'),
)
```
- Timestamps use `default=datetime.now` and `onupdate=datetime.now`
- Every model implements `__repr__` showing key identifying fields

---

## Error Handling Patterns

### Broad Exception Catch with Logging
All async handlers and service methods wrap logic in `try/except Exception`:
```python
try:
    # business logic
except Exception as e:
    self.logger.opt(exception=True).error(f"[核销] GSX查询失败: {e}")
```

Use `logger.opt(exception=True)` to include full traceback in error logs.

### Graceful Degradation
LLM calls always have a fallback:
```python
try:
    response = await client.chat.completions.create(...)
    return parse_response(response)
except Exception as e:
    logger.error(f"LLM调用失败: {e}")
    return self._format_basic_info(list_product, detail_product)  # fallback
```

### Validation Result Pattern
Validators return a `ValidationResult` object instead of raising exceptions, enabling callers to inspect error type and suggestions:
```python
return ValidationResult(
    is_valid=False,
    error_type="CORRUPTED_FILE",
    error_message="文件格式错误或已损坏",
    suggestions=["重新创建Excel文件", "尝试用Excel打开并另存为"]
)
```

---

## Async Patterns

### asyncio.Semaphore for Concurrency Control
Limit concurrent API calls with a semaphore:
```python
semaphore = asyncio.Semaphore(3)  # max 3 concurrent

async def process_with_semaphore(product):
    async with semaphore:
        if not self.is_cancelled():
            await process_single_product(product)

await asyncio.gather(*[process_with_semaphore(p) for p in products])
```

### asyncio.sleep for Rate Limiting
Always add delay between external API calls:
```python
await asyncio.sleep(self.request_delay)
```

### threading.Event for Cancellation
Use `threading.Event` for cancellation flags (safe across sync/async boundaries):
```python
self._cancellation_event = threading.Event()

def cancel(self): self._cancellation_event.set()
def is_cancelled(self): return self._cancellation_event.is_set()
def _reset_cancellation(self): self._cancellation_event.clear()
```

### asyncio.to_thread for Sync-in-Async
Wrap synchronous blocking calls:
```python
result = await asyncio.to_thread(
    lambda: SendMessage(shop_id, user_id).send_text(from_uid, text)
)
```

---

## Dependency Injection Patterns

### Service Registration (configure_standard_services)
Register services with explicit lifetime and guard against double-registration:
```python
if not container.is_registered(ConnectionStatusManager):
    container.register_singleton(
        ConnectionStatusManager,
        factory=lambda: ConnectionStatusManager()
    )
```

### Service Retrieval
Retrieve by type, not by string name:
```python
from core.di_container import container
db_manager = container.get(DatabaseManager)
```

### Thread Safety
All DI container operations use `threading.RLock` with double-checked locking for singletons:
```python
if key in self._singletons:
    return self._singletons[key]
with self._lock:
    if key in self._singletons:  # double-check
        return self._singletons[key]
    instance = descriptor.factory()
    self._singletons[key] = instance
```

---

## Logging Patterns

### Logger Instantiation
Always use the project logger factory at module level:
```python
from utils.logger_loguru import get_logger
logger = get_logger("ProductSync")
```

For class-based services, assign in `__init__`:
```python
self.logger = get_logger("DIContainer")
```

### Log Message Format
Prefix log messages with a bracketed context tag:
```python
self.logger.info(f"[核销] 发送成功 from_uid={from_uid} text={text[:30]!r}")
self.logger.warning(f"[核销] 发送失败 from_uid={from_uid} result={result}")
self.logger.opt(exception=True).error(f"[核销] 发送回复失败 from_uid={from_uid}: {e}")
```

### Phase Logging
Use `===` section headers in logs for multi-phase operations:
```python
logger.info("=== 第一阶段：开始抓取商品列表 ===")
logger.info("=== 第二阶段：开始并发提取商品详细知识 ===")
```

---

## Lazy Import Pattern
To avoid circular imports, use local imports inside methods/functions:
```python
async def _send(self, ...):
    from Channel.pinduoduo.utils.API.send_message import SendMessage
    result = await asyncio.to_thread(lambda: SendMessage(...).send_text(...))
```

This pattern is used consistently in handlers, tools, and service methods.

---

## Config Access Pattern
Always use dot-notation via the global `config` or `get_config` helper:
```python
from config import get_config
model_name = get_config("llm.model_name", "gpt-4o")
api_key = get_config("llm.api_key", "")
```

For agent-specific config, use `AgentConfig.load_from_config()`:
```python
from Agent.CustomerAgent.custom.agent_config import AgentConfig
agent_config = AgentConfig.load_from_config()
llm_client = LLMClient(
    model_name=agent_config.model_name,
    api_key=agent_config.api_key,
    api_base=agent_config.api_base,
)
```

---

## Database Session Pattern
Always use `session_scope()` context manager for transactional DB operations:
```python
from database import db_manager
with db_manager.session_scope() as session:
    order = session.query(Order).filter_by(id=order_id).first()
    if order:
        order.status = status
```

---

## Progress Callback Pattern
Long-running async operations accept an optional `progress_callback`:
```python
async def sync_shop(
    self,
    ...,
    progress_callback: Optional[Callable[[SyncProgress], None]] = None,
) -> SyncProgress:
    ...
    if progress_callback:
        progress_callback(progress)
```

Update the shared progress object in-place and call the callback after each meaningful step.

---

## Build Script Conventions
- Scripts use `argparse` with `--clean`, `--mode`, `--check-only` flags
- Platform checks via `platform.system() == "Windows"`
- All subprocess calls go through a `run_command(cmd, cwd=None)` helper that captures output and raises on non-zero exit
- Windows console encoding set to UTF-8 via `ctypes.windll.kernel32.SetConsoleOutputCP(65001)`
- Required files validated before build via a `check_dependencies()` function
