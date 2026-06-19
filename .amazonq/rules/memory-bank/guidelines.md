# Development Guidelines and Patterns

## Code Quality Standards

### 1. Python Version and Type Hints
- **Python 3.11+** required throughout the codebase
- **Type hints** extensively used for function parameters and return values
- **Pydantic models** for configuration validation and data structures
- **Enums** used for fixed value sets (ServiceLifetime, ModelType)

### 2. Logging Standards
- **Loguru** used consistently across all modules
- Structured logging with context binding
- Logger naming convention: `get_logger(__name__)` or `get_logger("ModuleName")`
- Log levels: DEBUG, INFO, WARNING, ERROR
- Business logging with specific event types (message_processed, agent_response, business_error, performance_metric)

### 3. Error Handling
- Custom exception hierarchy (ConfigError with subclasses)
- Context manager for atomic operations
- Exception handling in all async operations
- Error logging with stack traces in development mode

## Code Formatting Patterns

### 1. Module Organization
- All modules use `__init__.py` files for clean exports
- Consistent use of `__all__` lists for public API definition
- Docstrings in triple quotes with proper formatting
- Import organization: standard library → third-party → local modules

### 2. Class and Method Conventions
- **Abstract base classes** (ABC) for defining interfaces
- **Property decorators** for computed attributes
- **Classmethod and staticmethod** used appropriately
- **Async methods** prefixed with `async_` or use `await`
- **Private methods** prefixed with underscore `_`

### 3. Naming Conventions
- **snake_case** for variables, functions, and methods
- **PascalCase** for classes and exceptions
- **UPPER_SNAKE_CASE** for constants
- **Descriptive names** that indicate purpose and type

## Structural Conventions

### 1. Dependency Injection Pattern
- Central DI container (`core/di_container.py`) manages all service lifecycles
- Three service lifetimes: SINGLETON, TRANSIENT, SCOPED
- Service registration via `configure_standard_services()`
- Lazy loading to prevent circular dependencies

**Example DI Usage:**
```python
from core.di_container import container

# Service registration
container.register_singleton(DatabaseManager, factory=lambda: DatabaseManager())

# Service retrieval
db_manager = container.get(DatabaseManager)
```

### 2. Base Service Pattern
- **BaseService** abstract class provides common functionality
- **initialize()** and **dispose()** methods required
- **ConfigurableService** for configuration-driven services
- **AsyncService** for asynchronous operations
- **HealthCheckable** for services supporting health monitoring

**Example Base Service:**
```python
class CustomService(BaseService):
    def __init__(self, logger=None):
        super().__init__(logger)
        
    def initialize(self) -> bool:
        self._initialized = True
        return True
        
    def dispose(self):
        self._disposed = True
```

### 3. Message Queue Pattern
- **SimpleMessageQueue** for FIFO message processing
- **QueueManager** for centralized queue management
- **MessageWrapper** with unique message IDs
- **QueueStats** for monitoring and metrics

### 4. Configuration Management
- **Config** class with thread-safe operations
- **Pydantic models** for type-safe configuration
- **Dot notation** for nested configuration access
- **Atomic updates** with context manager
- **Automatic validation** on load and save

**Example Configuration Access:**
```python
from config import config

# Get nested configuration
api_key = config.get("llm.api_key")
business_start = config.get("business_hours.start")
```

## Textual Standards

### 1. Documentation
- **Module-level docstrings** with purpose and usage
- **Class docstrings** with attributes and methods
- **Function docstrings** with parameters and returns
- **Chinese documentation** for business logic
- **English identifiers** for code elements

### 2. String Formatting
- **f-strings** preferred for string interpolation
- **UTF-8 encoding** for all text files
- **Consistent quotes**: double quotes for docstrings, single quotes for strings
- **Multiline strings** with triple quotes

### 3. Comment Guidelines
- **TODO comments** for pending work
- **FIXME comments** for known issues
- **NOQA comments** for intentional linter violations
- **Type hint comments** when type hints are insufficient

## Practices Followed Throughout Codebase

### 1. Asynchronous Programming
- **async/await** pattern for I/O operations
- **asyncio.Queue** for message processing
- **Async context managers** for resource management
- **Error handling** in async operations

### 2. Thread Safety
- **threading.RLock** for reentrant locking
- **Thread-safe singletons** via DI container
- **Atomic file operations** with temp files
- **Double-checked locking** in DI container

### 3. Resource Management
- **Context managers** for file and network resources
- **Connection pooling** for database access
- **Automatic cleanup** in service disposal
- **Memory management** with deduplication caches

### 4. Testing and Validation
- **Configuration validation** on load
- **Input validation** in API methods
- **Error boundary** around external calls
- **Performance metrics** collection

## Semantic Patterns Overview

### 1. Recurring Implementation Patterns

**Factory Pattern:**
```python
# Used in DI container for service creation
factory=lambda: DatabaseManager(db_path=db_path)
```

**Builder Pattern:**
```python
# Used in message context creation
context = Context(content=message, from_user=user_id, ...)
```

**Observer Pattern:**
```python
# Used in UI logging with signals
log_received = pyqtSignal(str, str, object)
```

**Strategy Pattern:**
```python
# Used in message handlers
class BaseHandler(ABC):
    @abstractmethod
    async def handle(self, context: Context) -> Optional[Reply]:
        pass
```

### 2. Common Architectural Approaches

**Layered Architecture:**
1. UI Layer (PyQt6 widgets and windows)
2. Service Layer (DI-managed business services)
3. Data Layer (SQLAlchemy models and repositories)
4. Integration Layer (Channel APIs and WebSocket)

**Event-Driven Architecture:**
- Message queue for event distribution
- Handler chain for event processing
- Async consumers for event handling

**Plugin Architecture:**
- Channel plugins for different platforms
- Tool plugins for AI agent capabilities
- Handler plugins for message processing

### 3. Design Patterns in Use

**Singleton Pattern:**
- DI container manages singleton instances
- Module-level instances for global services
- Thread-safe singleton implementation

**Decorator Pattern:**
- @injectable decorator for DI registration
- Tool decorators for AI agent functions
- Logging decorators for method tracing

**Adapter Pattern:**
- Channel adapters for platform APIs
- Logger adapters for different logging systems
- Configuration adapters for different formats

### 4. Proper Internal API Usage

**DI Container API:**
```python
# Service registration
container.register_singleton(ServiceClass, factory=...)

# Service retrieval
service = container.get(ServiceClass)

# Async service retrieval
service = await container.get_async(ServiceClass)
```

**Configuration API:**
```python
# Get configuration
value = config.get("section.key", default_value)

# Set configuration
config.set("section.key", value, save=True)

# Atomic update
with config.atomic_update():
    config.set("key1", value1)
    config.set("key2", value2)
```

**Logging API:**
```python
# Get logger
logger = get_logger(__name__)

# Structured logging
logger.info("Message", extra={"event_type": "operation", "details": {...}})

# Business logging
business_logger = get_business_logger("ModuleName")
business_logger.log_message_process(user_id, message_type, processing_time)
```

### 5. Frequently Used Code Idioms

**Async Context Manager:**
```python
async with resource_manager() as resource:
    result = await resource.process()
```

**Error Boundary:**
```python
try:
    result = await risky_operation()
except Exception as e:
    logger.error(f"Operation failed: {e}", exc_info=True)
    return fallback_value
```

**Configuration Validation:**
```python
@field_validator('start', 'end')
@classmethod
def validate_time_format(cls, v: str) -> str:
    try:
        datetime.strptime(v, '%H:%M')
        return v
    except ValueError:
        raise ValueError('时间格式必须为HH:MM，例如08:00')
```

### 6. Popular Annotations and Type Hints

**Type Hints:**
```python
from typing import Optional, Dict, List, Any, Union, Callable

def process_message(
    content: str,
    metadata: Dict[str, Any],
    callback: Optional[Callable[[str], None]] = None
) -> Union[str, None]:
    """Process message with typed parameters."""
```

**Pydantic Models:**
```python
class ConfigModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    business_hours: BusinessHoursConfig = Field(
        default_factory=BusinessHoursConfig,
        description="营业时间配置"
    )
```

**Async Types:**
```python
async def process_async(queue: asyncio.Queue) -> List[MessageWrapper]:
    """Process messages asynchronously."""
```

## Development Workflow Practices

### 1. Before Modifying APIs
1. **Test with curl or Python scripts** to verify actual request parameters
2. **Update parsing code based on real response structures** not assumptions
3. **Validate with mock data or real calls** before deployment

### 2. Code Organization
- **Namespace packages** for major modules (Agent, Channel, Message)
- **Clear separation of concerns** between layers
- **Dependency direction**: higher layers depend on lower layers
- **Circular dependency prevention** via DI and lazy imports

### 3. Testing Approach
- **Integration testing** for platform APIs
- **Unit testing** for business logic
- **Configuration testing** for validation rules
- **Performance testing** for high-concurrency scenarios

### 4. Deployment Considerations
- **Windows executable** packaging support
- **Configuration migration** between versions
- **Database schema evolution** handling
- **Log rotation and management**

These guidelines represent the established patterns and best practices observed throughout the codebase, ensuring consistency and maintainability across all development efforts.