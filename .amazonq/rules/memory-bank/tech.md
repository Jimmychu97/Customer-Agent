# Technology Stack

## Language & Runtime
- Python >= 3.11（使用 `.python-version` 文件锁定版本）
- Windows 操作系统（PyQt6 桌面应用）

## Package Management
- uv（推荐，替代 pip/venv）
- `pyproject.toml` 定义依赖，`uv.lock` 锁定版本

## Core Dependencies

| 类别 | 库 | 版本 | 用途 |
|------|----|------|------|
| UI | PyQt6 | >=6.9.0 | 桌面 GUI 框架 |
| UI | pyqt6-fluent-widgets | >=1.9.0 | Fluent Design 组件库 |
| AI | openai | >=1.109.1 | OpenAI 兼容 API 客户端 |
| 数据库 | SQLAlchemy | >=2.0.43 | ORM + SQLite |
| 配置 | pydantic | >=2.5.0 | 配置模型验证 |
| 异步 | websockets | >=10.4 | WebSocket 长连接 |
| 异步 | aiohttp | >=3.13.2 | 异步 HTTP 请求 |
| 异步 | aiofiles | >=25.1.0 | 异步文件操作 |
| 浏览器自动化 | playwright | >=1.52.0 | 拼多多登录/Cookie 刷新 |
| 日志 | loguru | >=0.7.0 | 结构化日志 |
| 分词 | jieba | >=0.42.1 | 知识库中文检索 |
| Token 统计 | tiktoken | >=0.12.0 | LLM Token 计数 |
| 数据处理 | pandas | >=2.1.0,<2.3.0 | 商品数据处理 |
| 数据处理 | numpy | >=1.24.0,<2.0.0 | 数值计算 |
| 图像处理 | pillow | >=12.0.0 | 图片处理 |
| 文档解析 | pypdf | latest | PDF 解析 |
| 文档解析 | python-docx | >=1.2.0 | Word 文档解析 |
| 文档解析 | openpyxl | >=3.1.5 | Excel 解析 |
| 文档解析 | xlrd | >=2.0.2 | 旧版 Excel 解析 |
| 网络 | requests | >=2.28.0 | 同步 HTTP 请求 |
| 网络 | python-socks[asyncio] | >=2.8.2 | SOCKS 代理支持 |
| 打包 | pyinstaller | >=6.0.0 (optional) | Windows exe 打包 |

## LLM 配置

### 文本对话 LLM（`config.json` → `llm`）
支持任何 OpenAI 兼容接口：
- DeepSeek: `https://api.deepseek.com/v1`，模型 `deepseek-chat`
- 豆包: `https://ark.cn-beijing.volces.com/api/v3`，模型 `doubao-seed-1-6-flash-250828`
- Kimi: `https://api.moonshot.cn/v1`，模型 `moonshot-v1-8k`

### 视觉 LLM（`config.json` → `vision_llm`）
用于商品图片知识提取，当前配置为阿里云百炼 `qwen3.5-ocr`

## Development Commands

```bash
# 安装依赖
uv sync

# 启动应用
uv run python app.py
# 或
python app.py

# 安装 Playwright 浏览器（必须执行一次）
uv run playwright install chromium
# 或（如果上面失败）
.venv\Scripts\python.exe -m playwright install chromium

# 打包 Windows exe
python scripts/build_win_exe.py --clean

# 测试脚本
python scripts/test_gsx.py
python scripts/test_sku.py
```

## Database
- SQLite（文件路径由 `config.json` → `db_path` 配置，默认 `./temp/channel_shop.db`）
- SQLAlchemy 2.x ORM，模型定义在 `database/models.py`
- 连接池管理在 `database/connection_pool.py`

## Async Architecture
- Python `asyncio` 事件循环
- PyQt6 主线程运行 Qt 事件循环
- 每个自动回复账号在独立线程中运行 asyncio 事件循环（`AutoReplyThread`）
- `utils/async_helper.py` 提供线程间异步调用桥接

## Proxy Support
- HTTP 代理：`http://admin:Asd123123@43.139.22.120:3128`（在 README 中配置）
- `python-socks[asyncio]` 支持 SOCKS 代理

## Build & Packaging
- PyInstaller 打包配置：`scripts/agent_customer.spec`
- 打包脚本：`scripts/build_win_exe.py`
- Playwright 浏览器打包到 `.browsers/` 目录
- 支持 PyInstaller `sys.frozen` 检测，路径自动适配
