"""Job scheduler for background tasks."""
from __future__ import annotations

import asyncio
from typing import Callable, List, Optional

from src.core.logging import get_logger

logger = get_logger(__name__)


class Job:
    """A scheduled background job."""

    def __init__(
        self,
        name: str,
        func: Callable,
        interval_seconds: int,
        run_on_start: bool = False,
    ) -> None:
        self.name = name
        self.func = func
        self.interval_seconds = interval_seconds
        self.run_on_start = run_on_start
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def run(self) -> None:
        """Run the job in a loop."""
        self._running = True
        if self.run_on_start:
            await self._execute_once()

        while self._running:
            await asyncio.sleep(self.interval_seconds)
            if not self._running:
                break
            await self._execute_once()

    async def _execute_once(self) -> None:
        """Execute the job once with error handling."""
        try:
            logger.info(f"Job started: {self.name}")
            await self.func()
            logger.info(f"Job completed: {self.name}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                f"Job failed: {self.name}",
                extra={"extra_data": {"error": str(e)}},
                exc_info=True,
            )

    def start(self) -> asyncio.Task:
        """Start the job as a background task."""
        self._task = asyncio.create_task(self.run())
        return self._task

    async def stop(self) -> None:
        """Stop the job."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


class Scheduler:
    """Manages multiple background jobs."""

    def __init__(self) -> None:
        self._jobs: List[Job] = []

    def add_job(
        self,
        name: str,
        func: Callable,
        interval_seconds: int,
        run_on_start: bool = False,
    ) -> Job:
        """Add a job to the scheduler."""
        job = Job(name, func, interval_seconds, run_on_start)
        self._jobs.append(job)
        return job

    def start_all(self) -> List[asyncio.Task]:
        """Start all jobs. Returns list of tasks."""
        tasks = []
        for job in self._jobs:
            task = job.start()
            tasks.append(task)
            logger.info(
                f"Job scheduled: {job.name}",
                extra={
                    "extra_data": {
                        "interval": job.interval_seconds,
                        "run_on_start": job.run_on_start,
                    }
                },
            )
        return tasks

    async def stop_all(self) -> None:
        """Stop all jobs."""
        for job in self._jobs:
            await job.stop()
        logger.info("All jobs stopped")
