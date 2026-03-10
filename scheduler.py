"""
scheduler.py

Configures APScheduler to run the digest pipeline twice daily.
"""

import logging
from typing import Callable

import pytz
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from config import SCHEDULE_TIMES, TIMEZONE

logger = logging.getLogger(__name__)


def _parse_schedule_times(times_str: str) -> list[tuple[int, int]]:
    """Parse "08:00,18:00" into [(8, 0), (18, 0)]."""
    result = []
    for entry in times_str.split(","):
        hour_str, minute_str = entry.strip().split(":")
        result.append((int(hour_str), int(minute_str)))
    return result


def _job_listener(event) -> None:
    if event.exception:
        logger.error("Scheduled job '%s' failed: %s", event.job_id, event.exception)
    else:
        logger.info("Scheduled job '%s' completed successfully.", event.job_id)


def create_scheduler(job_function: Callable) -> BlockingScheduler:
    """
    Build and return a BlockingScheduler with one CronTrigger per schedule time.

    Args:
        job_function: Callable to invoke at each scheduled time.

    Returns:
        Configured (but not yet started) BlockingScheduler.
    """
    tz = pytz.timezone(TIMEZONE)
    scheduler = BlockingScheduler(timezone=tz)
    scheduler.add_listener(_job_listener, EVENT_JOB_ERROR | EVENT_JOB_EXECUTED)

    times = _parse_schedule_times(SCHEDULE_TIMES)
    for i, (hour, minute) in enumerate(times):
        scheduler.add_job(
            job_function,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=tz),
            id=f"digest_job_{i}",
            name=f"AI Digest {hour:02d}:{minute:02d}",
            max_instances=1,         # prevent overlapping runs
            replace_existing=True,
            misfire_grace_time=300,  # fire up to 5 min late (handles laptop sleep)
        )
        logger.info(
            "Scheduled digest job at %02d:%02d %s.", hour, minute, TIMEZONE
        )

    return scheduler
