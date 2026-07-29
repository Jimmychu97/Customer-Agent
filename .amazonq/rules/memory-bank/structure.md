# Project Structure

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
│       ├── custom/         # 核心框架组件
│       │   ├── customer_agent.py   # Agent 主逻辑（多轮工具调用）
│       │   ├── llm_client.py       # LLM API 客户端（OpenAI 兼容）
│       │   ├── session_manager.py  # 会话上下文管理
│       │   ├── tool_executor.py    # 工具执行器
│       │   ├── tool_decorator.py   # 工具注册装饰器
│       │   ├── message_builder.py  # 消息构建
│       │   └── agent_config.py     # Agent 配置
│       └── tools/          # Agent 工具集
│           ├── search_customer_service_knowledge.py
│           ├── get_product_knowledge.py
│           ├── get_product_list.py
│           ├── send_goods_link.py
│           ├── move_conversation.py
│           ├── coupon_query.py
│           ├── verify_voucher.py
│           ├── use_voucher.py
│           └── get_voucher_code.py
│
├── Channel/                # 渠道集成层
│   ├── channel.py          # 渠道基类
│   └── pinduoduo/
│       ├── pdd_channel.py  # 拼多多渠道主类
│       ├── pdd_login.py    # Playwright 自动登录
│       ├── pdd_message.py  # 消息模型
│       ├── cookie_cache.py # Cookie 缓存
│       ├── cookie_utils.py # Cookie 工具
│       ├── core/           # 连接核心
│       │   ├── pdd_connection.py   # WebSocket 连接管理
│       │   ├── pdd_lifecycle.py    # 连接生命周期
│       │   ├── pdd_message_handler.py  # 消息解析
│       │   ├── pdd_status.py       # 连接状态
│       │   ├── pdd_config.py       # 渠道配置
│       │   └── pdd_utils.py        # 工具函数
│       └── utils/API/      # 拼多多 API 封装
│
├── Message/                # 消息处理层（队列 + 处理器链）
│   ├── message.py          # 消息模型
│   ├── core/
│   │   ├── queue.py        # 异步消息队列（queue_manager 单例）
│   │   ├── consumer.py     # 消息消费者（message_consumer_manager 单例）
│   │   └── handlers.py     # 处理器基类（MessageHandler ABC）
│   ├── handlers/           # 具体处理器实现
│   │   ├── base.py         # 基础处理器
│   │   ├── preprocessor.py # 预处理器（消息过滤/标准化）
│   │   ├── ai_handler.py   # AI 回复处理器
│   │   ├── keyword_handler.py  # 关键词转人工处理器
│   │   └── voucher_handler.py  # 核销处理器（验机报告）
│   └── models/
│       └── queue_models.py # 队列消息模型（MessageWrapper）
│
├── bridge/                 # 渠道与消息处理桥接层
│   ├── context.py          # Context 统一消息上下文
│   └── reply.py            # Reply 回复对象
│
├── core/                   # 核心基础设施
│   ├── di_container.py     # DI 容器（Singleton/Transient/Scoped）
│   ├── service_providers.py # _DIProxy 懒代理（向后兼容层）
│   ├── connection_status.py # ConnectionStatusManager 单例
│   ├── base_service.py     # 服务基类
│   └── cache.py            # 缓存管理
│
├── database/               # 数据持久层
│   ├── db_manager.py       # DatabaseManager（SQLAlchemy）
│   ├── models.py           # ORM 模型定义
│   ├── knowledge_service.py # 知识库服务（jieba 分词检索）
│   ├── product_sync.py     # 商品知识同步（多模态 LLM 提取）
│   └── connection_pool.py  # 连接池管理
│
├── ui/                     # PyQt6 用户界面
│   ├── main_ui.py          # 主窗口
│   ├── auto_reply/         # 自动回复模块（含线程管理）
│   │   ├── ui.py           # 自动回复页面
│   │   ├── manager.py      # 自动回复管理器
│   │   ├── threads.py      # AutoReplyThread（每账号独立线程）
│   │   └── card.py         # 账号卡片组件
│   ├── goods_ui.py         # 商品管理页
│   ├── knowledge_ui.py     # 知识库管理页
│   ├── keyword_ui.py       # 关键词管理页
│   ├── setting_ui.py       # 设置页
│   ├── user_ui.py          # 账号管理页
│   ├── voucher_ui.py       # 核销管理页
│   ├── log_ui.py           # 日志查看页
│   └── message_ui.py       # 消息查看页
│
├── utils/                  # 工具模块
│   ├── logger_loguru.py    # Loguru 日志封装（get_logger 工厂）
│   ├── logging_context.py  # 日志上下文管理
│   ├── async_helper.py     # 异步辅助工具
│   ├── path_utils.py       # 路径工具
│   ├── runtime_path.py     # 运行时路径（支持 PyInstaller）
│   ├── encoding_helper.py  # 编码工具
│   ├── file_validator.py   # 文件验证
│   └── resource_manager.py # 资源管理
│
└── scripts/                # 构建与测试脚本
    ├── build_win_exe.py    # Windows exe 打包脚本
    └── agent_customer.spec # PyInstaller 配置
```

## Core Architectural Patterns

### 1. 初始化顺序（app.py 强制保证）
```
config → DI容器(configure_standard_services) → db_manager → logger → UI
```

### 2. 依赖注入 + 懒代理
- `core/di_container.py` 提供 DIContainer，支持 Singleton/Transient/Scoped 三种生命周期
- `core/service_providers.py` 的 `_DIProxy` 实现透明代理，旧代码 `from database import db_manager` 无需修改

### 3. 消息处理流水线
```
WebSocket → PDDChannel → bridge.Context → MessageQueue → MessageConsumer → Handler链
                                                                    ↓
                                          [PreprocessorHandler → KeywordHandler → VoucherHandler → AIHandler]
```

### 4. 每账号独立线程
- `ui/auto_reply/threads.py` 中每个 `AutoReplyThread` 持有独立的 `PDDChannel` 实例
- 所有线程共享同一个 `ConnectionStatusManager` 单例（通过 DI 容器）

### 5. 模块级单例 vs DI 容器单例
- `queue_manager`、`message_consumer_manager` 通过模块级变量直接使用
- `ConnectionStatusManager`、`DatabaseManager`、`KnowledgeService` 通过 DI 容器管理
