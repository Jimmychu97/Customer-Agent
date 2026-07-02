# Project Structure: Agent-Customer

## Directory Layout

```
Customer-Agent/
├── app.py                  # 应用入口，全局单例初始化顺序管理
├── config.py               # 配置管理（Pydantic 验证 + 线程安全 + 原子保存）
├── config.json             # 运行时配置文件（首次运行自动生成）
├── pyproject.toml          # 项目依赖（uv 管理）
│
├── Agent/                  # AI 代理模块（自研 Agent 框架）
│   ├── bot.py              # Agent 入口
│   └── CustomerAgent/
│       ├── custom/         # Agent 核心实现
│       │   ├── customer_agent.py   # 主 Agent 逻辑（多轮工具调用）
│       │   ├── llm_client.py       # LLM 客户端（OpenAI 兼容）
│       │   ├── session_manager.py  # 会话上下文管理
│       │   ├── tool_executor.py    # 工具执行器
│       │   ├── tool_decorator.py   # 工具注册装饰器
│       │   ├── message_builder.py  # 消息构建
│       │   └── agent_config.py     # Agent 配置
│       └── tools/          # Agent 工具集（每个工具独立文件）
│           ├── search_customer_service_knowledge.py
│           ├── get_product_knowledge.py
│           ├── get_product_list.py
│           ├── send_goods_link.py
│           ├── move_conversation.py
│           ├── coupon_query.py
│           ├── get_voucher_code.py
│           ├── use_voucher.py
│           └── verify_voucher.py
│
├── Channel/                # 渠道集成层
│   ├── channel.py          # 渠道抽象基类
│   └── pinduoduo/
│       ├── pdd_channel.py  # 拼多多渠道主类
│       ├── pdd_login.py    # Playwright 自动登录
│       ├── pdd_message.py  # 消息模型
│       ├── cookie_cache.py # Cookie 缓存
│       ├── cookie_utils.py # Cookie 工具
│       ├── core/           # 连接核心
│       │   ├── pdd_connection.py   # WebSocket 连接管理
│       │   ├── pdd_lifecycle.py    # 连接生命周期
│       │   ├── pdd_message_handler.py  # 消息处理
│       │   ├── pdd_config.py       # 渠道配置
│       │   ├── pdd_status.py       # 连接状态
│       │   └── pdd_utils.py        # 工具函数
│       └── utils/API/      # 拼多多 API 封装
│
├── Message/                # 消息处理层（队列 + 处理器链）
│   ├── message.py          # 消息模型
│   ├── core/
│   │   ├── queue.py        # 消息队列管理（模块级单例 queue_manager）
│   │   ├── consumer.py     # 消息消费者
│   │   └── handlers.py     # 处理器链管理
│   ├── handlers/           # 具体处理器
│   │   ├── base.py         # 处理器基类
│   │   ├── preprocessor.py # 预处理（过滤/格式化）
│   │   ├── keyword_handler.py  # 关键词匹配转人工
│   │   ├── ai_handler.py   # AI 回复处理器
│   │   └── voucher_handler.py  # 核销 + GSX 验机
│   └── models/
│       └── queue_models.py # 队列数据模型
│
├── bridge/                 # 渠道与消息处理桥接层
│   ├── context.py          # Context/ContextType/ChannelType 模型
│   └── reply.py            # 回复模型
│
├── core/                   # 核心基础设施
│   ├── di_container.py     # DI 容器（SINGLETON/TRANSIENT/SCOPED）
│   ├── service_providers.py # _DIProxy 懒代理（向后兼容层）
│   ├── connection_status.py # 连接状态管理器（跨实例共享）
│   ├── base_service.py     # 服务基类
│   └── cache.py            # 缓存管理
│
├── database/               # 数据持久层
│   ├── models.py           # SQLAlchemy ORM 模型
│   ├── db_manager.py       # 数据库管理器（通过 DI 注册）
│   ├── knowledge_service.py # 知识库服务（jieba 分词检索）
│   ├── product_sync.py     # 商品知识同步（多模态 LLM 提取）
│   └── connection_pool.py  # 连接池管理
│
├── ui/                     # PyQt6 用户界面
│   ├── main_ui.py          # 主窗口
│   ├── auto_reply/         # 自动回复模块（含线程管理）
│   │   ├── ui.py           # 自动回复页面
│   │   ├── manager.py      # 回复管理器
│   │   ├── threads.py      # AutoReplyThread（每账号独立线程）
│   │   └── card.py         # 账号卡片组件
│   ├── goods_ui.py         # 商品管理页
│   ├── keyword_ui.py       # 关键词管理页
│   ├── Knowledge_ui.py     # 知识库管理页
│   ├── setting_ui.py       # 设置页
│   ├── user_ui.py          # 账号管理页
│   ├── log_ui.py           # 日志查看页
│   ├── message_ui.py       # 消息查看页
│   └── voucher_ui.py       # 核销管理页
│
├── utils/                  # 工具模块
│   ├── logger_loguru.py    # Loguru 日志封装（get_logger）
│   ├── logger_config.py    # 日志配置
│   ├── logging_context.py  # 日志上下文
│   ├── async_helper.py     # 异步辅助工具
│   ├── encoding_helper.py  # 编码工具
│   ├── file_validator.py   # 文件验证
│   ├── path_utils.py       # 路径工具
│   ├── resource_manager.py # 资源管理
│   ├── runtime_path.py     # 运行时路径（支持 PyInstaller）
│   └── volcengine_models.py # 火山引擎模型配置
│
└── scripts/                # 构建与测试脚本
    ├── build_exe.py        # PyInstaller 打包脚本
    ├── build_win_exe.py    # Windows 打包入口
    ├── agent_customer.spec # PyInstaller spec 文件
    ├── install_playwright.py
    ├── test_gsx.py
    └── test_sku.py
```

## Core Components & Relationships

### 初始化顺序（app.py 强制约束）
```
config → DI容器(configure_standard_services) → db_manager → logger
→ queue_manager → message_consumer_manager → status_manager → cache_manager
```

### 数据流
```
拼多多 WebSocket
    → PDDChannel (Channel层)
    → bridge.Context (桥接层，统一消息格式)
    → MessageQueue (Message/core/queue.py)
    → MessageConsumer (处理器链)
        → preprocessor → keyword_handler → voucher_handler → ai_handler
    → CustomerAgent (Agent层，多轮工具调用)
    → 拼多多 API 发送回复
```

### DI 容器管理的服务
- `ConnectionStatusManager` — 跨 PDDChannel 实例共享连接状态
- `DatabaseManager` — 数据库管理器
- `KnowledgeService` — 知识库服务

### 模块级单例（不经过 DI 容器）
- `queue_manager`（Message/core/queue.py）
- `message_consumer_manager`
- `cache_manager`
- `config`（config.py）

## Architectural Patterns

### 1. 依赖注入 + 懒代理
`_DIProxy`（core/service_providers.py）将 `from database import db_manager` 这类旧式导入透明代理到 DI 容器，实现零迁移成本的向后兼容。

### 2. 处理器链（Chain of Responsibility）
Message/handlers/ 中每个处理器继承 base.py，按顺序执行，支持短路（关键词匹配后不再调用 AI）。

### 3. 渠道抽象
Channel/channel.py 定义抽象基类，PDDChannel 实现具体渠道逻辑，bridge/context.py 提供统一的 Context 模型屏蔽渠道差异。

### 4. 每账号独立线程
ui/auto_reply/threads.py 中每个 AutoReplyThread 持有独立的 PDDChannel 实例，共享 ConnectionStatusManager 单例。

### 5. 配置原子保存
config.py 使用临时文件 + os.replace() 原子重命名，防止写入中断导致配置损坏。
