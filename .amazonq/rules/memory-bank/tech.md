# Technology Stack

## Language & Runtime
- Python >= 3.11（使用 `.python-version` 锁定版本）
- Windows 为主要运行平台（可打包为 exe）

## Build & Dependency Management
- **uv** 管理依赖（`uv sync` 安装）
- **hatchling** 作为 build backend
- **PyInstaller** 用于打包 Windows exe（optional dependency）

## Core Dependencies

| 类别 | 包 | 说明 |
|------|-----|------|
| UI | pyqt6 >= 6.9.0 | 桌面界面框架 |
| UI | pyqt6-fluent-widgets >= 1.9.0 | Fluent Design 组件库 |
| AI | openai >= 1.109.1 | OpenAI 兼容 API 客户端 |
| AI | tiktoken >= 0.12.0 | Token 统计 |
| 数据库 | sqlalchemy >= 2.0.43 | ORM |
| 配置 | pydantic >= 2.5.0 | 配置模型验证 |
| 异步 | websockets >= 10.4 | WebSocket 客户端 |
| 异步 | aiohttp >= 3.13.2 | 异步 HTTP 客户端 |
| 异步 | aiofiles >= 25.1.0 | 异步文件操作 |
| 日志 | loguru >= 0.7.0 | 结构化日志 |
| 分词 | jieba >= 0.42.1 | 中文分词（知识库检索） |
| 文档解析 | pypdf | PDF 解析 |
| 文档解析 | python-docx >= 1.2.0 | Word 文档解析 |
| 文档解析 | openpyxl >= 3.1.5 | Excel 解析 |
| 文档解析 | xlrd >= 2.0.2 | 旧版 Excel 解析 |
| 数据 | pandas >= 2.1.0,<2.3.0 | 数据处理 |
| 数据 | numpy >= 1.24.0,<2.0.0 | 数值计算 |
| 浏览器自动化 | playwright >= 1.52.0 | 拼多多自动登录 |
| 图像 | pillow >= 12.0.0 | 图像处理 |
| HTTP | requests >= 2.28.0 | 同步 HTTP 请求 |
| 火山引擎 | volcengine >= 1.0.209 | 火山引擎 SDK（语音/模型） |

## Development Commands

```bash
# 安装依赖
uv sync

# 启动应用
python app.py

# 打包 Windows exe（Windows 上运行）
python scripts/build_win_exe.py --clean
```

## Configuration System
- 运行时配置：`config.json`（首次运行自动生成）
- 配置模型：Pydantic `ConfigModel`，支持 `config.get("llm.api_key")` 点号嵌套访问
- 线程安全：`threading.RLock()` 保护所有读写操作
- 原子保存：临时文件 + `replace()` 原子重命名，防止配置文件损坏

## Database
- SQLite（文件路径：`./temp/channel_shop.db`，可通过 `config.json` 的 `db_path` 配置）
- SQLAlchemy 2.0 ORM（使用 `Session` 上下文管理器）
- 知识库存储在同一 SQLite 数据库中

## AI / LLM Integration
- 通过 OpenAI Python SDK 调用任意兼容 API（DeepSeek、Kimi、Gemini、Claude 等）
- 自研 Agent 框架，工具定义通过 `@tool` 装饰器注册
- 支持多模态 LLM（商品图片知识提取）
- 嵌入模型通过 `config.json` 的 `embedder` 配置

## Logging
- Loguru 框架，通过 `get_logger("ModuleName")` 获取 logger
- 日志文件：`logs/app.log`
- 支持 `logging_context.py` 上下文绑定

## Async Architecture
- asyncio 事件循环运行在独立线程（与 Qt 主线程分离）
- `utils/async_helper.py` 提供 Qt ↔ asyncio 桥接
- WebSocket 连接、消息队列消费均为 async 协程

## Packaging (Windows exe)
- PyInstaller + `.spec` 文件（`scripts/agent_customer.spec`）
- Playwright 浏览器路径通过 `PLAYWRIGHT_BROWSERS_PATH` 环境变量配置
- 打包产物：`dist/AgentCustomer/`
