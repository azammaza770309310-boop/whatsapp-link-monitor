# Architecture

## Overview

This project follows **Clean Architecture** principles with clear separation of concerns. The codebase is organized into four layers, each with distinct responsibilities.

## Layer Diagram

```
┌─────────────────────────────────────────────────┐
│           Presentation Layer                     │
│  (Bot Controllers, Keyboards, Orchestrator)     │
├─────────────────────────────────────────────────┤
│           Application Layer                      │
│  (Services: Link, Validation, Backup, Health)   │
│  (Jobs: Scheduler, Validator, Backup)           │
├─────────────────────────────────────────────────┤
│           Domain Layer                           │
│  (Entities, Repository Interfaces)              │
├─────────────────────────────────────────────────┤
│           Infrastructure Layer                   │
│  (Database, Telegram Client, HTTP Server)       │
└─────────────────────────────────────────────────┘
```

## Layers

### 1. Domain Layer (`src/domain/`)

The innermost layer containing pure business logic with **no external dependencies**.

- **`entities.py`** - Domain models: `Link`, `User`, `Submission`, `Backup`
- **`repositories.py`** - Abstract interfaces (ABC) for data access

**Rules:**
- No I/O operations
- No framework dependencies
- Pure Python with type hints

### 2. Application Layer (`src/application/`)

Orchestrates business use cases.

- **`services/`** - Application services that combine domain logic
  - `LinkService` - Link submission, search, statistics
  - `ValidationService` - HTTP-based link validation
  - `BackupService` - Database export/import
  - `HealthService` - Health monitoring
  - `RateLimiter` - Per-user rate limiting
  - `PluginManager` - Extensible plugin system
- **`jobs/`** - Background jobs
  - `Scheduler` - Job scheduling
  - `LinkValidatorJob` - Periodic validation
  - `BackupJob` - Periodic backups

### 3. Infrastructure Layer (`src/infrastructure/`)

Handles all I/O and external service interactions.

- **`database/`** - SQLite connection, migrations, repository implementations
- **`telegram/`** - Telethon client wrapper
- **`http/`** - aiohttp server for health endpoints

### 4. Presentation Layer (`src/presentation/`)

Entry points for external interfaces.

- **`bot/`** - Telegram bot interface
  - `controllers/` - Handle user and admin interactions
  - `keyboards.py` - Inline keyboard definitions
  - `orchestrator.py` - Wires controllers together

## Dependency Rule

Dependencies flow **inward** only:
- Presentation → Application → Domain
- Infrastructure → Domain (implements interfaces)
- Domain depends on nothing external

## Configuration

Configuration is loaded from environment variables via `src/core/config.py`. All settings are validated at startup.

## Error Handling

Custom exception hierarchy in `src/core/exceptions.py`:
- `BotError` - Base
- `ConfigurationError`
- `DatabaseError`
- `MigrationError`
- `TelegramError`
- `ValidationError`
- `LinkValidationError`
- `RateLimitExceededError`
- `AuthorizationError`
- `PluginError`
- `BackupError`

## Logging

Structured JSON logging via `src/core/logging.py`. Supports both JSON and text formats.

## Database Migrations

Migrations are in `migrations/` directory. Each migration is a Python module with an `upgrade(db)` function. The `MigrationRunner` tracks applied migrations in `_migrations` table.

## Plugin Architecture

Plugins extend functionality without modifying core code:

```python
from src.application.services.plugin_manager import Plugin

class MyPlugin(Plugin):
    name = "my_plugin"
    
    async def on_link_saved(self, link):
        # Called when a link is saved
        pass
```

Register in `.env`:
```
PLUGINS=my_module.MyPlugin,another.AnotherPlugin
```

## Testing

- **Unit tests** - `tests/unit/` - Test application services with in-memory repositories
- **Integration tests** - `tests/integration/` - Test SQLite repositories with real database

Run with:
```bash
pytest tests/
```
