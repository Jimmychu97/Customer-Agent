# Project Structure

## Directory Layout

```
Customer-Agent/
├── app.py                  # 应用入口，全局单例初始化顺序管理
├── config.py               # 配置管理（Pydantic 验证 + 线程安全 + 原子保存）
├── config.json             # 运行时配置文件（首次运行自动生成）
├── pyproject.toml          # 项目依赖（uv 管理）
│
├── Agent/                  # AI 代理模块（自研框架）
│   ├── bot.py              # Bot 基类接口
│   └── CustomerAgent/
│       ├── custom/         # Agent 核心实现
│       │   ├── customer_agent.py   # 主 Agent 类（工具调用循环）
│       │   ├── agent_config.py     # 配置管理
│       │   ├── llm_client.py       # LLM API 封装（OpenAI 兼容）
│       │   ├── message_builder.py  # 消息/Prompt 构建
│       │   ├── session_manager.py  # 会话历史 + 上下文压缩
│       │   ├── tool_decorator.py   # @agent_tool 装饰器注册机制
│       │   └── tool_executor.py    # 并行工具执行器
│       └── tools/          # Agent 工具集（每个文件一个工具）
│           ├── search_customer_service_knowledge.py
│           ├── get_product_knowledge.py
│           ├── get_product_list.py
│           ├── send_goods_link.py
│           ├── move_conversation.py
│           ├── verify_voucher.py
│           ├── use_voucher.py
│           ├── coupon_query.py
│           └── get_voucher_code.py
│
├── Channel/                # 渠道集成层
│   ├── channel.py          # Channel 基类接口
│   └── pinduoduo/
│       ├── pdd_channel.py  # 拼多多渠道主类
│       ├── pdd_login.py    # Playwright 自动登录
│       ├── pdd_message.py  # 消息模型
│       ├── cookie_cache.py # Cookie 缓存管理
│       ├── cookie_utils.py # Cookie 工具函数
│       ├── core/           # 连接核心
│       │   ├── pdd_connection.py   # WebSocket 连接管理
│       │   ├── pdd_lifecycle.py    # 连接生命周期
│       │   ├── pdd_message_handler.py  # 消息处理分发
│       │   ├── pdd_config.py       # 渠道配置
│       │   ├── pdd_status.py       # 连接状态
│       │   └── pdd_utils.py        # 工具函数
│       └── utils/API/      # 拼多多 API 封装
│
├── Message/                # 消息处理层
│   ├── message.py          # 消息模型
│   ├── core/
│   │   ├── queue.py        # 异步消息队列（queue_manager 单例）
│   │   ├── consumer.py     # 消息消费者管理
│   │   └── handlers.py     # 处理器链管理
│   └── handlers/           # 具体处理器
│       ├── base.py         # 处理器基类
│       ├── preprocessor.py # 预处理器（过滤/去重）
│       ├── ai_handler.py   # AI 回复处理器
│       ├── keyword_handler.py  # 关键词转人工处理器
│       └── voucher_handler.py  # 核销处理器
│
├── bridge/                 # 渠道与消息处理桥接层
│   ├── context.py          # Context 上下文对象
│   └── reply.py            # Reply 回复对象
│
├── core/                   # 核心基础设施
│   ├── di_container.py     # DI 容器（Singleton/Transient/Scoped）
│   ├── service_providers.py # _DIProxy 懒代理（向后兼容层）
│   ├── connection_status.py # 连接状态管理器（跨实例共享）
│   ├── base_service.py     # 服务基类
│   └── cache.py            # 缓存管理器
│
├── database/               # 数据持久层
│   ├── models.py           # SQLAlchemy ORM 模型
│   ├── db_manager.py       # 数据库管理器（通过 DI 代理）
│   ├── knowledge_service.py # 知识库服务（jieba 分词检索）
│   ├── product_sync.py     # 商品知识自动同步（多模态 LLM 提取）
│   └── connection_pool.py  # 连接池管理
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
│   ├── logger_loguru.py    # Loguru 日志封装（get_logger 工厂）
│   ├── logger_config.py    # 日志配置
│   ├── async_helper.py     # 异步辅助工具
│   ├── path_utils.py       # 路径工具
│   ├── runtime_path.py     # 运行时路径（支持 PyInstaller）
│   ├── file_validator.py   # 文件验证器
│   ├── encoding_helper.py  # 编码辅助
│   ├── resource_manager.py # 资源管理器
│   └── volcengine_models.py # 火山引擎模型配置
│
└── scripts/                # 构建与测试脚本
    ├── build_exe.py        # PyInstaller 打包脚本
    ├── build_win_exe.py    # Windows 打包入口
    ├── agent_customer.spec # PyInstaller spec 文件
    └── install_playwright.py # Playwright 安装脚本
```

## Core Architectural Patterns

### 1. 初始化顺序（app.py 强制约束）
```
config → DI容器(configure_standard_services) → db_manager → logger → UI
```

### 2. 依赖注入 + 懒代理
- `core/di_container.py` 提供 DIContainer（Singleton/Transient/Scoped）
- `core/service_providers.py` 提供 `_DIProxy`，使旧代码 `from database import db_manager` 无需修改
- 双重检查锁定保证线程安全的单例创建

### 3. 消息处理流水线
```
WebSocket → PDDChannel → bridge.Context → MessageQueue → 处理器链
                                                          ├── preprocessor（过滤/去重）
                                                          ├── keyword_handler（关键词转人工）
                                                          ├── voucher_handler（核销处理）
                                                          └── ai_handler（AI回复）
```

### 4. Agent 工具调用循环
```
用户消息 → 加载历史 → 构建messages → LLM调用 → 有tool_calls?
                                                    ├── 是 → 并行执行工具 → 追加结果 → 循环
                                                    └── 否 → 返回内容
```

### 5. 模块级单例 vs DI 容器
- DI 容器管理：`ConnectionStatusManager`、`DatabaseManager`、`KnowledgeService`
- 模块级单例（直接 import）：`queue_manager`、`message_consumer_manager`、`cache_manager`

## Key Relationships
- `PDDChannel` 每个 AutoReplyThread 独立实例，共享 `ConnectionStatusManager`
- `CustomerAgent` 通过 `@agent_tool` 装饰器自动注册工具，导入工具模块即触发注册
- `bridge.Context` 是渠道层与消息处理层的数据契约
