# Development Guidelines

## Code Quality Standards

### Module Docstrings
每个模块顶部必须有三引号文档字符串，说明模块职责：
```python
"""
模块名称
========
简短描述模块功能和职责。
"""
```

### Class & Method Docstrings
- 类必须有说明其职责的文档字符串
- 公共方法必须有 Args/Returns 说明（中文）
- 私有方法（`_` 前缀）可简化

### Logging Pattern
统一使用 `utils.logger_loguru.get_logger`，每个类/模块独立 logger：
```python
from utils.logger_loguru import get_logger
logger = get_logger("ModuleName")          # 模块级
# 或在类中
self.logger = get_logger(self.__class__.__name__)  # 类级
```
日志消息使用中文，格式：`f"操作描述: key={value}"`

### Error Handling
- 所有异步方法必须有 `try/except Exception as e` 兜底
- 捕获异常后记录 `logger.error(f"操作失败: {e}")`
- 业务方法失败返回 `False` 或 `None`，不向上抛出（除非是初始化阶段）
- 关键路径提供 fallback 回复（如 AI 回复失败时返回固定文字）

---

## Naming Conventions

### Files & Directories
- 模块文件：`snake_case.py`
- 类名：`PascalCase`
- 函数/方法：`snake_case`
- 私有方法：`_snake_case`
- 常量：`UPPER_SNAKE_CASE`

### Specific Patterns
- 处理器类：`XxxHandler`（继承 `MessageHandler` 或 `BaseHandler`）
- 管理器类：`XxxManager`
- 服务类：`XxxService`
- 配置类：`XxxConfig`（Pydantic BaseModel）
- 工具参数模型：`XxxParams`（Pydantic BaseModel）

---

## Architectural Patterns

### 1. Agent Tool Registration（@agent_tool 装饰器）
工具通过 `@agent_tool` 装饰器注册到全局 `TOOL_REGISTRY`，参数用 Pydantic 模型定义：
```python
from pydantic import BaseModel, Field
from Agent.CustomerAgent.custom.tool_decorator import agent_tool

class MyToolParams(BaseModel):
    shop_id: str = Field(description="店铺ID")
    query: str = Field(description="查询关键词")

@agent_tool(
    name="my_tool",
    description="工具功能描述（传给LLM）",
    param_model=MyToolParams,
)
def my_tool(params: MyToolParams) -> str:
    ...
    return "结果字符串"
```
工具函数必须接收 Pydantic 模型实例，返回字符串。
工具模块在 `customer_agent.py` 中通过 `# noqa: F401` 导入触发注册。

### 2. Message Handler Chain（处理器链）
处理器继承 `MessageHandler`（ABC），实现 `can_handle` 和 `handle`：
```python
from Message.core.handlers import MessageHandler
from bridge.context import Context

class MyHandler(MessageHandler):
    def can_handle(self, context: Context) -> bool:
        return context.type == ContextType.TEXT

    async def handle(self, context: Context, metadata: dict) -> bool:
        # 处理逻辑
        return True  # True=已处理，停止链；False=继续下一个处理器
```
处理器链顺序：`PreprocessorHandler → KeywordHandler → VoucherHandler → AIHandler`

### 3. DI Container Usage
服务注册和获取：
```python
from core.di_container import container

# 注册（在 configure_standard_services 中）
container.register_singleton(MyService, factory=lambda: MyService())

# 获取
service = container.get(MyService)
```
向后兼容代理（模块 `__init__.py` 中）：
```python
from core.service_providers import _create_proxy
db_manager = _create_proxy(DatabaseManager)  # 透明代理，调用方无感知
```

### 4. Config Access Pattern
```python
from config import config

# 点号分隔的嵌套访问
api_key = config.get("llm.api_key", "")
enabled = config.get("business_hours.enabled", True)

# 类型安全访问（推荐）
cfg_model = config.get_model()
api_key = cfg_model.llm.api_key

# 设置并保存
config.set("llm.api_key", "new_key", save=True)
```

### 5. Database Session Pattern
使用 `with session_scope()` 上下文管理器，或 `with get_session() as session`：
```python
from database import db_manager

with db_manager.session_scope() as session:
    result = session.query(MyModel).filter(...).first()
    session.commit()
```
KnowledgeService 使用 `with self.get_session() as session` 模式，
SQLAlchemy 2.x 风格优先使用 `select()` + `session.scalar()`/`session.scalars()`。

### 6. Context Creation（拼多多消息）
```python
from bridge.context import Context, ContextType, ChannelType

context = Context.create_pinduoduo_context(
    content="用户消息内容",
    from_uid="用户UID",
    shop_id="店铺ID",
    user_id="客服ID",
    user_msg_type=ContextType.TEXT,
    channel_type=ChannelType.PINDUODUO,
)
```

### 7. Async in Qt Thread
每个 `AutoReplyThread` 在独立线程中运行 asyncio 事件循环，
跨线程调用使用 `utils/async_helper.py` 提供的桥接工具：
```python
from utils.async_helper import run_coroutine_in_thread
result = run_coroutine_in_thread(some_coroutine(), loop)
```
在处理器中调用同步 API 使用 `asyncio.to_thread`：
```python
result = await asyncio.to_thread(lambda: SyncAPI().call())
```

---

## Module `__init__.py` Conventions

- 空 `__init__.py`：仅作包标识，不导出任何内容（如 `Agent/__init__.py`）
- 代理 `__init__.py`：创建 DI 代理并声明 `__all__`（如 `database/__init__.py`）
- 聚合 `__init__.py`：从子模块重新导出，声明 `__all__`（如 `Message/handlers/__init__.py`）
- 向后兼容 `__init__.py`：重新导出已迁移的函数（如 `utils/logging_context.py`）

---

## Pydantic Usage

- 配置模型继承 `BaseModel`，使用 `Field(default=..., description="...")`
- 需要任意类型时添加 `model_config = ConfigDict(arbitrary_types_allowed=True)`
- 枚举继承 `str, Enum`，使 JSON 序列化直接输出字符串值
- `ContextType` 和 `ChannelType` 都实现了 `__str__` 返回 `.value`

---

## Import Conventions

- 避免循环依赖：业务模块间使用延迟导入（函数内 `import`）
- 工具模块在 `customer_agent.py` 中用 `# noqa: F401` 注释说明导入目的
- 从 DI 容器获取服务时在函数内延迟导入：
  ```python
  def some_method(self):
      from core.di_container import container
      from Agent.CustomerAgent.custom.customer_agent import CustomerAgent
      bot = container.get(CustomerAgent)
  ```

---

## Initialization Order (Critical)

`app.py` 强制保证以下顺序，新增模块必须遵守：
1. `from config import config` — 配置最先
2. `from database import db_manager` — 数据库代理（懒加载）
3. `configure_standard_services(config)` — DI 容器注册
4. UI 模块延迟导入（在 `main()` 函数内）

---

## Frequently Used Idioms

```python
# 安全获取枚举值（兼容字符串和枚举）
channel_str = channel_type.value if hasattr(channel_type, 'value') else str(channel_type)

# 内容截断（日志/展示）
preview = str(content)[:50] + "..." if content else ""

# 双重检查锁定（单例）
if key not in self._singletons:
    with self._lock:
        if key not in self._singletons:
            self._singletons[key] = factory()

# 原子配置保存（临时文件+重命名）
temp_path = config_path.with_suffix('.tmp')
with open(temp_path, 'w') as f:
    json.dump(data, f)
temp_path.replace(config_path)
```
