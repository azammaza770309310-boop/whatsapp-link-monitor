# Contributing

## Development Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/azammaza770309310-boop/whatsapp-link-monitor.git
   cd whatsapp-link-monitor
   ```

2. **Create virtual environment**
   ```bash
   python3.11 -m venv venv
   source venv/bin/activate  # Linux/macOS
   # or
   venv\Scripts\activate  # Windows
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   ```

4. **Set up environment**
   ```bash
   cp config/.env.example .env
   # Edit .env with test credentials
   ```

5. **Run tests**
   ```bash
   pytest tests/ -v
   ```

## Code Style

- **Formatter**: Black (line length: 100)
- **Linter**: flake8
- **Type checker**: mypy
- **Import order**: isort (profile: black)

Run all checks:
```bash
black src tests
flake8 src tests
mypy src --ignore-missing-imports
```

## Architecture Guidelines

### Layer Dependencies

- **Domain layer** must not import from other layers
- **Application layer** depends on Domain (interfaces only)
- **Infrastructure layer** implements Domain interfaces
- **Presentation layer** uses Application services

### Adding a New Feature

1. **Domain layer**: Add entity or update interface in `src/domain/`
2. **Infrastructure layer**: Implement repository in `src/infrastructure/database/repositories/`
3. **Application layer**: Add service in `src/application/services/`
4. **Presentation layer**: Add controller method in `src/presentation/bot/controllers/`
5. **Tests**: Add unit tests in `tests/unit/` and integration tests in `tests/integration/`

### Adding a Migration

1. Create a new file in `migrations/` with format `NNN_description.py`
2. Implement `async def upgrade(db: Database) -> None:`
3. Test locally by running the application

Example:
```python
# migrations/004_add_tags.py
from src.infrastructure.database.connection import Database

async def upgrade(db: Database) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
    """)
```

### Adding a Plugin

1. Create a plugin class inheriting from `Plugin`
2. Implement desired hooks
3. Add to `.env`: `PLUGINS=my_module.MyPlugin`

Example:
```python
# my_plugin.py
from src.application.services.plugin_manager import Plugin
from src.domain.entities import Link

class NotificationPlugin(Plugin):
    name = "notification"
    
    async def on_link_saved(self, link: Link) -> None:
        # Send notification to external service
        pass
```

## Testing

### Unit Tests

Unit tests use in-memory repository implementations:

```python
@pytest.fixture
def link_service():
    link_repo = InMemoryLinkRepository()
    user_repo = InMemoryUserRepository()
    submission_repo = InMemorySubmissionRepository()
    return LinkService(link_repo, user_repo, submission_repo)
```

### Integration Tests

Integration tests use a temporary SQLite database:

```python
@pytest.fixture
async def database():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    # ... create database, run migrations
    yield db
    # ... cleanup
```

### Running Tests

```bash
# All tests
pytest

# With coverage
pytest --cov=src --cov-report=html

# Specific test file
pytest tests/unit/test_link_service.py

# Verbose
pytest -v
```

## Pull Request Process

1. Create a feature branch: `git checkout -b feature/my-feature`
2. Make your changes
3. Ensure tests pass: `pytest`
4. Ensure code is formatted: `black src tests`
5. Commit with clear message
6. Push and create pull request

### Commit Message Format

```
type: brief description

Optional longer description
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `refactor`: Code refactoring
- `test`: Tests
- `chore`: Maintenance

## Release Process

1. Update version in `pyproject.toml`
2. Update `CHANGELOG.md`
3. Create tag: `git tag v1.0.0`
4. Push tag: `git push origin v1.0.0`
5. CI/CD will build and publish Docker image
