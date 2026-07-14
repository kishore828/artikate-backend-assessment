"""
Section 2 — Celery tasks for transactional email sending.

Architecture
------------
* ``send_email_task``  — the user-facing task. It first asks the Token
  Bucket rate limiter for a token; if denied, it requeues itself with
  exponential backoff (instead of `time.sleep`, which would block the
  worker). Once a token is granted, it calls the (stubbed) email
  provider.
* On permanent failure (retries exhausted) the task records itself into
  the ``FailedTask`` dead-letter table for operator inspection.

Worker-crash resilience
-----------------------
The task inherits its resilience from the project-wide Celery config
in ``config/settings.py``:
    CELERY_TASK_ACKS_LATE              = True
    CELERY_TASK_REJECT_ON_WORKER_LOST  = True

With ``acks_late``, the broker only removes the message after the task
body returns. If the worker is SIGKILL'd mid-execution, the broker
connection drops, the message stays un-acked, and Redis' visibility
timeout eventually re-delivers it to another worker. Combined with
``reject_on_worker_lost=True``, the broker explicitly requeues rather
than dropping the message.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from celery import shared_task
from django.core.mail import send_mail
from django.utils import timezone

from .models import FailedTask
from .rate_limiter import acquire_token

logger = logging.getLogger(__name__)

# Exponential backoff: 2 ** attempt * base, capped at MAX_BACKOFF.
BASE_BACKOFF_SECONDS = 2
MAX_BACKOFF_SECONDS = 600          # never wait more than 10 minutes
MAX_RETRIES = 5                    # total attempts = 1 + MAX_RETRIES


def _backoff(retries: int) -> int:
    """Compute the next retry delay (seconds), with jitter."""
    delay = min(BASE_BACKOFF_SECONDS * (2 ** retries), MAX_BACKOFF_SECONDS)
    # Jitter: ±25 % — spreads thundering herds of retried emails.
    jitter = delay * 0.25 * (random.random() * 2 - 1)
    return max(1, int(delay + jitter))


def _record_dead_letter(
    task_name: str,
    task_id: str,
    args: tuple,
    kwargs: dict,
    exc: BaseException,
    retries: int,
) -> None:
    """Persist a permanently-failed task to the dead-letter table."""
    import traceback
    FailedTask.objects.create(
        task_name=task_name,
        task_id=task_id,
        args=list(args),
        kwargs=kwargs,
        exception_type=type(exc).__name__,
        exception_message=str(exc),
        traceback="".join(traceback.format_exception(exc)),
        retries_attempted=retries,
    )
    logger.error(
        "Dead-lettered task %s (id=%s) after %d retries: %s",
        task_name, task_id, retries, exc,
    )


@shared_task(
    bind=True,
    name="section2_queue.send_email",
    autoretry_for=(Exception,),
    retry_backoff=BASE_BACKOFF_SECONDS,
    retry_backoff_max=MAX_BACKOFF_SECONDS,
    retry_jitter=True,
    max_retries=MAX_RETRIES,
    acks_late=True,            # per-task override mirroring project default
    reject_on_worker_lost=True,
)
def send_email_task(self, recipient: str, subject: str, body: str) -> str:
    """Send a transactional email, rate-limited to 200/min via Redis.

    Flow
    ----
    1. Ask the Token Bucket for a token.
       * If allowed -> send the email via Django's email backend.
       * If denied  -> retry with exponential backoff.
    2. If the email backend raises, retry with backoff.
    3. If retries are exhausted, persist to ``FailedTask`` and re-raise
       so Celery marks the task as FAILURE.
    """
    try:
        if not acquire_token(bucket="email"):
            # Bucket empty — back off instead of busy-spinning. We use
            # self.retry (NOT time.sleep) so the worker stays free to
            # process other tasks while we wait.
            raise _RateLimited()

        # Token acquired — actually send.
        send_mail(
            subject=subject,
            message=body,
            from_email=None,        # uses DEFAULT_FROM_EMAIL
            recipient_list=[recipient],
            fail_silently=False,
        )
        logger.info("Sent email to %s subject=%r", recipient, subject)
        return f"sent:{recipient}"

    except _RateLimited as exc:
        # Don't count rate-limit denials against the permanent-failure
        # budget — but DO back off so we don't hammer Redis.
        try:
            raise self.retry(exc=exc, countdown=_backoff(self.request.retries))
        except self.MaxRetriesExceededError:
            # Out of retries — record and give up.
            _record_dead_letter(
                task_name=self.name,
                task_id=self.request.id,
                args=(recipient, subject, body),
                kwargs={},
                exc=exc,
                retries=self.request.retries,
            )
            raise

    except Exception as exc:
        # Any other failure (SMTP error, network blip, etc.) — retry
        # with exponential backoff, then dead-letter if we run out.
        try:
            raise self.retry(exc=exc, countdown=_backoff(self.request.retries))
        except self.MaxRetriesExceededError:
            _record_dead_letter(
                task_name=self.name,
                task_id=self.request.id,
                args=(recipient, subject, body),
                kwargs={},
                exc=exc,
                retries=self.request.retries,
            )
            raise


class _RateLimited(Exception):
    """Internal sentinel — raised when the Token Bucket returns 0."""
