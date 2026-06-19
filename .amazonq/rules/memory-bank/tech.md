# Technology Stack and Development Setup

## Programming Languages and Versions
- **Primary Language**: Python 3.11+ (requires >=3.11)
- **Platform**: Windows (primary), with potential Linux/macOS support via PyQt6
- **Build System**: Hatchling (specified in pyproject.toml)

## Core Dependencies

### UI Framework
- `pyqt6>=6.9.0`: Qt6 framework for desktop UI
- `pyqt6-fluent-widgets>=1.9.0`: Modern Fluent Design UI components

### AI and ML
- `openai>=1.109.1`: OpenAI-compatible API client (supports multiple LLM providers)
- `volcengine>=1.0.209`: Volcano Engine/ByteDance LLM support
- `tiktoken>=0.12.0`: Token counting for LLM context management
- `jieba>=0.42.1`: Chinese text segmentation for knowledge base retrieval

### Data and Database
- `sqlalchemy>=2.0.43`: ORM for SQLite database
- `pandas>=2.1.0,<2.3.0`: Data manipulation and analysis
- `numpy>=1.24.0,<2.0.0`: Numerical computing

### Web and Networking
- `websockets>=10.4`: WebSocket client/server for real-time messaging
- `requests>=2.28.0`: HTTP client for API calls
- `aiohttp>=3.13.2`: Asynchronous HTTP client
- `aiofiles>=25.1.0`: Async file operations

### Document Processing
- `pypdf`: PDF document parsing
- `python-docx>=1.2.0`: Word document processing
- `openpyxl>=3.1.5`: Excel file handling
- `xlrd>=2.0.2`: Legacy Excel file support

### Configuration and Validation
- `pydantic>=2.5.0`: Data validation and settings management
- Configuration uses Pydantic models with type safety

### Logging and Utilities
- `loguru>=0.7.0`: Modern, structured logging
- `pillow>=12.0.0`: Image processing for UI

### Build Dependencies (optional)
- `pyinstaller>=6.0.0`: For creating Windows executables

## Development Commands

### Installation
```bash
# Install dependencies using uv (recommended)
uv sync

# Or using pip (if uv not available)
pip install -e .
```

### Running the Application
```bash
# Start the main application
python app.py
```

### Building Executable
```bash
# Build Windows executable
python scripts/build_win_exe.py --clean

# Build output location: dist/AgentCustomer/
```

### Testing and Development
```bash
# Test specific components
python scripts/test_sku.py  # Test SKU/Product functionality

# Install Playwright browsers (for web automation)
python scripts/install_playwright.py
```

## Development Environment Setup

### 1. Python Environment
```bash
# Ensure Python 3.11+ is installed
python --version  # Should show 3.11.x or higher

# Set up virtual environment (optional with uv)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install uv package manager (recommended)
pip install uv
```

### 2. Project Configuration
1. **First Run**: Application automatically creates `config.json` with default settings
2. **Required Configuration**:
   - LLM API settings (model name, API key, base URL)
   - Pinduoduo platform credentials (client_id, client_secret, access_token)
   - GSX verification API credentials (if using device verification)

### 3. Database Setup
- SQLite database automatically created at `temp/channel_shop.db`
- No manual database setup required
- Product knowledge automatically synced from Pinduoduo API

### 4. Platform Integration
- Requires Pinduoduo seller account credentials
- WebSocket connection to Pinduoduo customer service system
- API access for product data and message sending

## File Structure and Build Configuration

### Key Configuration Files
- `pyproject.toml`: Project metadata and dependencies
- `config.json`: User configuration (auto-generated)
- `uv.lock`: Dependency lock file (if using uv)
- `.python-version`: Python version specification

### Build Configuration
- **Entry Point**: `app.py`
- **Package Structure**: Multiple namespace packages (Agent, Channel, Message, etc.)
- **Windows Build**: PyInstaller configuration in `scripts/build_exe.py`
- **Icon Files**: Located in `icon/` directory

## Technology Patterns

### 1. Async Patterns
- WebSocket connections use `asyncio` and `websockets`
- Message queue processing is asynchronous
- File operations use `aiofiles` for async I/O

### 2. DI Container Pattern
- All services managed through DI container
- Prevents tight coupling between components
- Enables easier testing and mocking

### 3. Configuration Management
- Thread-safe configuration access
- Automatic validation with Pydantic
- Support for nested configuration paths

### 4. Logging Strategy
- Structured logging with Loguru
- Log files stored in `logs/` directory
- Rotating logs with proper file management

## Platform-Specific Considerations

### Windows-Specific
- Built and tested primarily on Windows
- Executable packaging optimized for Windows
- Uses Windows-specific browser paths for Playwright

### Cross-Platform Potential
- PyQt6 supports Linux and macOS
- Database uses SQLite (cross-platform)
- Most dependencies are cross-platform

## Performance Considerations
- Asynchronous message processing for high concurrency
- Connection pooling for database access
- Caching layer for frequently accessed data
- Token counting to manage LLM context windows