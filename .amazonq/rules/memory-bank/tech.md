# Technology Stack

## Runtime Requirements
- Python >= 3.11 (`.python-version` 文件锁定版本)
- Windows 操作系统（桌面应用，PyQt6 GUI）
- uv 包管理器

## Core Dependencies

### UI 框架
| 包 | 版本 | 用途 |
|----|------|------|
| `pyqt6` | >=6.9.0 | 桌面 GUI 框架 |
| `pyqt6-fluent-widgets` | >=1.9.0 | Fluent Design 风格组件 |

### AI / LLM
| 包 | 版本 | 用途 |
|----|------|------|
| `openai` | >=1.109.1 | OpenAI 兼容 API 客户端 |
| `tiktoken` | >=0.12.0 | Token 计数（上下文压缩判断） |

### 数据库
| 包 | 版本 | 用途 |
|----|------|------|
| `sqlalchemy` | >=2.0.43 | ORM + SQLite |

### 异步通信
| 包 | 版本 | 用途 |
|----|------|------|
| `websockets` | >=10.4 | WebSocket 客户端（拼多多消息接收） |
| `aiohttp` | >=3.13.2 | 异步 HTTP 请求 |
| `aiofiles` | >=25.1.0 | 异步文件操作 |
| `python-socks[asyncio]` | >=2.8.2 | SOCKS/HTTP 代理支持 |

### 浏览器自动化
| 包 | 版本 | 用途 |
|----|------|------|
| `playwright` | >=1.52.0 | 拼多多自动登录 + Cookie 刷新 |

### 数据处理
| 包 | 版本 | 用途 |
|----|------|------|
| `pydantic` | >=2.5.0 | 配置验证 + 数据模型 |
| `pandas` | >=2.1.0,<2.3.0 | 数据处理 |
| `numpy` | >=1.24.0,<2.0.0 | 数值计算 |
| `jieba` | >=0.42.1 | 中文分词（知识库检索） |

### 文档解析
| 包 | 版本 | 用途 |
|----|------|------|
| `pypdf` | latest | PDF 解析 |
| `python-docx` | >=1.2.0 | Word 文档解析 |
| `openpyxl` | >=3.1.5 | Excel 读写 |
| `xlrd` | >=2.0.2 | 旧版 Excel 读取 |

### 日志
| 包 | 版本 | 用途 |
|----|------|------|
| `loguru` | >=0.7.0 | 结构化日志（替代标准 logging） |

### 其他
| 包 | 版本 | 用途 |
|----|------|------|
| `requests` | >=2.28.0 | 同步 HTTP 请求 |
| `pillow` | >=12.0.0 | 图像处理 |
| `volcengine` | >=1.0.209 | 火山引擎 SDK（豆包模型） |

### 构建（可选依赖）
| 包 | 版本 | 用途 |
|----|------|------|
| `pyinstaller` | >=6.0.0 | 打包为 Windows exe |

## Development Commands

### 环境管理
```bash
# 安装依赖
uv sync

# 安装含构建工具的依赖
uv sync --extra build

# 安装 Playwright 浏览器
uv run playwright install chromium
# 或（venv 内）
.venv\Scripts\python.exe -m playwright install chromium
```

### 运行应用
```bash
# 开发模式
python app.py
# 或
uv run python app.py
```

### 打包 Windows exe
```bash
python scripts/build_win_exe.py --clean
# 产物位于 dist/AgentCustomer/
```

### 测试脚本
```bash
# GSX 验机接口测试
python scripts/test_gsx.py

# SKU 同步测试
python scripts/test_sku.py
```

## Configuration

### 配置文件：config.json（首次运行自动生成）
```json
{
  "llm": {
    "api_base": "https://api.deepseek.com/v1",
    "api_key": "<your-api-key>",
    "model_name": "deepseek-chat"
  },
  "gsx": {
    "appid": "<gsx-appid>",
    "secret": "<gsx-secret>"
  },
  "zhenxin_api": {
    "apikey": "<zhenxin-apikey>"
  },
  "business_hours": { "enabled": true },
  "prompt": { "instructions": [] }
}
```

### 支持的 LLM 服务
| 服务 | api_base | model_name |
|------|----------|------------|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 豆包（火山引擎） | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-seed-1-6-flash-250828` |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |

## Database
- SQLite（文件路径：`./temp/channel_shop.db`，可通过 `config.json` 的 `db_path` 配置）
- SQLAlchemy 2.x ORM
- 连接池管理（`database/connection_pool.py`）

## Proxy Support
- 代理地址格式：`http://user:password@host:port`
- 通过 `python-socks[asyncio]` 支持 SOCKS/HTTP 代理
- WebSocket 连接支持代理透传

## Build Output
- 打包产物：`dist/AgentCustomer/`
- Playwright 浏览器：`.browsers/`（打包后与 exe 同级目录）
- 日志文件：`logs/app.log`
