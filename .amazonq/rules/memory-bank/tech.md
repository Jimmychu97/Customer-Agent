# Tech Stack: Agent-Customer

## Language & Runtime

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

```bash
# Install dependencies
uv sync

# Run application
uv run python app.py

# Install Playwright browser (required for PDD login)
uv run playwright install chromium

# Build Windows executable
python scripts/build_win_exe.py --clean
# Output: dist/AgentCustomer/
```

## Database

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
