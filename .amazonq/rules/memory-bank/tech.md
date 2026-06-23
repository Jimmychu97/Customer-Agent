# Technology Stack

## Language & Runtime

- Python 3.11 (pinned via `.python-version`)
- Windows desktop application (no cross-platform requirement)
- Package manager: `uv` with `pyproject.toml` (hatchling build backend)

## Run & Build Commands

```bash
# Install dependencies
uv sync

# Install Playwright browsers (required for PDD login)
uv run playwright install chromium

# Run application
uv run python app.py
# or
python app.py

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
```

## Database

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
