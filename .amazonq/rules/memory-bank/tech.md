# Tech Stack: Agent-Customer

## Language & Runtime
<<<<<<< HEAD

- **Python >= 3.11** (`.python-version` pins version)
- **Windows only** (UI framework and Playwright dependencies)
- **Package manager**: `uv` (with `uv.lock` for reproducible installs)
- **Build backend**: `hatchling`

## Core Dependencies

| Category | Package | Version | Purpose |
|----------|---------|---------|---------|
| UI | `pyqt6` | >=6.9.0 | Desktop GUI framework |
| UI | `pyqt6-fluent-widgets` | >=1.9.0 | Fluent Design UI components |
| AI | `openai` | >=1.109.1 | OpenAI-compatible API client |
| Config | `pydantic` | >=2.5.0 | Config validation models |
| Database | `sqlalchemy` | >=2.0.43 | ORM + SQLite |
| WebSocket | `websockets` | >=10.4 | PDD WebSocket connection |
| Browser | `playwright` | >=1.52.0 | PDD auto-login & cookie refresh |
| HTTP | `requests` | >=2.28.0 | Sync HTTP (PDD API calls) |
| HTTP async | `aiohttp` | >=3.13.2 | Async HTTP |
| Logging | `loguru` | >=0.7.0 | Structured logging |
| Tokenization | `tiktoken` | >=0.12.0 | LLM token counting |
| NLP | `jieba` | >=0.42.1 | Chinese word segmentation for KB search |
| Data | `pandas` | >=2.1.0,<2.3.0 | Data manipulation |
| Data | `numpy` | >=1.24.0,<2.0.0 | Numerical operations |
| File parsing | `pypdf` | latest | PDF document parsing |
| File parsing | `python-docx` | >=1.2.0 | Word document parsing |
| File parsing | `openpyxl` | >=3.1.5 | Excel (.xlsx) parsing |
| File parsing | `xlrd` | >=2.0.2 | Excel (.xls) parsing |
| Image | `pillow` | >=12.0.0 | Image processing |
| Volcengine | `volcengine` | >=1.0.209 | Doubao/Volcengine LLM SDK |
| Async file | `aiofiles` | >=25.1.0 | Async file I/O |

## Development Commands
=======

- Python 3.11 (pinned via `.python-version`)
- Windows desktop application (no cross-platform requirement)
- Package manager: `uv` with `pyproject.toml` (hatchling build backend)

## Run & Build Commands
>>>>>>> 528dddd363ca2635fa7b3e6e91cccb41ce7054bc

```bash
# Install dependencies
uv sync

# Install Playwright browsers (required for PDD login)
uv run playwright install chromium

# Run application
uv run python app.py

<<<<<<< HEAD
# Install Playwright browser (required for PDD login)
uv run playwright install chromium

# Build Windows executable
python scripts/build_win_exe.py --clean
# Output: dist/AgentCustomer/
=======
# Build standalone Windows exe (PyInstaller)
python scripts/build_win_exe.py --clean
# Output: dist/AgentCustomer/
```

## Core Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `pyqt6` | >=6.9.0 | Desktop UI framework |
| `pyqt6-fluent-widgets` | >=1.9.0 | Fluent Design UI components |
| `pydantic` | >=2.5.0 | Data validation, config models, message models |
| `sqlalchemy` | >=2.0.43 | ORM, SQLite database |
| `openai` | >=1.109.1 | OpenAI-compatible LLM client |
| `loguru` | >=0.7.0 | Structured logging with rotation |
| `websockets` | >=10.4 | Async WebSocket client (PDD connection) |
| `playwright` | >=1.52.0 | Browser automation (PDD login/cookie refresh) |
| `requests` | >=2.28.0 | HTTP API calls |
| `aiohttp` | >=3.13.2 | Async HTTP |
| `jieba` | >=0.42.1 | Chinese text segmentation (knowledge search) |
| `tiktoken` | >=0.12.0 | Token counting for LLM context management |
| `pandas` | >=2.1.0,<2.3.0 | Data manipulation |
| `numpy` | >=1.24.0,<2.0.0 | Numeric operations |
| `pillow` | >=12.0.0 | Image processing |
| `pypdf` | latest | PDF document parsing |
| `python-docx` | >=1.2.0 | Word document parsing |
| `openpyxl` | >=3.1.5 | Excel file parsing |
| `xlrd` | >=2.0.2 | Legacy Excel reading |
| `volcengine` | >=1.0.209 | Volcengine (Doubao/ByteDance) SDK |
| `aiofiles` | >=25.1.0 | Async file I/O |

## Optional / Build-only Dependencies

```toml
[project.optional-dependencies]
build = ["pyinstaller>=6.0.0"]
>>>>>>> 528dddd363ca2635fa7b3e6e91cccb41ce7054bc
```

## Database

<<<<<<< HEAD
- **Engine**: SQLite via SQLAlchemy 2.x (async-capable ORM)
- **Location**: Auto-configured via `config.json` → `db_path`
- **Models**: `Shop`, `ProductKnowledge`, `CustomerServiceKnowledge`, `Keyword`, etc. in `database/models.py`
- **Access pattern**: Session-per-operation via `db_manager` (connection pool in `database/connection_pool.py`)

## Configuration System

- **File**: `config.json` (auto-generated on first run from `config_base` defaults)
- **Manager**: `config.py` → `Config` class with thread-safe `RLock`, Pydantic validation, atomic saves
- **Access pattern**: `config.get("llm.api_key")` (dot-notation for nested keys) or `config.get_model()` for typed `ConfigModel`
- **Global instance**: `from config import config` — imported first in `app.py`

## Logging

- **Library**: Loguru
- **Setup**: `utils/logger_loguru.py` → `get_logger(name)` returns named logger
- **Output**: `logs/app.log` + console
- **Pattern**: `logger = get_logger("ModuleName")` at module top level

## Build / Packaging

- **Tool**: PyInstaller >= 6.0.0 (optional dependency group `build`)
- **Spec**: `scripts/agent_customer.spec`
- **Script**: `scripts/build_win_exe.py` — wraps PyInstaller with clean/copy steps
- **Playwright browsers**: Packaged into `.browsers/` directory adjacent to exe
- **Frozen detection**: `getattr(sys, 'frozen', False)` pattern used in `app.py` and `utils/runtime_path.py`
=======
- SQLite (file: `temp/channel_shop.db`)
- SQLAlchemy ORM with `declarative_base()`
- Tables: `channels`, `shops`, `accounts`, `keywords`, `product_knowledge`, `customer_service_knowledge`, `coupons`, `vouchers`, `voucher_logs`, `goods_commit`, `orders`

## AI / LLM Integration

- OpenAI-compatible API (any provider): DeepSeek, Doubao (Volcengine), Kimi (Moonshot), etc.
- Custom Agent framework in `Agent/CustomerAgent/custom/`:
  - Multi-turn tool calling loop
  - Per-user session history management
  - Tool registration via `@tool` decorator
  - tiktoken-based token budget tracking

## Logging

- Loguru with file rotation at 10 MB, 5 backup files (zip compressed)
- Log file: `logs/app.log`
- Console output disabled in PyInstaller frozen builds
- `get_logger(name)` factory function used throughout
- `UILogHandler` bridges Loguru → PyQt6 signal for live log viewer in UI

## Async Architecture

- Each `AutoReplyThread` (QThread) runs its own `asyncio` event loop
- `asyncio.Queue` used for message queuing
- `asyncio.Semaphore` for concurrency control in message consumer
- `asyncio.create_task` with `done_callback` for task lifecycle tracking
- WebSocket connection via `websockets` library in async context

## Config System

- `config.json` in project root (auto-created on first run)
- `Config` class in `config.py`: thread-safe with `threading.RLock()`
- Pydantic `ConfigModel` for validation
- Supports dot-notation access: `config.get("llm.api_key")`
- Atomic save via temp file + rename
- `@contextmanager` `atomic_update()` for rollback-safe batch updates
- Global convenience functions: `get_config()`, `set_config()`, `save_config()`

## Packaging (PyInstaller)

- Spec file: `scripts/agent_customer.spec`
- Build script: `scripts/build_win_exe.py`
- Playwright browsers bundled in `.browsers/` relative to exe
- `runtime_path.py` and `path_utils.py` handle `sys._MEIPASS` paths in frozen builds
>>>>>>> 528dddd363ca2635fa7b3e6e91cccb41ce7054bc
