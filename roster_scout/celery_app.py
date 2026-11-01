"""Celery broker configuration; PostgreSQL remains application state."""

import os

from celery import Celery


celery_app = Celery(
    "roster_scout",
    broker=os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    include=("roster_scout.tasks",),
)
celery_app.conf.update(
    accept_content=("json",),
    task_serializer="json",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    result_backend=None,
)
