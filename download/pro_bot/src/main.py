"""Application entry point."""
from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

from src.application.jobs.backup_job import BackupJob
from src.application.jobs.link_validator_job import LinkValidatorJob
from src.application.jobs.scheduler import Scheduler
from src.application.services.backup_service import BackupService
from src.application.services.health_service import HealthService
from src.application.services.link_service import LinkService
from src.application.services.plugin_manager import PluginManager
from src.application.services.rate_limiter import RateLimiter
from src.application.services.validation_service import ValidationService
from src.core.config import load_config, validate_config
from src.core.container import container
from src.core.exceptions import ConfigurationError
from src.core.logging import get_logger, setup_logging
from src.infrastructure.database.connection import Database
from src.infrastructure.database.migrations import MigrationRunner
from src.infrastructure.database.repositories.link_repository import SqliteLinkRepository
from src.infrastructure.database.repositories.submission_backup_repository import (
    SqliteBackupRepository,
    SqliteSubmissionRepository,
)
from src.infrastructure.database.repositories.user_repository import SqliteUserRepository
from src.infrastructure.http.server import HttpServer, start_keep_alive
from src.infrastructure.telegram.bot_client import BotClient
from src.presentation.bot.orchestrator import BotOrchestrator

logger = get_logger(__name__)


async def main() -> None:
    """Application main entry point."""
    # Load configuration
    config = load_config()
    errors = validate_config(config)
    if errors:
        for e in errors:
            print(f"❌ {e}", file=sys.stderr)
        raise ConfigurationError("Invalid configuration")

    # Setup logging
    setup_logging(config.logging.level, config.logging.format)
    logger.info(
        "Starting WhatsApp Link Monitor",
        extra={
            "extra_data": {
                "log_level": config.logging.level,
                "channel_id": config.channel_id,
                "admin_count": len(config.admin_ids),
            }
        },
    )

    # Ensure directories
    Path(config.database.path).parent.mkdir(parents=True, exist_ok=True)
    Path(config.telegram.session_dir).mkdir(parents=True, exist_ok=True)

    # Initialize database
    database = Database(config.database)
    await database.connect()

    # Run migrations
    migration_runner = MigrationRunner(database)
    applied = await migration_runner.run()
    logger.info(
        "Migrations complete",
        extra={"extra_data": {"applied": applied}},
    )

    # Initialize repositories
    link_repo = SqliteLinkRepository(database)
    user_repo = SqliteUserRepository(database)
    submission_repo = SqliteSubmissionRepository(database)
    backup_repo = SqliteBackupRepository(database)

    # Initialize services
    link_service = LinkService(link_repo, user_repo, submission_repo)
    validation_service = ValidationService(link_repo)
    backup_service = BackupService(link_repo, user_repo, backup_repo)
    health_service = HealthService(link_repo, user_repo)
    rate_limiter = RateLimiter(
        config.rate_limit.per_minute,
        config.rate_limit.burst,
    )

    # Initialize plugin manager
    plugin_manager = PluginManager()
    if config.plugins:
        try:
            plugin_manager.load_from_config(config.plugins)
            await plugin_manager.initialize_all(
                {
                    "link_service": link_service,
                    "validation_service": validation_service,
                    "config": config,
                }
            )
        except Exception as e:
            logger.error(f"Plugin initialization failed: {e}")

    # Initialize bot
    bot_client = BotClient(config.telegram)
    await bot_client.start()

    # Initialize orchestrator
    orchestrator = BotOrchestrator(
        bot_client=bot_client,
        link_service=link_service,
        user_repo=user_repo,
        validation_service=validation_service,
        backup_service=backup_service,
        rate_limiter=rate_limiter,
        admin_ids=config.admin_ids,
        channel_id=config.channel_id,
    )
    orchestrator.register_handlers()

    # Initialize scheduler
    scheduler = Scheduler()
    validator_job = LinkValidatorJob(validation_service)
    backup_job = BackupJob(backup_service, config.jobs.backup_retention_days)

    scheduler.add_job(
        "link_validator",
        validator_job.run,
        interval_seconds=config.jobs.validation_interval_hours * 3600,
        run_on_start=False,
    )
    scheduler.add_job(
        "backup",
        backup_job.run,
        interval_seconds=config.jobs.backup_interval_hours * 3600,
        run_on_start=False,
    )
    scheduler.start_all()

    # Start HTTP server
    http_server = HttpServer(config.http, health_data_provider=health_service.get_health)
    await http_server.start()

    # Start keep-alive (for cloud deployments)
    render_url = os.getenv("RENDER_EXTERNAL_URL")
    keep_alive_task = await start_keep_alive(render_url)

    logger.info("=== WhatsApp Link Monitor started ===")

    # Setup shutdown handler
    shutdown_event = asyncio.Event()

    def signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except (NotImplementedError, RuntimeError, ValueError):
            try:
                signal.signal(sig, lambda *_: signal_handler())
            except Exception:
                pass

    # Wait for shutdown
    await shutdown_event.wait()

    # Cleanup
    logger.info("Shutting down...")
    if keep_alive_task:
        keep_alive_task.cancel()
        try:
            await keep_alive_task
        except asyncio.CancelledError:
            pass
    await scheduler.stop_all()
    await orchestrator.stop()
    await http_server.stop()
    await validation_service.close()
    await plugin_manager.shutdown_all()
    await database.disconnect()
    logger.info("Application stopped")


def run() -> None:
    """Run the application."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except ConfigurationError as e:
        print(f"❌ Configuration error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    run()
