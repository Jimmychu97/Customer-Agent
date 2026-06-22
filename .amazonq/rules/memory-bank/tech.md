# Technology Stack

## Languages & Runtime
- Python 3.11 (pinned via `.python-version`)
- Windows desktop target (primary platform)

## Package Manager
- `uv` — fast Python package manager and virtual environment tool
- Project definition: `pyproject.toml` (hatchling build backend)

## Key Dependencies

| Category | Library | Version | Purpose |
|---|---|---|---|
| UI | PyQt6 | >=6.9.0 | Desktop GUI framework |
| UI | pyqt6-fluent-widgets | >=1.9.0 | Fluent Design UI components |
| AI/LLM | openai | >=1.109.1 | OpenAI-compatible API client |
| AI/LLM | tiktoken | >=0.12.0 | Token counting |
| Database | SQLAlchemy | >=2.0.43 | ORM and session management |
| Async | websockets | >=10.4 | WebSocket client for PDD |
| Async | aiohttp | >=3.13.2 | Async HTTP client |
| Async | aiofiles | >=25.1.0 | Async file I/O |
| Browser | playwright | >=1.52.0 | Automated PDD login / cookie refresh |
| Config | pydantic | >=2.5.0 | Config model validation |
| Logging | loguru | >=0.7.0 | Structured logging with rotation |
| NLP | jieba | >=0.42.1 | Chinese word segmentation for knowledge retrieval |
| HTTP | requests | >=2.28.0 | Synchronous HTTP requests |
| Data | pandas | >=2.1.0,<2.3.0 | Data processing |
| Data | numpy | >=1.24.0,<2.0.0 | Numerical operations |
| Docs | pypdf | latest | PDF parsing |
| Docs | python-docx | >=1.2.0 | Word document parsing |
| Docs | openpyxl | >=3.1.5 | Excel (xlsx) parsing |
| Docs | xlrd | >=2.0.2 | Excel (xls) parsing |
| Image | Pillow | >=12.0.0 | Image processing |
| Cloud | volcengine | >=1.0.209 | Volcengine (Doubao) SDK |
| Build | pyinstaller | >=6.0.0 (optional) | Windows exe packaging |

## Development Commands

```bash
# Install dependencies
uv sync

# Run application
uv run python app.py
# or
python app.py

# Install Playwright browser (required once)
uv run playwright install chromium

# Build Windows executable
python scripts/build_win_exe.py --clean

# Run test scripts
uv run python scripts/test_gsx.py
uv run python scripts/test_sku.py
```

## Database
- SQLite via SQLAlchemy ORM
- Default path: `./temp/channel_shop.db`
- Configurable via `config.json` → `db_path`
- Connection pool configured in `database/connection_pool.py`

## Logging
- Loguru with automatic file rotation (10 MB, 5 backups, zip compression)
- Log file: `logs/app.log`
- Console output enabled in development (disabled in frozen/packaged exe)
- Level controlled via `LOG_LEVEL` environment variable (default: `info`)
- Per-module loggers via `get_logger("ModuleName")`
- `UILogHandler` bridges Loguru → PyQt6 signals for in-app log viewer

## LLM Integration
- Supports any OpenAI-compatible API endpoint
- Configured via `config.json`: `llm.api_base`, `llm.api_key`, `llm.model_name`
- Compatible providers: DeepSeek, Doubao (Volcengine), Kimi (Moonshot), OpenAI

## Browser Automation
- Playwright Chromium for Pinduoduo automated login and cookie refresh
- Browser binaries stored at `.browsers/` in project root
- Path configured via `PLAYWRIGHT_BROWSERS_PATH` env var (supports packaged exe)

## Build & Distribution
- PyInstaller spec: `scripts/agent_customer.spec`
- Build script: `scripts/build_win_exe.py --clean`
- Output: `dist/AgentCustomer/` — self-contained, no Python required
