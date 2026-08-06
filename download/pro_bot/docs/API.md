# API Reference

## HTTP Endpoints

The bot exposes HTTP endpoints for monitoring and health checks.

### `GET /`

Alias for `/health`.

**Response:**
```json
{
  "status": "ok",
  "uptime_seconds": 3600,
  "link_count": 150,
  "user_count": 25,
  "last_error": null,
  "last_error_at": null,
  "timestamp": "2026-01-01T12:00:00.000Z"
}
```

### `GET /health`

Health check endpoint.

**Response:** Same as `GET /`.

**Status codes:**
- `200 OK` - Bot is healthy
- `200 OK` with `status: "degraded"` - Bot running but with issues

### `GET /metrics`

Detailed metrics endpoint.

**Response:**
```json
{
  "status": "ok",
  "uptime_seconds": 3600,
  "link_count": 150,
  "user_count": 25,
  "timestamp": "2026-01-01T12:00:00.000Z"
}
```

## Telegram Bot Commands

### User Commands

| Command | Description |
|---------|-------------|
| `/start` | Show main menu |
| `/help` | Show help text |
| `/stats` | Show link statistics |

### User Interactions

- **Send a WhatsApp link** - Bot extracts and saves the link
- **Send search text** - Bot searches saved links
- **Button: 📤 إرسال رابط** - Instructions for submitting
- **Button: 🔍 بحث** - Enter search mode
- **Button: 📊 إحصائيات** - View statistics
- **Button: 📋 آخر الروابط** - View recent links
- **Button: ❓ المساعدة** - Help text

### Admin Commands (Admin Panel)

Available via **⚙️ لوحة الإدارة** button (admin users only):

| Button | Description |
|--------|-------------|
| 📊 إحصائيات مفصلة | Detailed statistics |
| 👥 المستخدمون | List users and admins |
| 🔄 تحقق من الروابط | Trigger validation job |
| 💾 نسخ احتياطي | Create manual backup |
| 📤 تصدير | Export links as JSON |
| 📥 استيراد | Import links from JSON |

## WhatsApp Link Patterns

The bot recognizes these URL patterns:

| Pattern | Category |
|---------|----------|
| `chat.whatsapp.com/...` | `group_invite` |
| `whatsapp.com/channel/...` | `channel` |
| `wa.me/<phone>` | `direct_chat` |
| `wa.me/message/...` | `message_link` |
| `api.whatsapp.com/send?...` | `api_send` |
| `api.whatsapp.com/q?...` | `qr_code` |
| `l.whatsapp.com/...` | `short_link` |

## Database Schema

### `links` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment ID |
| `url` | TEXT | Original URL |
| `normalized_url` | TEXT UNIQUE | Normalized for dedup |
| `category` | TEXT | Link category |
| `status` | TEXT | `active`, `expired`, `revoked`, `invalid`, `unverified` |
| `title` | TEXT | Optional title |
| `description` | TEXT | Optional description |
| `submitted_by` | INTEGER | User ID |
| `submitted_by_name` | TEXT | User display name |
| `source_group_id` | INTEGER | Group ID if from group |
| `source_group_name` | TEXT | Group name |
| `content_hash` | TEXT | MD5 hash for dedup |
| `message_text` | TEXT | Original message text |
| `verified_at` | TIMESTAMP | Last validation time |
| `created_at` | TIMESTAMP | Creation time |
| `updated_at` | TIMESTAMP | Last update time |

### `users` table

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment ID |
| `telegram_id` | INTEGER UNIQUE | Telegram user ID |
| `username` | TEXT | Telegram username |
| `first_name` | TEXT | First name |
| `last_name` | TEXT | Last name |
| `role` | TEXT | `user`, `admin`, `super_admin` |
| `is_blocked` | INTEGER | 0 or 1 |
| `submissions_count` | INTEGER | Total submissions |
| `created_at` | TIMESTAMP | Registration time |
| `last_active_at` | TIMESTAMP | Last activity |

### `submissions` table

Records each link submission (including duplicates).

### `backups` table

Records backup metadata.

### `_migrations` table

Tracks applied migrations.

## Plugin API

Plugins can hook into these events:

```python
class Plugin(ABC):
    name: str = "base_plugin"

    async def initialize(self, context: Dict[str, Any]) -> None:
        """Called once on startup. Context contains services."""
        pass

    async def on_link_saved(self, link: Link) -> None:
        """Called when a new link is saved."""
        pass

    async def on_link_deleted(self, link_id: int) -> None:
        """Called when a link is deleted."""
        pass

    async def on_link_validated(self, link: Link, is_valid: bool) -> None:
        """Called after link validation."""
        pass

    async def shutdown(self) -> None:
        """Called on application shutdown."""
        pass
```

The `context` dict passed to `initialize()` contains:
- `link_service` - LinkService instance
- `validation_service` - ValidationService instance
- `config` - AppConfig instance
