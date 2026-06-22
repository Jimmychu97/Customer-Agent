# Agent-Customer

电商AI客服桌面应用程序，基于 PyQt6 构建，支持拼多多平台 WebSocket 实时消息接收，集成 AI 大模型实现智能自动回复。

## 功能特性

- **多渠道支持**：支持拼多多平台 WebSocket 实时消息接收
- **AI 智能回复**：基于自研 Agent 框架（不依赖 Agno），多轮工具调用 + 会话上下文管理
- **AI 主动推荐**：客服代理可主动获取商品列表、发送商品卡片给用户
- **双知识库体系**：客服知识库（售前/售后/商品解答）+ 产品知识库（LLM提取），AI优先检索知识库回答
- **商品知识自动同步**：从拼多多 API 拉取商品列表，调用多模态 LLM 提取产品知识入库
- **GSX 验机查询**：核销完成后自动调用 GSX 接口查询验机报告并生成简析发送给用户
- **关键词转人工**：自动识别用户意图，支持关键词触发转人工服务
- **消息队列处理**：异步消息队列 + 处理器链，支持高并发场景
- **自动重连机制**：WebSocket 连接支持断线自动重连和心跳检测

### AI Agent 可用工具

| 工具名称 | 功能描述 |
|----------|----------|
| `search_customer_service_knowledge` | 搜索客服知识库（售前咨询、商品解答、售后政策等） |
| `get_product_knowledge` | 查询指定商品的详细知识（成分、规格、用法、价格等） |
| `get_shop_products` | 获取店铺商品列表 |
| `send_goods_link` | 向用户发送商品卡片链接 |
| `transfer_conversation` | 转接会话给人工客服 |

---

## 环境要求

- Python >= 3.11
- Windows 操作系统
- uv 包管理器（见下方安装说明）

---

## 新电脑部署步骤

### 第一步：安装 Python

前往 [https://www.python.org/downloads/](https://www.python.org/downloads/) 下载 **Python 3.11 或以上版本**，安装时勾选 **Add Python to PATH**。

验证安装：
```bash
python --version
```

### 第二步：安装 uv

```bash
pip install uv
```

验证安装：
```bash
uv --version
```

### 第三步：克隆或复制项目

将项目文件夹复制到新电脑，进入项目根目录：
```bash
cd Customer-Agent
```

### 第四步：安装项目依赖

```bash
uv sync
```

### 第五步：安装 Playwright 浏览器

> **必须执行**，用于拼多多账号自动登录和 Cookie 刷新，缺少此步骤将导致 WebSocket 无法收到消息。

```bash
uv run playwright install chromium
```

### 第六步：启动应用

```bash
python app.py
```

首次运行会自动在项目根目录生成 `config.json` 配置文件。

---

## 配置说明

启动后进入**设置页**完成以下配置，或直接编辑项目根目录的 `config.json`：

| 配置项 | 说明 | 必填 |
| --- | --- | --- |
| `llm.api_base` | LLM API 地址（如 `https://api.deepseek.com/v1`） | ✅ |
| `llm.api_key` | LLM API 密钥 | ✅ |
| `llm.model_name` | 模型名称（如 `deepseek-chat`） | ✅ |
| `gsx.appid` | GSX 验机平台 AppID（gkdt.com 个人中心获取） | ✅ |
| `gsx.secret` | GSX 验机平台 AppSecret | ✅ |
| `business_hours` | AI客服工作时间（默认 08:00-23:00） | 可选 |
| `prompt.instructions` | AI 客服行为指令 | 可选 |

支持任何 OpenAI 兼容接口，例如：

| 模型服务 | api_base | model_name |
|---|---|---|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 豆包（火山引擎） | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-seed-1-6-flash-250828` |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |

---

## 首次使用流程

1. **登录拼多多账号**：在「账号管理」页面添加账号，输入手机号和密码登录，获取 Cookie
2. **同步商品 SKU**：在「商品管理」页面点击「同步所有商品SKU」，同步完成后在 ServerId 列填写 GSX 对应的接口编码
3. **录入客服知识**：在「知识库管理」页面添加客服知识（售前咨询话术、商品说明、售后政策等）
4. **配置关键词**：在「关键词管理」页面配置触发转人工的关键词
5. **启动自动回复**：在「自动回复」页面选择账号，点击启动

---

## 常见问题

**Q：启动后收不到消息？**
- 检查 Cookie 是否有效，在账号管理页重新登录
- 确认已执行 `uv run playwright install chromium`
- 查看 `logs/app.log` 日志，搜索 `会话已过期` 关键字

**Q：AI 回复不准确？**
- 在知识库管理页补充相关客服知识内容
- 在设置页调整 `prompt.instructions` 行为指令

**Q：GSX 验机报告查询失败？**
- 确认 `config.json` 中 `gsx.appid` 和 `gsx.secret` 已正确配置
- 确认商品管理页的 ServerId 列已填写对应的 GSX 接口编码

---

## 构建 Windows 可执行文件

如需打包为独立 exe（无需安装 Python 即可运行）：

```bash
python scripts/build_win_exe.py --clean
```

打包产物位于 `dist/AgentCustomer/` 目录，直接复制到目标电脑运行即可。

---

## 项目结构

```
Customer-Agent/
├── Agent/                  # AI 代理模块（自研 Agent 框架）
│   └── CustomerAgent/
│       ├── custom/         # LLM 客户端、会话管理、工具执行器
│       └── tools/          # Agent 工具集
├── Channel/                # 渠道集成
│   └── pinduoduo/
│       ├── core/           # 连接、生命周期、消息处理
│       └── utils/API/      # 拼多多 API 封装
├── Message/                # 消息处理（队列 + 处理器链）
│   ├── core/               # consumer / handlers / queue
│   └── handlers/           # 预处理器、AI、关键词、核销处理器
├── bridge/                 # 渠道与消息处理桥接层
├── core/                   # 核心服务（DI 容器、连接状态）
├── database/               # 数据库（SQLAlchemy + 知识服务 + 商品同步）
├── ui/                     # PyQt6 用户界面
├── utils/                  # 工具模块（日志、路径等）
├── scripts/                # 构建脚本
├── config.json             # 运行时配置文件（首次运行自动生成）
└── app.py                  # 应用入口
```

## 技术栈

| 类别 | 技术 |
|------|------|
| UI 框架 | PyQt6 + pyqt6-fluent-widgets |
| AI 框架 | 自研 Agent 框架 + OpenAI 兼容 API |
| 数据库 | SQLAlchemy + SQLite |
| 中文分词 | jieba（知识库检索） |
| Token 统计 | tiktoken |
| 异步通信 | asyncio + websockets |
| 浏览器自动化 | Playwright（拼多多登录/Cookie刷新） |
| 文档解析 | pypdf + python-docx + openpyxl + xlrd |
| 日志 | Loguru |
| 配置 | Pydantic |

## License

MIT
