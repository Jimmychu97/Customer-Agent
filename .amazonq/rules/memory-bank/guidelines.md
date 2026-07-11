# Development Guidelines

## Code Quality Standards

### Module Docstrings
每个模块顶部必须有三引号文档字符串，说明模块职责：
```python
"""
模块功能描述

关键说明或注意事项
"""
```

### `__init__.py` 模式
- 空 `__init__.py`：纯命名空间包（如 `Agent/`, `Agent/CustomerAgent/`）
- 导出型 `__init__.py`：显式声明 `__all__`，通过相对导入聚合子模块公共 API：
```python
from .base import BaseHandler
from .ai_handler import AIReplyHandler

__all__ = ['BaseHandler', 'AIReplyHandler']
```
- DI 代理型 `__init__.py`：通过 `_create_proxy` 提供向后兼容的模块级单例：
```python
from core.service_providers import _create_proxy
db_manager = _create_proxy(DatabaseManager)
```

### 类命名与结构
- 类名使用 PascalCase，文件名使用 snake_case
- 每个类有简短的类级文档字符串说明职责
- 子组件使用延迟初始化（`Optional[Type] = None`），在 `initialize_async()` 中赋值

### 日志规范
统一使用 `utils.logger_loguru` 中的 `get_logger`，**不直接使用 `logging` 模块**：
```python
from utils.logger_loguru import get_logger
logger = get_logger("ModuleName")  # 模块级 logger
# 或在类中
self.logger = get_logger("ClassName")
```
日志内容截断保护隐私（内容预览限 50 字符）：
```python
content_preview = str(context.content)[:50] + "..." if context.content else ""
```

---

## Dependency Injection Pattern

### 服务注册（`core/di_container.py`）
所有核心服务通过 DI 容器统一管理，在 `app.py` 最早期调用 `configure_standard_services()`：
```python
from core.di_container import configure_standard_services
configure_standard_services(_app_config)
```

注册方式：
```python
container.register_singleton(ServiceType, factory=lambda: ServiceType())
container.register_transient(ServiceType, implementation_type=ConcreteType)
```

获取服务：
```python
from core.di_container import container
instance = container.get(ServiceType)
```

### 双重检查锁定（线程安全单例）
```python
if key in self._singletons:
    return self._singletons[key]
with self._lock:
    if key in self._singletons:  # 二次检查
        return self._singletons[key]
    instance = factory()
    self._singletons[key] = instance
```

---

## Async Patterns

### 异步初始化
组件使用 `initialize_async()` 延迟初始化，避免构造函数中执行 IO：
```python
async def initialize_async(self) -> bool:
    if self._is_initialized:
        return True
    try:
        # ... 初始化逻辑
        self._is_initialized = True
        return True
    except Exception as e:
        logger.error(f"初始化失败: {e}")
        return False
```

### 延迟初始化调用点
```python
async def async_reply(self, query: str, context: Context = None) -> Reply:
    if not self._is_initialized:
        if not await self.initialize_async():
            return Reply(ReplyType.TEXT, "初始化失败，请检查配置。")
```

### 并行工具执行
多个工具调用使用 `asyncio.gather` 并行执行（见 `ToolExecutor.execute_parallel`）。

---

## Configuration Pattern

### 访问配置
使用点号分隔的嵌套键访问：
```python
from config import config
value = config.get('llm.api_key', default='')
config.set('llm.api_key', 'new_value', save=True)
```

### 原子保存
配置保存使用临时文件 + `Path.replace()` 原子重命名，防止写入中断导致损坏：
```python
temp_path = self.config_path.with_suffix('.tmp')
with open(temp_path, 'w', encoding='utf-8') as f:
    json.dump(self._config, f, ensure_ascii=False, indent=4)
temp_path.replace(self.config_path)
```

### Pydantic 配置模型
所有配置段使用 Pydantic `BaseModel` 定义，带 `Field(default=..., description=...)`：
```python
class LLMConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    api_key: str = Field(default="", description="API密钥")
```

---

## Handler Chain Pattern

### 处理器基类
所有消息处理器继承 `BaseHandler`（继承自 `MessageHandler`）：
```python
class MyHandler(BaseHandler):
    def __init__(self, name: Optional[str] = None):
        super().__init__()
        self.name = name or self.__class__.__name__
```

### 统一日志方法
处理器使用 `log_message()` 记录操作，不直接记录完整消息内容：
```python
await self.log_message(context, "处理完成", extra_info="附加信息")
```

---

## Import Conventions

### 避免循环依赖
业务模块间使用函数内延迟导入（lazy import）：
```python
def some_method(self):
    from database.knowledge_service import KnowledgeService  # 延迟导入
    service = KnowledgeService()
```

### 工具注册触发
Agent 工具通过导入触发 `@agent_tool` 装饰器注册，使用 `# noqa: F401` 注释：
```python
from Agent.CustomerAgent.tools import (
    send_goods_link,   # noqa: F401  — 注册 send_goods_link 工具
    move_conversation, # noqa: F401  — 注册 transfer_conversation 工具
)
```

### 向后兼容重导出
废弃模块保留原有导入路径，通过重导出维持兼容性：
```python
# utils/logging_context.py - 向后兼容而保留
from utils.logger_loguru import log_with_ctx, format_conn_key
__all__ = ["log_with_ctx", "format_conn_key"]
```

---

## Error Handling

### 降级策略
关键路径失败时提供降级回复，不抛出异常到上层：
```python
except Exception as e:
    logger.error(f"操作失败: {e}")
    return Reply(ReplyType.TEXT, "抱歉，我现在无法回复，请稍后再试。")
```

### 用户信息提取防御
访问 context 属性时使用 `getattr` + `hasattr` 防御：
```python
def _get_user_info(self, context: Context) -> str:
    try:
        if hasattr(context, 'kwargs') and context.kwargs:
            from_uid = getattr(context.kwargs, 'from_uid', None)
        return "用户:unknown"
    except Exception:
        return "用户:unknown"
```

---

## Agent Loop Pattern

CustomerAgent 的核心循环结构（`_run_agent_loop`）：
1. 调用 LLM（`llm_client.chat`）
2. 无 tool_calls → 返回内容
3. 有 tool_calls → 保存 assistant 消息
4. 检查循环上限（`max_loops`），超限强制结束
5. 并行执行工具（`tool_executor.execute_parallel`）
6. 将工具结果追加到 messages
7. 回到步骤 1

Session ID 格式：`{channel_type.value}{from_uid}`，降级时使用 `fallback_{hash}`。

---

## Naming Conventions

| 类型 | 规范 | 示例 |
|------|------|------|
| 类 | PascalCase | `CustomerAgent`, `DIContainer` |
| 函数/方法 | snake_case | `async_reply`, `get_logger` |
| 常量 | UPPER_SNAKE_CASE | `DEFAULT_MAX_LOOPS`, `DEFAULT_TOKEN_WINDOW` |
| 私有属性 | 单下划线前缀 | `self._config`, `self._lock` |
| 模块文件 | snake_case | `customer_agent.py`, `di_container.py` |
| 中文注释 | 优先使用中文 | 所有注释和文档字符串使用中文 |
