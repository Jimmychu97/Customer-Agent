# Project Structure

## Directory Layout

```
Customer-Agent/
├── app.py                  # 应用入口，全局单例初始化顺序管理
├── config.py               # 配置管理（Pydantic 验证 + 线程安全）
├── config.json             # 运行时配置文件（首次运行自动生成）
├── pyproject.toml          # 项目依赖（uv 管理）
│
├── Agent/                  # AI 代理模块
│   ├── bot.py              # Agent 入口，对外暴露 run_agent()
│   └── CustomerAgent/
│       ├── custom/         # 核心框架
│       │   ├── customer_agent.py   # Agent 主循环（多轮工具调用）
│       │   ├── llm_client.py       # OpenAI 兼容 LLM 客户端
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
│           ├── verify_voucher.py
│           ├── get_voucher_code.py
│           ├── use_voucher.py
│           └── coupon_query.py
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
├── Message/                # 消息处理层
│   ├── message.py          # 消息模型
│   ├── core/
│   │   ├── queue.py        # 异步消息队列
│   │   ├── consumer.py     # 消息消费者
│   │   └── handlers.py     # 处理器链管理
│   ├── handlers/           # 具体处理器
│   │   ├── base.py         # 处理器基类
│   │   ├── preprocessor.py # 预处理器（过滤/去重）
│   │   ├── ai_handler.py   # AI 回复处理器
│   │   ├── keyword_handler.py  # 关键词转人工处理器
│   │   └── voucher_handler.py  # 核销处理器
│   └── models/
│       └── queue_models.py # 队列数据模型
│
├── bridge/                 # 渠道与消息处理桥接层
│   ├── context.py          # 消息上下文
│   └── reply.py            # 回复发送
│
├── core/                   # 核心服务
│   ├── di_container.py     # DI 容器（依赖注入）
│   ├── service_providers.py # 服务注册
│   ├── base_service.py     # 服务基类
│   ├── connection_status.py # 连接状态管理
│   └── cache.py            # 缓存管理
│
├── database/               # 数据层
│   ├── db_manager.py       # 数据库管理器
│   ├── models.py           # SQLAlchemy 模型
│   ├── knowledge_service.py # 知识库服务
│   ├── product_sync.py     # 商品同步服务
│   └── connection_pool.py  # 连接池
│
├── ui/                     # PyQt6 用户界面
│   ├── main_ui.py          # 主窗口
│   ├── auto_reply/         # 自动回复页面（含线程管理）
│   ├── goods_ui.py         # 商品管理页
│   ├── Knowledge_ui.py     # 知识库管理页
│   ├── keyword_ui.py       # 关键词管理页
│   ├── setting_ui.py       # 设置页
│   ├── user_ui.py          # 账号管理页
│   ├── voucher_ui.py       # 核销管理页
│   ├── log_ui.py           # 日志查看页
│   └── message_ui.py       # 消息查看页
│
├── utils/                  # 工具模块
│   ├── logger_loguru.py    # Loguru 日志封装
│   ├── logging_context.py  # 日志上下文
│   ├── async_helper.py     # 异步辅助
│   ├── path_utils.py       # 路径工具
│   ├── runtime_path.py     # 运行时路径（支持 PyInstaller）
│   ├── encoding_helper.py  # 编码工具
│   ├── file_validator.py   # 文件验证
│   └── resource_manager.py # 资源管理
│
└── scripts/                # 构建脚本
    ├── build_win_exe.py    # Windows exe 打包
    └── agent_customer.spec # PyInstaller 配置
```

## Core Components & Relationships

```
app.py
  └─ config (最先初始化)
  └─ DI Container (configure_standard_services)
       ├─ db_manager
       ├─ queue_manager
       ├─ message_consumer_manager
       ├─ status_manager (ConnectionStatusManager)
       └─ cache_manager

消息流:
PDD WebSocket → pdd_message_handler → bridge/context
  → Message Queue → Consumer → Handler Chain
       ├─ preprocessor (过滤/去重)
       ├─ keyword_handler (关键词转人工)
       ├─ voucher_handler (核销处理)
       └─ ai_handler → Agent/bot.py → CustomerAgent
                            └─ LLM + Tools → reply
```

## Architectural Patterns

- **DI 容器**：所有核心服务通过 `core/di_container.py` 统一注册和获取，避免全局变量
- **处理器链（Chain of Responsibility）**：消息依次经过多个 Handler，每个 Handler 可决定是否继续传递
- **懒加载（Lazy Import）**：业务模块间通过延迟导入避免循环依赖
- **每账号独立线程**：每个 AutoReplyThread 持有独立 PDDChannel 实例，共享 ConnectionStatusManager
- **原子配置保存**：config.py 使用临时文件 + 原子重命名防止配置损坏
