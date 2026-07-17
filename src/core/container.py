"""Dependency injection container."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Type, TypeVar

T = TypeVar("T")


class Container:
    """Simple dependency injection container."""

    def __init__(self) -> None:
        self._services: Dict[str, Any] = {}
        self._factories: Dict[str, Any] = {}

    def register(self, interface: Type[T], implementation: T) -> None:
        """Register a singleton instance."""
        key = interface.__name__
        self._services[key] = implementation

    def register_factory(self, interface: Type[T], factory: Any) -> None:
        """Register a factory function that creates instances."""
        key = interface.__name__
        self._factories[key] = factory

    def resolve(self, interface: Type[T]) -> T:
        """Resolve a registered service."""
        key = interface.__name__
        if key in self._services:
            return self._services[key]
        if key in self._factories:
            instance = self._factories[key]()
            self._services[key] = instance
            return instance
        raise KeyError(f"Service not registered: {key}")

    def has(self, interface: Type[T]) -> bool:
        """Check if a service is registered."""
        key = interface.__name__
        return key in self._services or key in self._factories

    def clear(self) -> None:
        """Clear all registrations."""
        self._services.clear()
        self._factories.clear()


# Global container instance
container = Container()
