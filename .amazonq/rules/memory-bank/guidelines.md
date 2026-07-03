# Development Guidelines: Agent-Customer

## Code Quality Standards

### 模块文档字符串
每个模块顶部必须有三引号文档字符串，说明模块职责：
```python
"""
模块名称

简要说明模块功能和职责。
如有重要约束（如初始化顺序），在此说明。
"""
```

### 类文档字符串
类必须有文档字符串，说明职责和核心特性：
```python
class Config:
    """
    线程安全的配置管理器

    特性：
    - 类型安全的配置访问
    - 配置验证
    - 线程安全
    """
```

### 方法文档字符串（Args/Returns 格式）
公共方法使用 Google 风格文档字符串：
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

### 私有方法命名
内部辅助方法以单下划线前缀：`_generate_coupon_id`, `_calculate_discount`, `_get_user_info`

### 常量命名
模块级常量使用全大写：`DEFAULT_DB_PATH`, `DEFAULT_TOKEN_WINDOW`, `ZIP_SIGNATURE`

---

## Structural Conventions

### 返回值统一格式
业务方法统一返回 `dict`，包含 `success` 字段：
```python
# 成功
return {"success": True, "coupon_id": coupon_id, "total_quantity": total_quantity}

# 失败
return {"success": False, "error_msg": "优惠券不存在"}
```

### 异常处理模式
所有业务方法用 `try/except Exception` 包裹，失败时记录日志并返回错误字典：
```python
def some_method(self) -> Dict[str, Any]:
    try:
        # 业务逻辑
        return {"success": True, ...}
    except Exception as e:
        self.logger.error(f"操作失败: {str(e)}")
        return {"success": False, "error_msg": str(e)}
```

### 数据类用于验证结果
使用 `@dataclass` 封装验证结果，避免裸字典：
```python
@dataclass
class ValidationResult:
    is_valid: bool
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    suggestions: List[str] = None
    file_info: Dict = None

    def __post_init__(self):
        if self.suggestions is None:
            self.suggestions = []
```

### 继承链
- 处理器：`MessageHandler` → `BaseHandler` → 具体处理器
- 验证器：`FileValidator` → `ExcelValidator`（调用 `super().validate_basic()` 后扩展）
- API 管理器：`BaseRequest` → `VoucherManager`
- Agent：`Bot` → `CustomerAgent`

---

## Logging Pattern

统一使用 Loguru，每个类在 `__init__` 中获取 logger：
```python
from utils.logger_loguru import get_logger

class MyClass:
    def __init__(self):
        self.logger = get_logger("MyClass")
```

模块级 logger（非类方法中使用）：
```python
logger = get_logger("CustomerAgent")
```

日志级别使用规范：
- `logger.info(f"操作成功: {detail}")` — 正常业务流程
- `logger.error(f"操作失败: {str(e)}")` — 异常捕获
- `logger.warning(f"达到上限 {limit}，强制结束")` — 警告
- `logger.debug(f"注册服务: {key}")` — 调试信息

---

## Dependency Injection Patterns

### 服务注册（在 configure_standard_services 中）
```python
container.register_singleton(
    MyService,
    factory=lambda: MyService()
)
```

### 服务获取（通过 DI 代理）
```python
# database/__init__.py 中定义代理
from core.service_providers import _create_proxy
from database.db_manager import DatabaseManager
db_manager = _create_proxy(DatabaseManager)

# 其他模块直接导入使用
from database import db_manager
db_manager.get_session()
```

### 避免循环依赖
使用延迟导入（lazy import）：
```python
def some_method(self):
    from some.module import SomeClass  # 在方法内部导入
    return SomeClass()
```

---

## Database Patterns

### SQLAlchemy ORM 模型规范
- 每个模型类有中文 docstring 说明用途
- 所有 Column 必须有 `comment` 参数
- 时间字段统一使用 `default=datetime.now` 和 `onupdate=datetime.now`
- 唯一约束使用 `__table_args__` 中的 `UniqueConstraint`
- 关联关系使用 `relationship` 并指定 `back_populates` 或 `backref`

```python
class ProductKnowledge(Base):
    """产品知识表，存储LLM提取的商品详细知识"""
    __tablename__ = 'product_knowledge'

    id = Column(Integer, primary_key=True, autoincrement=True)
    shop_id = Column(Integer, ForeignKey('shops.id', ondelete='CASCADE'),
                     nullable=False, comment='店铺ID')
    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now,
                        comment='更新时间')

    __table_args__ = (
        UniqueConstraint('shop_id', 'goods_id', name='uix_product_knowledge_shop_goods'),
    )
```

### Session 使用模式
```python
with db_manager.get_session() as session:
    obj = session.query(Model).filter_by(field=value).first()
    session.add(new_obj)
    session.commit()
```

---

## Configuration Patterns

### 访问配置
```python
from config import config

# 点号分隔嵌套访问
api_key = config.get("llm.api_key")
db_path = config.get("db_path", "./temp/channel_shop.db")

# 获取 Pydantic 验证后的模型
cfg = config.get_model()  # 返回 ConfigModel
```

### Pydantic 配置模型（v2 API）
```python
from pydantic import BaseModel, Field, ConfigDict

class LLMConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    model_name: str = Field(default="", description="模型名称")
    api_key: str = Field(default="", description="API密钥")
```

---

## Async Patterns

### Agent 异步初始化（延迟初始化模式）
```python
class CustomerAgent(Bot):
    def __init__(self):
        self._is_initialized = False
        self._llm_client: Optional[LLMClient] = None  # 延迟初始化

    async def async_reply(self, query: str, context: Context = None) -> Reply:
        if not self._is_initialized:
            if not await self.initialize_async():
                return Reply(ReplyType.TEXT, "初始化失败")
        # 业务逻辑
```

### 并行工具执行
```python
tool_results = await self._tool_executor.execute_parallel(
    response.tool_calls, dependencies
)
```

### 线程安全（RLock）
```python
self._lock = threading.RLock()

def thread_safe_method(self):
    with self._lock:
        # 临界区操作
```

---

## Bridge / Context Patterns

### 创建拼多多上下文
```python
context = Context.create_pinduoduo_context(
    content=message_text,
    msg_id=msg_id,
    from_uid=from_uid,
    shop_id=shop_id,
    user_msg_type=ContextType.TEXT,
    channel_type=ChannelType.PINDUODUO,
)
```

### 从 Context 提取用户信息
```python
from_uid = getattr(context.kwargs, "from_uid", None)
shop_id = getattr(context.kwargs, "shop_id", None)
```

---

## File & Path Patterns

### 路径使用 pathlib.Path
```python
from pathlib import Path

spec_file = Path("scripts/agent_customer.spec")
if not spec_file.exists():
    raise FileNotFoundError(f"找不到 spec 文件: {spec_file}")
```

### 支持 PyInstaller 打包后的路径
```python
def get_project_root():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS).parent
    return Path(__file__).resolve().parent
```

### 原子文件写入
```python
temp_path = self.config_path.with_suffix('.tmp')
with open(temp_path, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=4)
temp_path.replace(self.config_path)  # 原子重命名
```

---

## Tool Registration Pattern（Agent 工具）

工具通过 `@agent_tool` 装饰器注册，在 `customer_agent.py` 中通过导入触发注册：
```python
# tools/search_customer_service_knowledge.py
from Agent.CustomerAgent.custom.tool_decorator import agent_tool

@agent_tool
def search_customer_service_knowledge(query: str, shop_id: str) -> str:
    """搜索客服知识库"""
    ...

# customer_agent.py 中导入触发注册
from Agent.CustomerAgent.tools import search_customer_service_knowledge  # noqa: F401
```

获取已注册工具列表：
```python
from Agent.CustomerAgent.custom.tool_decorator import get_tools_for_llm
tools = get_tools_for_llm()
```

---

## Naming Conventions

| 类型 | 规范 | 示例 |
|------|------|------|
| 类名 | PascalCase | `CustomerAgent`, `VoucherManager` |
| 方法/函数 | snake_case | `get_voucher_code`, `verify_voucher` |
| 私有方法 | `_snake_case` | `_generate_coupon_id`, `_calculate_discount` |
| 常量 | UPPER_SNAKE_CASE | `DEFAULT_MAX_LOOPS`, `ZIP_SIGNATURE` |
| 模块文件 | snake_case | `voucher_manager.py`, `di_container.py` |
| 数据库表名 | snake_case 复数 | `product_knowledge`, `voucher_logs` |
| 唯一约束名 | `uix_表名_字段` | `uix_product_knowledge_shop_goods` |

---

## Import Order Convention

```python
# 1. 标准库
import sys
import threading
from pathlib import Path
from typing import Dict, Any, Optional

# 2. 第三方库
from pydantic import BaseModel, Field
from sqlalchemy import Column, Integer, String

# 3. 本项目模块（按层级：core → database → bridge → Agent → utils）
from config import config
from database import db_manager
from bridge.context import Context
from utils.logger_loguru import get_logger
```

---

## Error Handling Hierarchy

```
ConfigError (基类)
├── ConfigFileNotFoundError
├── ConfigParseError
└── ConfigValidationError
```

自定义异常继承自具体业务基类，不直接继承 `Exception`。

---

## Windows-Specific Considerations

- 文件编码：读写文件统一使用 `encoding='utf-8'`，Windows bat 文件使用 `encoding='gbk'`
- 控制台编码：脚本入口设置 `kernel32.SetConsoleOutputCP(65001)`
- 路径分隔符：统一使用 `pathlib.Path`，避免硬编码 `\\`
- subprocess：Windows 上指定 `encoding='utf-8'` 避免 Unicode 问题
