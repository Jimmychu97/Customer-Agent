# Tech Stack: Agent-Customer

## Language & Runtime
- Python >= 3.11（使用 `.python-version` 文件锁定版本）
- Windows 操作系统（PyQt6 桌面应用）

## Package Manager
- **uv**（推荐，替代 pip/poetry）
- 依赖定义：`pyproject.toml`
- 锁文件：`uv.lock`

## Core Dependencies

### UI 框架
| 包 | 版本 | 用途 |
|----|------|------|
| `pyqt6` | >=6.9.0 | 桌面 GUI 框架 |
| `pyqt6-fluent-widgets` | >=1.9.0 | Fluent Design 风格组件库 |

### AI / LLM
| 包 | 版本 | 用途 |
|----|------|------|
| `openai` | >=1.109.1 | OpenAI 兼容 API 客户端 |
| `tiktoken` | >=0.12.0 | Token 统计 |

### 数据库
| 包 | 版本 | 用途 |
|----|------|------|
| `sqlalchemy` | >=2.0.43 | ORM + SQLite |

### 配置与验证
| 包 | 版本 | 用途 |
|----|------|------|
| `pydantic` | >=2.5.0 | 配置模型验证（v2 API） |

### 异步通信
| 包 | 版本 | 用途 |
|----|------|------|
| `websockets` | >=10.4 | WebSocket 客户端 |
| `aiohttp` | >=3.13.2 | 异步 HTTP |
| `aiofiles` | >=25.1.0 | 异步文件 IO |

### 浏览器自动化
| 包 | 版本 | 用途 |
|----|------|------|
| `playwright` | >=1.52.0 | 拼多多自动登录 / Cookie 刷新 |

### 中文处理
| 包 | 版本 | 用途 |
|----|------|------|
| `jieba` | >=0.42.1 | 中文分词（知识库检索） |

### 日志
| 包 | 版本 | 用途 |
|----|------|------|
| `loguru` | >=0.7.0 | 结构化日志 |

### 文档解析
| 包 | 版本 | 用途 |
|----|------|------|
| `pypdf` | latest | PDF 解析 |
| `python-docx` | >=1.2.0 | Word 文档解析 |
| `openpyxl` | >=3.1.5 | Excel 解析（xlsx） |
| `xlrd` | >=2.0.2 | Excel 解析（xls） |

### 数据处理
| 包 | 版本 | 用途 |
|----|------|------|
| `pandas` | >=2.1.0,<2.3.0 | 数据处理 |
| `numpy` | >=1.24.0,<2.0.0 | 数值计算 |
| `pillow` | >=12.0.0 | 图像处理 |

### 其他
| 包 | 版本 | 用途 |
|----|------|------|
| `requests` | >=2.28.0 | 同步 HTTP |
| `volcengine` | >=1.0.209 | 火山引擎 SDK |

### 可选依赖（构建）
| 包 | 版本 | 用途 |
|----|------|------|
| `pyinstaller` | >=6.0.0 | 打包为 Windows exe |

## Build System
- `hatchling`（pyproject.toml build-backend）
- 打包目标包：`Agent`, `Channel`, `Message`, `bridge`, `core`, `database`, `ui`, `utils`, `scripts`

## Development Commands

```bash
# 安装依赖
uv sync

# 安装 Playwright 浏览器（必须执行一次）
uv run playwright install chromium

# 启动应用
uv run python app.py
# 或
python app.py

# 打包为 Windows exe
python scripts/build_win_exe.py --clean

# 测试 GSX 接口
python scripts/test_gsx.py

# 测试 SKU 同步
python scripts/test_sku.py
```

## Supported LLM Providers（OpenAI 兼容接口）
| 服务 | api_base | model_name 示例 |
|------|----------|-----------------|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 豆包（火山引擎） | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-seed-1-6-flash-250828` |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` |

## Database
- SQLite（文件路径由 `config.json` 中 `db_path` 配置，默认 `./temp/channel_shop.db`）
- ORM：SQLAlchemy 2.x（declarative base）

## Logging
- Loguru（`utils/logger_loguru.py`）
- 日志文件：`logs/app.log`
- 获取 logger：`from utils.logger_loguru import get_logger; logger = get_logger("ModuleName")`

## Configuration
- 文件：`config.json`（首次运行自动生成）
- 访问：`from config import config; config.get("llm.api_key")`
- 支持点号分隔嵌套访问：`config.get("llm.api_key")`
- 原子保存：临时文件 + `Path.replace()` 重命名

## PyInstaller 打包
- Spec 文件：`scripts/agent_customer.spec`
- 产物目录：`dist/AgentCustomer/`
- Playwright 浏览器路径：打包后从 `.browsers/` 目录读取（`PLAYWRIGHT_BROWSERS_PATH` 环境变量）
