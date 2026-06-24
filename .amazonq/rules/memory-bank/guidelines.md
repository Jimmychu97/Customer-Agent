# Development Guidelines: Agent-Customer

## Code Quality Standards

### Module Docstrings
Every module starts with a triple-quoted docstring describing its purpose:
```python
"""
产品知识自动同步服务
=================
从拼多多API拉取商品列表，调用多模态LLM分析提取产品知识存入知识库。
"""
```

### Class Docstrings
Classes use brief Chinese docstrings. Long descriptions are on the class line, not `__init__`:
```python
class ProductSyncService:
    """产品知识自动同步服务"""

    def __init__(self, knowledge_service: KnowledgeService, request_delay: float = 1.0):
        """
        初始化

        Args:
            knowledge_service: 知识库服务实例
            request_delay: API请求间隔（秒），避免限流
        """
```

### Method Docstrings
All public methods use Google-style Args/Returns docstrings:
```python
def get(self, key: str, default: Any = None) -> Any:
    """
    获取配置项，支持点号分隔的嵌套访问

    Args:
        key: 配置键名，支持嵌套访问如 'llm.api_key'
        default: 默认值

    Returns:
        配置值
    """
```

### Logging Pattern
Every module creates a module-level logger using `get_logger`:
```python
from utils.logger_loguru import get_logger

logger = get_logger("ModuleName")
```
Classes that need logging also create instance loggers in `__init__`:
```python
self.logger = get_logger("ClassName")
```

---

## Structural Conventions

### Type Annotations
All function signatures use full type annotations. `from __future__ import annotations` is used in UI files:
```python
from __future__ import annotations
from typing import Optional, List, Dict, Any, Callable, Type

def sync_shop(
    self,
    shop_id: int,
    shop_db_id: int,
    user_id: str,
    is_full_sync: bool = False,
    progress_callback: Optional[Callable[[SyncProgress], None]] = None,
) -> SyncProgress:
```

### Dataclasses for Data Transfer
Progress, results, and configuration data use `@dataclass`:
```python
from dataclasses import dataclass

@dataclass
class SyncProgress:
    """同步进度"""
    total: int
    current: int
    success: int
    failed: int
    current_goods_name: str
    cancelled: bool = False
    phase: str = "fetching"
```

### Pydantic Models for Config Validation
All configuration structures use `pydantic.BaseModel` with `Field`:
```python
from pydantic import BaseModel, Field, ConfigDict

class LLMConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    model_name: str = Field(default="", description="模型名称")
    api_key: str = Field(default="", description="API密钥")
    api_base: str = Field(default="", description="API地址")
```

### Enums for Constants
Named constants and states use `Enum`:
```python
from enum import Enum

class ServiceLifetime(Enum):
    SINGLETON = "singleton"
    TRANSIENT = "transient"
    SCOPED = "scoped"
```

---

## API Response Pattern
All service/API methods return a consistent dict with `success` bool and optional `error_msg`:
```python
# Success
return {"success": True, "coupon_id": coupon_id, "total_quantity": total_quantity}

# Failure
return {"success": False, "error_msg": "优惠券不存在"}
```
Callers always check `result["success"]` before accessing other fields.

---

## Error Handling Patterns

### Try/Except with Logging
All public methods catch exceptions, log them, and return gracefully:
```python
try:
    with db_manager.get_session() as session:
        # ... operation
        return {"success": True, ...}
except Exception as e:
    self.logger.error(f"操作失败: {str(e)}")
    return {"success": False, "error_msg": str(e)}
```

### Graceful Degradation
When LLM/external API calls fail, always fall back to a simpler implementation:
```python
except Exception as e:
    logger.error(f"LLM调用失败: {e}")
    return self._format_basic_info(list_product, detail_product)  # fallback
```

### Custom Exception Hierarchy
Domain-specific errors subclass a base exception class:
```python
class ConfigError(Exception): pass
class ConfigFileNotFoundError(ConfigError): pass
class ConfigParseError(ConfigError): pass
class ConfigValidationError(ConfigError): pass
```

---

## Dependency Injection Pattern

Services are obtained from the global DI container, not instantiated directly:
```python
from core.di_container import container
from database.knowledge_service import KnowledgeService

# In UI/service __init__:
self.knowledge_service: KnowledgeService = container.get(KnowledgeService)
```

Services are registered once in `app.py` via `configure_standard_services(config)`. New services must be registered there before use:
```python
from core.di_container import container
container.register_singleton(MyService, factory=lambda: MyService())
```

The DI container uses double-checked locking for thread-safe singleton creation.

---

## Thread Safety Patterns

### RLock for Shared State
All shared mutable state uses `threading.RLock`:
```python
self._lock = threading.RLock()

def get(self, key: str) -> Any:
    with self._lock:
        # access shared state
```

### threading.Event for Cancellation
Long-running background tasks use `threading.Event` for cancellation:
```python
self._cancellation_event = threading.Event()

def cancel(self): self._cancellation_event.set()
def is_cancelled(self): return self._cancellation_event.is_set()
def _reset_cancellation(self): self._cancellation_event.clear()
```

### QThread for Long-Running Async Work
Background operations that call `asyncio` code from Qt use `QThread` with a fresh event loop:
```python
class SyncWorker(QThread):
    progress_updated = pyqtSignal(int, int, int, str, str)
    sync_finished = pyqtSignal(int, int, bool)

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(self.service.async_method(...))
        loop.close()
        self.sync_finished.emit(result.success, result.failed, result.cancelled)
```

---

## Async Patterns

### asyncio.Semaphore for Concurrency Control
Concurrent async tasks use a semaphore to avoid rate-limiting:
```python
max_concurrent = 3
semaphore = asyncio.Semaphore(max_concurrent)

async def process_with_semaphore(item):
    async with semaphore:
        if not self.is_cancelled():
            await process_single(item)

tasks = [process_with_semaphore(item) for item in items]
await asyncio.gather(*tasks)
```

### Rate Limiting Delays
Add `await asyncio.sleep(self.request_delay)` after external API calls to avoid throttling.

---

## Database Access Pattern

Always use the context manager form of `db_manager.get_session()`:
```python
with db_manager.get_session() as session:
    obj = session.query(Model).filter_by(field=value).first()
    session.add(new_obj)
    session.commit()
```

Use `session.flush()` when you need the auto-generated ID before `commit()`:
```python
session.add(voucher)
session.flush()  # populate voucher.id
log.voucher_id = voucher.id
session.add(log)
```

---

## UI Patterns (PyQt6 + qfluentwidgets)

### Lazy Loading
Tab contents are only loaded on first activation using boolean flags:
```python
self._product_loaded = False
self._cs_loaded = False

def _on_tab_changed(self, index):
    if index == 0 and not self._product_loaded:
        self._load_product_data()
        self._product_loaded = True
```

### Signals for Progress Reporting
QThread workers emit typed pyqtSignals; UI slots update widgets:
```python
# Worker
self.progress_updated.emit(current, total, success, name, phase)

# UI connection
worker.progress_updated.connect(self._on_progress)
worker.sync_finished.connect(self._on_finished)
```

### InfoBar for User Feedback
Use qfluentwidgets `InfoBar` for notifications instead of `QMessageBox` for non-blocking feedback:
```python
from qfluentwidgets import InfoBar, InfoBarPosition

InfoBar.success("同步完成", f"成功 {n} 个", parent=self, duration=3000,
                position=InfoBarPosition.TOP)
InfoBar.error("同步失败", str(e), parent=self)
```

### Import TYPE_CHECKING to Avoid Circular Imports
UI and service files use `TYPE_CHECKING` guard for type hints:
```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from database.knowledge_service import KnowledgeService
```

---

## Config Access Patterns

Use the global `config` singleton with dot-notation for nested keys:
```python
from config import get_config

model_name = get_config("llm.model_name", "gpt-4o")
api_key = get_config("llm.api_key", "")
```

For typed access use `get_validated_config()`:
```python
from config import get_validated_config
cfg = get_validated_config()  # returns ConfigModel
```

For atomic multi-key updates:
```python
from config import config
with config.atomic_update():
    config.set("llm.api_key", new_key, save=False)
    config.set("llm.model_name", new_model, save=False)
# saved atomically on context exit
```

---

## File & Path Handling

Use `pathlib.Path` throughout, not `os.path`:
```python
from pathlib import Path
path = Path(file_path)
if not path.exists(): ...
size = path.stat().st_size
```

For PyInstaller frozen-exe support, always use the runtime path helper:
```python
if getattr(sys, 'frozen', False):
    root = Path(sys._MEIPASS).parent
else:
    root = Path(__file__).resolve().parent
```

---

## Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| Classes | PascalCase | `ProductSyncService`, `DIContainer` |
| Methods/functions | snake_case | `sync_shop`, `get_voucher_code` |
| Private methods | `_snake_case` | `_generate_coupon_id`, `_reset_cancellation` |
| Constants | UPPER_SNAKE | `ZIP_SIGNATURE`, `SUPPORTED_EXTENSIONS` |
| Module-level loggers | `logger` | `logger = get_logger("ModuleName")` |
| Type aliases | snake_case | `progress_callback` |
| Config keys | dot-separated strings | `"llm.api_key"`, `"gsx.appid"` |
