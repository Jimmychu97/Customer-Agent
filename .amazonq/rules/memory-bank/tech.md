# Technology Stack

## Language & Runtime
- Python >= 3.11 (required)
- Windows OS (primary target; PyQt6 UI and Playwright automation)
- Package manager: `uv` (preferred) or `pip`

## Core Dependencies (pyproject.toml)

| Category | Package | Version | Purpose |
|---|---|---|---|
| UI | pyqt6 | >=6.9.0 | Desktop GUI framework |
| UI | pyqt6-fluent-widgets | >=1.9.0 | Fluent Design UI components |
| AI/LLM | openai | >=1.109.1 | OpenAI-compatible API client |
| AI/LLM | tiktoken | >=0.12.0 | Token counting for context window management |
| Database | sqlalchemy | >=2.0.43 | ORM + SQLite backend |
| Config | pydantic | >=2.5.0 | Type-safe config models and validation |
| Logging | loguru | >=0.7.0 | Structured logging with rotation |
| Async | websockets | >=10.4 | WebSocket client for Pinduoduo |
| Async | aiohttp | >=3.13.2 | Async HTTP client |
| Async | aiofiles | >=25.1.0 | Async file I/O |
| Browser | playwright | >=1.52.0 | Chromium automation for PDD login/Cookie |
| NLP | jieba | >=0.42.1 | Chinese word segmentation for knowledge search |
| Data | pandas | >=2.1.0,<2.3.0 | Data processing (product sync) |
| Data | numpy | >=1.24.0,<2.0.0 | Numerical support |
| HTTP | requests | >=2.28.0 | Synchronous HTTP (API calls) |
| Image | pillow | >=12.0.0 | Image processing |
| Docs | pypdf | latest | PDF parsing for knowledge import |
| Docs | python-docx | >=1.2.0 | Word document parsing |
| Docs | openpyxl | >=3.1.5 | Excel file parsing |
| Docs | xlrd | >=2.0.2 | Legacy Excel (.xls) support |
| Proxy | python-socks[asyncio] | >=2.8.2 | SOCKS proxy support for async connections |
| Cloud | volcengine | >=1.0.209 | ByteDance/Volcengine SDK (Doubao models) |
| Build | pyinstaller | >=6.0.0 | Windows exe packaging (optional dep) |

## Development Commands

```bash
# Install dependencies
uv sync

# Run application
uv run python app.py
# or
python app.py

# Install Playwright browser (required for PDD login)
uv run playwright install chromium
# Alternative if above fails:
.venv\Scripts\python.exe -m playwright install chromium

# Build Windows executable
python scripts/build_win_exe.py --clean

# Test GSX API integration
python scripts/test_gsx.py

# Test SKU sync
python scripts/test_sku.py
```

## LLM Integration
Supports any OpenAI-compatible API endpoint:

| Provider | api_base | model_name example |
|---|---|---|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| Volcengine (Doubao) | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-seed-1-6-flash-250828` |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` |

## Database
- SQLite (file-based, no server required)
- Default path: configured via `config.json` → `db_path`
- ORM: SQLAlchemy 2.x with `session_scope()` context manager pattern
- Agent session history stored in `agent_messages` table

## Build & Packaging
- Build tool: PyInstaller >= 6.0.0
- Spec file: `scripts/agent_customer.spec`
- Output: `dist/AgentCustomer/` (self-contained directory)
- Playwright browsers bundled at `.browsers/` relative to exe
- `PLAYWRIGHT_BROWSERS_PATH` env var set at runtime to locate bundled browsers

## Configuration File
- Location: `config.json` in project root (auto-created on first run)
- Format: JSON with Pydantic validation on load
- Atomic save: write to `.tmp` then rename to prevent corruption
- Access pattern: `config.get("llm.api_key")` (dot-notation for nested keys)

## Proxy Support
- HTTP proxy: `http://admin:Asd123123@43.139.22.120:3128`
- SOCKS proxy support via `python-socks[asyncio]` for WebSocket connections

## Python Version File
- `.python-version` pins the Python version for uv
