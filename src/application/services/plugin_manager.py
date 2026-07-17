"""Plugin architecture for extensible functionality."""
from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from src.core.exceptions import PluginError
from src.core.logging import get_logger
from src.domain.entities import Link

logger = get_logger(__name__)


class Plugin(ABC):
    """Base class for plugins."""

    name: str = "base_plugin"

    async def initialize(self, context: Dict[str, Any]) -> None:
        """Initialize the plugin with application context."""
        pass

    async def on_link_saved(self, link: Link) -> None:
        """Called when a new link is saved."""
        pass

    async def on_link_deleted(self, link_id: int) -> None:
        """Called when a link is deleted."""
        pass

    async def on_link_validated(self, link: Link, is_valid: bool) -> None:
        """Called when a link is validated."""
        pass

    async def shutdown(self) -> None:
        """Cleanup when shutting down."""
        pass


class PluginManager:
    """Manages plugin lifecycle."""

    def __init__(self) -> None:
        self._plugins: List[Plugin] = []

    @property
    def plugins(self) -> List[Plugin]:
        return list(self._plugins)

    def load_plugin(self, plugin: Plugin) -> None:
        """Load a plugin instance."""
        self._plugins.append(plugin)
        logger.info(f"Plugin loaded: {plugin.name}")

    def load_from_config(self, plugin_paths: List[str]) -> None:
        """Load plugins from configuration."""
        for path in plugin_paths:
            try:
                module_path, class_name = path.rsplit(".", 1)
                module = importlib.import_module(module_path)
                plugin_class = getattr(module, class_name)
                plugin = plugin_class()
                if not isinstance(plugin, Plugin):
                    raise PluginError(
                        f"{path} is not a Plugin subclass"
                    )
                self.load_plugin(plugin)
            except Exception as e:
                logger.error(f"Failed to load plugin {path}: {e}")
                raise PluginError(f"Failed to load plugin {path}: {e}") from e

    async def initialize_all(self, context: Dict[str, Any]) -> None:
        """Initialize all plugins."""
        for plugin in self._plugins:
            try:
                await plugin.initialize(context)
            except Exception as e:
                logger.error(f"Plugin {plugin.name} initialization failed: {e}")

    async def notify_link_saved(self, link: Link) -> None:
        """Notify all plugins of a saved link."""
        for plugin in self._plugins:
            try:
                await plugin.on_link_saved(link)
            except Exception as e:
                logger.error(f"Plugin {plugin.name} on_link_saved failed: {e}")

    async def notify_link_deleted(self, link_id: int) -> None:
        """Notify all plugins of a deleted link."""
        for plugin in self._plugins:
            try:
                await plugin.on_link_deleted(link_id)
            except Exception as e:
                logger.error(f"Plugin {plugin.name} on_link_deleted failed: {e}")

    async def notify_link_validated(self, link: Link, is_valid: bool) -> None:
        """Notify all plugins of a validated link."""
        for plugin in self._plugins:
            try:
                await plugin.on_link_validated(link, is_valid)
            except Exception as e:
                logger.error(f"Plugin {plugin.name} on_link_validated failed: {e}")

    async def shutdown_all(self) -> None:
        """Shutdown all plugins."""
        for plugin in self._plugins:
            try:
                await plugin.shutdown()
            except Exception as e:
                logger.error(f"Plugin {plugin.name} shutdown failed: {e}")
