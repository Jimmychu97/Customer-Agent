# Technology Stack

## Runtime Requirements
- Python >= 3.11 (`.python-version` 文件管理)
- Windows 操作系统
- uv 包管理器

## Core Dependencies

| 类别 | 库 | 版本 | 用途 |
|------|----|------|------|
| UI | PyQt6 | >=6.9.0 | 桌面 GUI 框架 |
| UI | pyqt6-fluent-widgets | >=1.9.0 | Fluent Design 组件库 |
| AI | openai | >=1.109.1 | OpenAI 兼容 LLM 客户端 |
| 数据库 | SQLAlchemy | >=2.0.43 | ORM + SQLite |
| 配置 | pydantic | >=2.5.0 | 配置验证和类型安全 |
| 异步通信 | websockets | >=10.4 | WebSocket 客户端 |
| 浏览器自动化 | playwright | >=1.52.0 | 拼多多登录/Cookie 刷新 |
| 日志 | loguru | >=0.7.0 | 结构化日志 |
| 中文分词 | jieba | >=0.42.1 | 知识库检索分词 |
| Token 统计 | tiktoken | >=0.12.0 | LLM Token 计算 |
| HTTP | requests | >=2.28.0 | 同步 HTTP 请求 |
| HTTP | aiohttp | >=3.13.2 | 异步 HTTP 请求 |
| 数据处理 | pandas | >=2.1.0,<2.3.0 | 商品数据处理 |
| 文档解析 | pypdf | latest | PDF 解析 |
| 文档解析 | python-docx | >=1.2.0 | Word 文档解析 |
| 文档解析 | openpyxl / xlrd | latest | Excel 解析 |
| 图像处理 | pillow | >=12.0.0 | 图片处理 |
| 火山引擎 | volcengine | >=1.0.209 | 豆包模型支持 |

## Build System
- 包管理：`uv`（`pyproject.toml` + `uv.lock`）
- 构建后端：`hatchling`
- 打包工具：PyInstaller（`scripts/build_win_exe.py`）

## Development Commands

```bash
# 安装依赖
uv sync

# 安装 Playwright 浏览器（必须）
uv run playwright install chromium
# 或
.venv\Scripts\python.exe -m playwright install chromium

# 启动应用
uv run python app.py
# 或
python app.py

# 打包为 Windows exe
python scripts/build_win_exe.py --clean
```

## Configuration System
- 配置文件：`config.json`（首次运行自动生成）
- 配置类：`config.py` 中的 `Config` 类
- 验证：Pydantic `ConfigModel`
- 访问：`config.get('llm.api_key')` 支持点号嵌套访问
- 线程安全：`threading.RLock`
- 原子保存：临时文件 + `Path.replace()` 原子重命名

## Database
- 引擎：SQLite（通过 SQLAlchemy ORM）
- 路径：`config.json` 中 `db_path` 配置，或默认 `temp/channel_shop.db`
- 连接池：`database/connection_pool.py`

## Logging
- 框架：Loguru
- 配置：`utils/logger_loguru.py`
- 日志文件：`logs/app.log`
- 上下文：`utils/logging_context.py` 提供结构化上下文

## Supported LLM Providers
任何 OpenAI 兼容接口：

| 服务 | api_base | model_name 示例 |
|------|----------|-----------------|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 豆包（火山引擎） | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-seed-1-6-flash-250828` |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` |

## External APIs
- 拼多多 WebSocket API（消息接收）
- 拼多多开放平台 API（商品同步）
- GSX 验机 API（gkdt.com，安卓设备）
- 真心验机 API（iunlock8.com，苹果设备）
