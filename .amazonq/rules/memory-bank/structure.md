# Project Structure

## Directory Overview

```
Customer-Agent/
├── app.py                  # 应用入口，全局单例初始化顺序管理
├── config.py               # 配置管理（Pydantic 验证 + 线程安全）
├── config.json             # 运行时配置文件（自动生成）
├── pyproject.toml          # 项目依赖声明（uv 管理）
│
├── Agent/                  # AI 代理模块
│   ├── bot.py              # Bot 工厂/入口
│   └── CustomerAgent/
│       ├── custom/         # 自研 Agent 框架核心
│       │   ├── customer_agent.py   # Agent 主逻辑（多轮工具调用循环）
│       │   ├── llm_client.py       # OpenAI 兼容 API 客户端
│       │   ├── session_manager.py  # 会话上下文（消息历史）管理
│       │   ├── tool_executor.py    # 工具注册与执行
│       │   ├── tool_decorator.py   # @tool 装饰器定义
│       │   ├── message_builder.py  # LLM 消息构造辅助
│       │   └── agent_config.py     # Agent 配置项
│       └── tools/          # Agent 工具集
│           ├── get_product_list.py
│           ├── get_product_knowledge.py
│           ├── send_goods_link.py
│           ├── search_customer_service_knowledge.py
│           ├── move_conversation.py
│           ├── coupon_query.py
│           ├── get_voucher_code.py
│           ├── use_voucher.py
│           └── verify_voucher.py
│
├── Channel/                # 渠道集成
│   ├── channel.py          # 渠道抽象基类
│   └── pinduoduo/
│       ├── pdd_channel.py  # 拼多多渠道主类
│       ├── pdd_login.py    # Playwright 自动化登录
│       ├── pdd_message.py  # 消息模型
│       ├── cookie_cache.py # Cookie 缓存管理
│       ├── cookie_utils.py # Cookie 工具函数
│       ├── core/           # 拆分的连接核心模块
│       │   ├── pdd_connection.py   # WebSocket 连接管理
│       │   ├── pdd_lifecycle.py    # 连接生命周期
│       │   ├── pdd_status.py       # 连接状态
│       │   ├── pdd_message_handler.py  # 消息解析处理
│       │   ├── pdd_config.py       # 连接配置
│       │   └── pdd_utils.py        # 工具函数
│       └── utils/API/      # 拼多多 API 封装
│
├── Message/                # 消息处理系统
│   ├── message.py          # 消息模型
│   ├── core/
│   │   ├── queue.py        # 异步消息队列（queue_manager 单例）
│   │   ├── consumer.py     # 消息消费者（message_consumer_manager 单例）
│   │   └── handlers.py     # 处理器链调度
│   ├── handlers/           # 具体处理器
│   │   ├── base.py         # 处理器基类
│   │   ├── preprocessor.py # 预处理器（过滤/格式化）
│   │   ├── ai_handler.py   # AI 回复处理器
│   │   ├── keyword_handler.py  # 关键词处理器
│   │   └── voucher_handler.py  # 券码处理器
│   └── models/
│       └── queue_models.py # 队列消息数据模型
│
├── bridge/                 # 渠道与消息处理的桥接层
│   ├── context.py          # 入站消息上下文模型（Pydantic）
│   └── reply.py            # 出站回复模型
│
├── core/                   # 核心基础服务
│   ├── di_container.py     # 依赖注入容器（DIContainer）
│   ├── service_providers.py    # _DIProxy 懒代理（向后兼容层）
│   ├── connection_status.py    # ConnectionStatusManager 单例
│   ├── cache.py            # 缓存管理器（cache_manager 单例）
│   ├── base_service.py     # 服务基类
│   └── __init__.py         # 导出 container
│
├── database/               # 数据持久化
│   ├── db_manager.py       # SQLAlchemy 数据库管理器（DI 单例）
│   ├── models.py           # ORM 模型定义
│   ├── knowledge_service.py    # 知识库 CRUD 服务（DI 单例）
│   ├── product_sync.py     # 拼多多商品同步服务
│   └── connection_pool.py  # 数据库连接池配置
│
├── ui/                     # PyQt6 用户界面
│   ├── main_ui.py          # 主窗口
│   ├── auto_reply/         # 自动回复功能模块（含线程管理）
│   ├── goods_ui.py         # 商品管理页
│   ├── knowledge_ui.py     # 知识库管理页
│   ├── keyword_ui.py       # 关键词配置页
│   ├── setting_ui.py       # 设置页
│   ├── log_ui.py           # 日志查看页
│   └── voucher_ui.py       # 优惠券管理页
│
├── utils/                  # 通用工具
│   ├── logger_loguru.py    # Loguru 日志封装（get_logger 工厂）
│   ├── logging_context.py  # 日志上下文管理
│   ├── async_helper.py     # 异步辅助（Qt-asyncio 桥接）
│   ├── path_utils.py       # 路径工具（打包兼容）
│   ├── runtime_path.py     # 运行时路径解析
│   ├── encoding_helper.py  # 编码处理
│   ├── file_validator.py   # 文件校验
│   └── resource_manager.py # 资源管理
│
└── scripts/                # 构建脚本
    ├── build_win_exe.py    # Windows exe 打包
    └── agent_customer.spec # PyInstaller spec 文件
```

## Core Components & Relationships

### 初始化顺序（app.py 强制规定）
```
config → DIContainer.configure_standard_services() → db_manager → UI
```

### 数据流
```
WebSocket (PDD) → pdd_message_handler → Message Queue → Consumer
    → Handler Chain [preprocessor → keyword → AI → voucher]
    → bridge.Reply → PDD API 发送
```

### 依赖注入模式
- `DIContainer`（core/di_container.py）管理 `ConnectionStatusManager`、`DatabaseManager`、`KnowledgeService` 的单例
- `_DIProxy` 代理类提供向后兼容（`from database import db_manager` 即可使用）
- 其他服务（`queue_manager`、`cache_manager`）通过模块级单例直接使用

### Bridge 层（渠道解耦）
- `Context`（Pydantic BaseModel）：入站消息的统一表示，携带渠道专用 kwargs（如 `PinduoduoKwargs`）
- `Reply`：出站回复的统一表示
- 使得 Message 处理层与具体渠道实现解耦

## Architectural Patterns

- **DI 容器**：自研轻量级 DIContainer，支持 SINGLETON / TRANSIENT / SCOPED 三种生命周期
- **处理器链（Chain of Responsibility）**：消息处理通过 handlers 链路依次传递
- **代理模式**：_DIProxy 实现懒加载向后兼容代理
- **模块级单例**：部分服务（queue_manager 等）直接在模块中定义单例，无需 DI 注册
- **桥接模式**：bridge 层将 Channel 与 Message 处理解耦
- **自研 Agent 循环**：LLM 调用 → 工具执行 → 结果反馈 → 继续调用，直到无工具调用为止
