"""Configuration management with environment variables and YAML support."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv


@dataclass(frozen=True)
class DatabaseConfig:
    path: str = "data/links.db"
    journal_mode: str = "WAL"
    busy_timeout: int = 30000
    synchronous: str = "NORMAL"


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"
    format: str = "json"


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = ""
    api_id: int = 0
    api_hash: str = ""
    session_dir: str = "sessions"
    connection_retries: int = 5
    retry_delay: int = 5
    request_retries: int = 5
    auto_reconnect: bool = True


@dataclass(frozen=True)
class JobsConfig:
    validation_interval_hours: int = 24
    backup_interval_hours: int = 24
    backup_retention_days: int = 30


@dataclass(frozen=True)
class RateLimitConfig:
    per_minute: int = 10
    burst: int = 20


@dataclass(frozen=True)
class HttpConfig:
    host: str = "0.0.0.0"
    port: int = 10000


@dataclass(frozen=True)
class AppConfig:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    jobs: JobsConfig = field(default_factory=JobsConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    admin_ids: List[int] = field(default_factory=list)
    channel_id: int = 0
    plugins: List[str] = field(default_factory=list)


def load_config(env_path: Optional[str] = None) -> AppConfig:
    """Load configuration from environment variables."""
    if env_path and Path(env_path).exists():
        load_dotenv(env_path)
    else:
        load_dotenv()

    admin_ids_str = os.getenv("ADMIN_IDS", "")
    admin_ids = [
        int(x.strip()) for x in admin_ids_str.split(",") if x.strip().isdigit()
    ]

    plugins_str = os.getenv("PLUGINS", "")
    plugins = [x.strip() for x in plugins_str.split(",") if x.strip()]

    api_id_str = os.getenv("API_ID", "0") or "0"
    try:
        api_id = int(api_id_str)
    except ValueError:
        api_id = 0

    channel_id_str = os.getenv("CHANNEL_ID", "0") or "0"
    try:
        channel_id = int(channel_id_str)
    except ValueError:
        channel_id = 0

    return AppConfig(
        database=DatabaseConfig(
            path=os.getenv("DATABASE_PATH", "data/links.db"),
        ),
        logging=LoggingConfig(
            level=os.getenv("LOG_LEVEL", "INFO"),
            format=os.getenv("LOG_FORMAT", "json"),
        ),
        telegram=TelegramConfig(
            bot_token=os.getenv("BOT_TOKEN", ""),
            api_id=api_id,
            api_hash=os.getenv("API_HASH", ""),
        ),
        jobs=JobsConfig(
            validation_interval_hours=int(os.getenv("VALIDATION_INTERVAL_HOURS", "24")),
            backup_interval_hours=int(os.getenv("BACKUP_INTERVAL_HOURS", "24")),
            backup_retention_days=int(os.getenv("BACKUP_RETENTION_DAYS", "30")),
        ),
        rate_limit=RateLimitConfig(
            per_minute=int(os.getenv("RATE_LIMIT_PER_MINUTE", "10")),
            burst=int(os.getenv("RATE_LIMIT_BURST", "20")),
        ),
        http=HttpConfig(
            host=os.getenv("HTTP_HOST", "0.0.0.0"),
            port=int(os.getenv("HTTP_PORT", "10000")),
        ),
        admin_ids=admin_ids,
        channel_id=channel_id,
        plugins=plugins,
    )


def validate_config(config: AppConfig) -> List[str]:
    """Validate configuration. Returns list of error messages."""
    errors = []
    if not config.telegram.bot_token:
        errors.append("BOT_TOKEN is required")
    if not config.admin_ids:
        errors.append("ADMIN_IDS is required (comma-separated user IDs)")
    if not config.channel_id:
        errors.append("CHANNEL_ID is required")
    return errors
