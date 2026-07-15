# Development Guidelines

## Code Quality Standards

### Module-Level Docstrings
每个模块顶部必须有三引号文档字符串，说明模块职责：
```python
"""
产品知识自动同步服务
=================
从拼多多API拉取商品列表，调用多模态LLM分析提取产品知识存入知识库。
"""
```

### Class Docstrings
类必须有简短的中文说明，复杂类需说明核心循环/职责分离：
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

### Method Docstrings
公共方法使用 Args/Returns 格式：
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

---

## Naming Conventions

- 类名：PascalCase（`DIContainer`, `ProductSyncService`, `ExcelValidator`）
- 方法/函数：snake_case（`sync_shop`, `validate_basic`, `get_config`）
- 私有成员：单下划线前缀（`_lock`, `_config`, `_singletons`）
- 常量/枚举值：大写 snake_case（`SINGLETON`, `ZIP_SIGNATURE`, `SUPPORTED_EXTENSIONS`）
- 模块级单例：小写（`container`, `config`, `logger`）
- 数据库列名：snake_case，带 `comment=` 说明（`shop_id`, `goods_name`）

---

## Type Annotations
全面使用类型注解，包括返回值：
```python
from typing import Dict, Any, Callable, Type, Optional, List

def get(self, service_type: Type) -> Any: ...
def validate_basic(self, file_path: str) -> ValidationResult: ...
async def sync_shop(self, shop_id: int, ..., progress_callback: Optional[Callable[[SyncProgress], None]] = None) -> SyncProgress: ...
```

---

## Logging Pattern
统一使用 `get_logger` 工厂，模块名作为 logger 名称：
```python
from utils.logger_loguru import get_logger
logger = get_logger("ProductSync")   # 模块级 logger

# 使用方式
logger.info("ProductSyncService 初始化成功")
logger.error(f"获取商品列表失败: {result.get('error_msg')}")
logger.debug(f"LLM输出: {content}")
logger.warning("LLM API key not configured")
```
类内部也可以将 logger 作为实例属性：`self.logger = get_logger("DIContainer")`

---

## Error Handling Patterns

### 分层异常类
为模块定义专属异常层次：
```python
class ConfigError(Exception): ...
class ConfigFileNotFoundError(ConfigError): ...
class ConfigParseError(ConfigError): ...
class ConfigValidationError(ConfigError): ...
```

### 降级策略
操作失败时提供降级路径，不直接抛出：
```python
except Exception as e:
    logger.error(f"LLM调用失败: {e}")
    return self._format_basic_info(list_product, detail_product)  # 降级返回基本信息
```

### 结构化错误返回（非异常场景）
使用 dataclass 返回验证结果，包含 `error_type`、`error_message`、`suggestions`：
```python
@dataclass
class ValidationResult:
    is_valid: bool
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    suggestions: List[str] = None
    file_info: Dict = None
```

---

## Thread Safety Patterns

### RLock 保护共享状态
所有共享可变状态用 `threading.RLock()` 保护：
```python
self._lock = threading.RLock()

with self._lock:
    self._services[key] = descriptor
```

### 双重检查锁定（单例创建）
避免在锁内执行耗时操作：
```python
if key in self._singletons:          # 第一次检查（无锁）
    return self._singletons[key]

with self._lock:
    if key in self._singletons:      # 第二次检查（有锁）
        return self._singletons[key]
    instance = descriptor.factory()
    self._singletons[key] = instance
    return instance
```

### 线程安全计数器（异步并发场景）
```python
from threading import Lock
counter_lock = Lock()

with counter_lock:
    progress.success += 1
    progress.current += 1
```

---

## Async Patterns

### asyncio.Semaphore 控制并发
```python
max_concurrent = 3
semaphore = asyncio.Semaphore(max_concurrent)

async def process_with_semaphore(product):
    async with semaphore:
        await process_single_product(product)

tasks = [process_with_semaphore(p) for p in products]
await asyncio.gather(*tasks)
```

### 请求限流延迟
```python
await asyncio.sleep(self.request_delay)  # 避免 API 限流
```

### 取消机制
使用 `threading.Event` 实现可取消的长时任务：
```python
self._cancellation_event = threading.Event()

def cancel(self): self._cancellation_event.set()
def is_cancelled(self): return self._cancellation_event.is_set()

# 在循环中检查
if self.is_cancelled():
    progress.cancelled = True
    break
```

---

## Dependency Injection Patterns

### 服务注册（configure_standard_services）
```python
# 检查是否已注册，避免重复注册
if not container.is_registered(ConnectionStatusManager):
    container.register_singleton(
        ConnectionStatusManager,
        factory=lambda: ConnectionStatusManager()
    )
```

### 懒代理（向后兼容）
旧代码通过 `_DIProxy` 无缝使用 DI 容器，无需修改调用方：
```python
# database/__init__.py
db_manager = _create_proxy(DatabaseManager)

# 调用方无需感知 DI 容器
from database import db_manager
db_manager.get_all_channels()
```

### 延迟导入（避免循环依赖）
在函数/方法内部导入，而非模块顶部：
```python
def configure_standard_services(config_instance=None):
    from core.connection_status import ConnectionStatusManager  # 延迟导入
    from database.db_manager import DatabaseManager
```

---

## Data Modeling Patterns

### SQLAlchemy ORM 规范
- 每个模型类有中文 docstring 说明用途
- 所有列带 `comment=` 参数
- 外键使用 `ondelete='CASCADE'`
- 唯一约束用 `UniqueConstraint` 在 `__table_args__` 中定义
- 时间戳列：`created_at`（`default=datetime.now`）+ `updated_at`（`onupdate=datetime.now`）
- 关联关系用 `relationship` + `back_populates` 双向绑定

```python
class ProductKnowledge(Base):
    __tablename__ = 'product_knowledge'
    __table_args__ = (
        UniqueConstraint('shop_id', 'goods_id', name='uix_product_knowledge_shop_goods'),
    )
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')
```

### Pydantic 配置模型
- 使用 `ConfigDict(arbitrary_types_allowed=True)`
- 所有字段带 `Field(default=..., description="...")`
- 嵌套配置用独立 BaseModel 类

```python
class LLMConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    model_name: str = Field(default="", description="模型名称")
    api_key: str = Field(default="", description="API密钥")
```

### Dataclass 用于进度/结果对象
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

---

## Configuration Access Pattern
统一通过 `get_config` 便捷函数访问，带默认值：
```python
from config import get_config

model_name = get_config("llm.model_name", "gpt-4o")
api_key = get_config("llm.api_key", "")
api_base = get_config("llm.api_base", None)
```
支持点号分隔的嵌套访问（`"llm.api_key"`）。

---

## Agent Tool Registration Pattern
工具通过 `@agent_tool` 装饰器注册，导入模块即触发注册：
```python
# customer_agent.py 中导入工具模块触发注册
from Agent.CustomerAgent.tools import (
    send_goods_link,                  # noqa: F401  — 注册 send_goods_link 工具
    move_conversation,                 # noqa: F401  — 注册 transfer_conversation 工具
)
```
每个工具文件独立，一个文件对应一个或一组相关工具。

---

## LLM Integration Pattern
使用 `AsyncOpenAI` 客户端，支持任意 OpenAI 兼容接口：
```python
client = AsyncOpenAI(
    api_key=api_key,
    base_url=api_base,
    timeout=120.0,
)
response = await client.chat.completions.create(
    model=model_name,
    messages=messages,
    temperature=0.3,
    response_format={"type": "json_object"},
)
```
LLM 调用失败时必须提供降级路径（返回基本信息或错误提示）。

---

## Build & Packaging Conventions
- 使用 `argparse` 提供 CLI 接口，带 `epilog` 示例
- 平台检测：`platform.system() == "Windows"`
- Windows 控制台编码：`kernel32.SetConsoleOutputCP(65001)`
- PyInstaller 优先使用 venv 内的可执行文件：`.venv/Scripts/pyinstaller.exe`
- 构建产物检查：验证 exe 存在后再报告成功

---

## Progress Reporting Pattern
长时任务通过回调函数上报进度：
```python
progress_callback: Optional[Callable[[SyncProgress], None]] = None

if progress_callback:
    progress_callback(progress)
```
进度对象包含阶段标识（`phase`）便于 UI 显示不同状态。
