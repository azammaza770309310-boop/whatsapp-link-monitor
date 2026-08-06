# WhatsApp Link Monitor - Production Edition

[![CI](https://github.com/azammaza770309310-boop/whatsapp-link-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/azammaza770309310-boop/whatsapp-link-monitor/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A production-grade Telegram bot for managing WhatsApp invite links that users voluntarily submit.

## ⚠️ Compliance Notice

This bot is designed to operate strictly within Telegram's Terms of Service:
- Users voluntarily submit WhatsApp invite links
- The bot does NOT scrape private chats
- The bot does NOT bypass platform restrictions
- The bot does NOT collect links from unauthorized sources
- All data is user-submitted or from groups where the bot is an explicit admin

## 🏗️ Architecture

Clean Architecture with clearly separated layers:

```
┌─────────────────────────────────────────┐
│  Presentation (Bot Controllers)         │
├─────────────────────────────────────────┤
│  Application (Services + Jobs)          │
├─────────────────────────────────────────┤
│  Domain (Entities + Interfaces)         │
├─────────────────────────────────────────┤
│  Infrastructure (DB, Telegram, HTTP)    │
└─────────────────────────────────────────┘
```

## ✨ Features

### Core
- ✅ Store submitted WhatsApp invite links
- ✅ Detect expired/revoked invite links (HTTP validation)
- ✅ Auto-remove invalid links
- ✅ Categorize links (groups, channels, direct, etc.)
- ✅ Full-text search
- ✅ Statistics dashboard
- ✅ Admin panel with role-based permissions
- ✅ Rate limiting (per-user)
- ✅ Duplicate detection (content hash)
- ✅ Export/Import database (JSON)

### Operations
- ✅ Scheduled background jobs
- ✅ Health monitoring (HTTP `/health` endpoint)
- ✅ Automatic backups
- ✅ Plugin architecture (registry-based)
- ✅ Structured logging (JSON)
- ✅ Database migrations
- ✅ Automatic recovery from failures

### Deployment
- ✅ Docker + docker-compose
- ✅ Linux hosting support
- ✅ CI/CD via GitHub Actions
- ✅ Unit + integration tests
- ✅ Full documentation

## 🚀 Quick Start

### Docker (Recommended)

```bash
git clone https://github.com/azammaza770309310-boop/whatsapp-link-monitor.git
cd whatsapp-link-monitor
cp config/.env.example .env
# Edit .env with your credentials
docker-compose up -d
```

### Manual

```bash
git clone https://github.com/azammaza770309310-boop/whatsapp-link-monitor.git
cd whatsapp-link-monitor
python -m venv venv
source venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
cp config/.env.example .env
# Edit .env
python -m src.main
```

## ⚙️ Configuration

See `config/.env.example` for all environment variables.

Required:
- `BOT_TOKEN` - Telegram bot token from @BotFather
- `ADMIN_IDS` - Comma-separated admin user IDs
- `CHANNEL_ID` - Destination channel ID

Optional:
- `DATABASE_PATH` - SQLite path (default: `data/links.db`)
- `LOG_LEVEL` - Logging level (default: `INFO`)
- `VALIDATION_INTERVAL_HOURS` - Link validation interval (default: 24)
- `BACKUP_INTERVAL_HOURS` - Backup interval (default: 24)
- `RATE_LIMIT_PER_MINUTE` - Per-user rate limit (default: 10)

## 📚 Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Deployment](docs/DEPLOYMENT.md)
- [API Reference](docs/API.md)
- [Contributing](docs/CONTRIBUTING.md)

## 🧪 Testing

```bash
pip install -r requirements-dev.txt
pytest tests/
```

## 📦 Project Structure

```
src/
├── core/                  # Cross-cutting concerns
│   ├── config.py          # Configuration management
│   ├── logging.py         # Structured logging
│   ├── exceptions.py      # Custom exceptions
│   └── container.py       # Dependency injection
├── domain/                # Business logic (no I/O)
│   ├── entities.py        # Domain models
│   └── repositories.py    # Repository interfaces
├── infrastructure/        # I/O & external services
│   ├── database/          # SQLite + migrations
│   ├── telegram/          # Telegram client + handlers
│   └── http/              # HTTP health server
├── application/           # Use cases
│   ├── services/          # Application services
│   └── jobs/              # Background jobs
└── presentation/          # Entry points
    └── bot/               # Telegram bot controllers
```

## 🔌 Plugin Architecture

Plugins can extend the bot without modifying core code:

```python
from src.application.services.plugin_manager import Plugin

class MyPlugin(Plugin):
    name = "my_plugin"
    
    async def on_link_saved(self, link):
        # Custom logic
        pass
```

Register in config:
```yaml
plugins:
  - my_plugin.MyPlugin
```

## 📊 Database Schema

See `migrations/` for the schema. Key tables:
- `links` - Stored WhatsApp links
- `categories` - Link categories
- `users` - Bot users with roles
- `submissions` - Link submission history
- `backups` - Backup metadata

## 🛡️ Security

- Role-based access control (user, admin, super_admin)
- Rate limiting per user
- Input validation on all endpoints
- SQL injection prevention (parameterized queries)
- No sensitive data in logs

## 📈 Monitoring

- Health endpoint: `GET /health`
- Metrics endpoint: `GET /metrics`
- Structured JSON logs
- Backup history in DB

## 📝 License

MIT License - see [LICENSE](LICENSE)
