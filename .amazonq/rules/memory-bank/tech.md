# Technology Stack

## Language & Runtime
- Python >= 3.11 (`.python-version` pins exact version)
- Windows OS only (PyQt6 desktop app, Playwright browser automation)

## Package Manager
- `uv` — fast Python package manager
  - `uv sync` — install all dependencies
  - `uv run python app.py` — run with venv
  - `uv run playwright install chromium` — install browser for login automation

## Key Dependencies (pyproject.toml)

| Category | Package | Purpose |
|----------|---------|---------|
| UI | `pyqt6>=6.9.0`, `pyqt6-fluent-widgets>=1.9.0` | Desktop GUI with Fluent Design |
| AI/LLM | `openai>=1.109.1` | OpenAI-compatible API client |
| Config | `pydantic>=2.5.0` | Config model validation |
| Database | `sqlalchemy>=2.0.43` | ORM + SQLite |
| Async | `asyncio` (stdlib), `websockets>=10.4`, `aiohttp>=3.13.2`, `aiofiles>=25.1.0` | WebSocket + async I/O |
| Browser | `playwright>=1.52.0` | Pinduoduo login & cookie refresh |
| HTTP | `requests>=2.28.0` | Sync HTTP with retry adapter |
| Logging | `loguru>=0.7.0` | Structured logging |
| NLP | `jieba>=0.42.1` | Chinese word segmentation for KB search |
| Tokenization | `tiktoken>=0.12.0` | Token counting for LLM context |
| Data | `pandas>=2.1.0,<2.3.0`, `numpy>=1.24.0,<2.0.0` | Data processing |
| Docs | `pypdf`, `python-docx>=1.2.0`, `openpyxl>=3.1.5`, `xlrd>=2.0.2` | Knowledge base document parsing |
| Build | `pyinstaller>=6.0.0` (optional) | Windows exe packaging |
| Volcengine | `volcengine>=1.0.209` | Volcengine (ByteDance) SDK |

## Development Commands

```bash
# Install dependencies
uv sync

# Run application
uv run python app.py
# or
python app.py

# Install Playwright browser (required for login)
uv run playwright install chromium
# Alternative if above fails:
.venv\Scripts\python.exe -m playwright install chromium

# Build Windows executable
python scripts/build_win_exe.py --clean
# Output: dist/AgentCustomer/
```

## Configuration
- `config.json` — auto-created on first run at project root
- Accessed via `config.get("llm.api_key")` (dot-notation nested access)
- Thread-safe with `threading.RLock()`
- Atomic saves via temp file + rename
- Validated with Pydantic `ConfigModel`

## Database
- SQLite via SQLAlchemy ORM
- Default path: `./temp/channel_shop.db`
- Configurable via `config.json` → `db_path`

## Logging
- Loguru — `from utils.logger_loguru import get_logger`
- Usage: `self.logger = get_logger(self.__class__.__name__)`
- Log file: `logs/app.log`

## LLM Integration
- Any OpenAI-compatible API (DeepSeek, Doubao/Volcengine, Kimi, etc.)
- Configured via `llm.api_base`, `llm.api_key`, `llm.model_name` in config
- Token counting with `tiktoken`

## Async/Sync Bridge
- `utils/async_helper.py` → `run_async_in_thread()` — runs async coroutines from sync code without event loop conflicts
- Used in `BaseRequest._run_async_login_func()` for cookie refresh from sync HTTP handlers

## Build System
- `hatchling` build backend
- `pyinstaller` for Windows exe packaging
- Playwright browsers bundled to `.browsers/` directory relative to exe
