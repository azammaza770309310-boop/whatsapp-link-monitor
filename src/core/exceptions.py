"""Custom exception hierarchy."""
from __future__ import annotations


class BotError(Exception):
    """Base exception for all bot errors."""


class ConfigurationError(BotError):
    """Configuration is invalid."""


class DatabaseError(BotError):
    """Database operation failed."""


class MigrationError(BotError):
    """Database migration failed."""


class TelegramError(BotError):
    """Telegram API operation failed."""


class ValidationError(BotError):
    """Input validation failed."""


class LinkValidationError(BotError):
    """WhatsApp link validation failed."""
    def __init__(self, link: str, reason: str):
        self.link = link
        self.reason = reason
        super().__init__(f"Link validation failed: {link} ({reason})")


class RateLimitExceededError(BotError):
    """User has exceeded rate limit."""
    def __init__(self, user_id: int, limit: int):
        self.user_id = user_id
        self.limit = limit
        super().__init__(f"Rate limit exceeded for user {user_id} (limit: {limit}/min)")


class AuthorizationError(BotError):
    """User is not authorized to perform this action."""


class PluginError(BotError):
    """Plugin operation failed."""


class BackupError(BotError):
    """Backup operation failed."""
