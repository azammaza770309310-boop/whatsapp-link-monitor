"""Link validation background job."""
from __future__ import annotations

from src.application.services.validation_service import ValidationService
from src.core.logging import get_logger

logger = get_logger(__name__)


class LinkValidatorJob:
    """Periodic job to validate stored links."""

    def __init__(
        self,
        validation_service: ValidationService,
        batch_size: int = 50,
    ) -> None:
        self._validation_service = validation_service
        self._batch_size = batch_size

    async def run(self) -> None:
        """Run one validation cycle."""
        try:
            result = await self._validation_service.validate_expired(
                limit=self._batch_size
            )
            logger.info(
                "Link validation job result",
                extra={"extra_data": result},
            )
        except Exception as e:
            logger.error(f"Link validation job error: {e}", exc_info=True)
