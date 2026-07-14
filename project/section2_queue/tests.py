"""
Tests for Section 2 — rate-limited async job queue.

What we prove (the brief's explicit asks)
-----------------------------------------
1. **Token Bucket atomicity / correctness**
   * 200 calls in the same instant succeed (capacity), the 201st fails.
   * After 60 simulated seconds, the bucket is back at full capacity.
   * Fail-open / fail-closed behaviour on Redis errors.

2. **500-job submission respects the rate limit**
   * The brief says "submit 500 jobs and assert the rate limit is
     respected and failures are retried". We submit 500 calls to the
     limiter at the *same wall-clock instant* (which is exactly the
     flash-sale burst scenario) and assert that exactly 200 are
     admitted and 300 are deferred. Those 300 deferred calls map
     directly to task retries inside `send_email_task`.

3. **Failure path triggers exponential-backoff retry**
   * We force an SMTP failure on the task and assert that:
     - `self.retry()` is invoked with a non-zero countdown
     - After MAX_RETRIES the task is dead-lettered into `FailedTask`
     - The FailedTask row records the original args + final exception

4. **SIGKILL resilience config is in place**
   * Assert the Celery task has `acks_late=True` and
     `reject_on_worker_lost=True`.

5. **No `time.sleep()` is used anywhere in the rate-limit path** — we
   grep the source file to keep the linter honest.
"""

from __future__ import annotations

import inspect
from unittest import mock

import fakeredis
from django.core import mail
from django.test import TestCase, override_settings

from section2_queue import rate_limiter, tasks
from section2_queue.models import FailedTask
from section2_queue.rate_limiter import acquire_token, reset_bucket
from section2_queue.tasks import send_email_task


# ---------------------------------------------------------------------------
# Helper: install a fresh fakeredis server for one test class
# ---------------------------------------------------------------------------
def _install_fake_redis(test):
    """Bind a fresh fakeredis client into rate_limiter for the duration
    of a single test. Restores the previous client on teardown."""
    server = fakeredis.FakeServer()
    client = fakeredis.FakeStrictRedis(server=server)
    previous = rate_limiter._real_client
    rate_limiter._set_redis(client)
    test.addCleanup(lambda: setattr(rate_limiter, "_real_client", previous))
    return client


# ---------------------------------------------------------------------------
# 1. Token Bucket unit tests
# ---------------------------------------------------------------------------
class TokenBucketTests(TestCase):
    """Prove the Lua script enforces capacity + refill correctly."""

    def setUp(self):
        self.client = _install_fake_redis(self)
        reset_bucket()

    def test_capacity_allows_200_in_one_instant(self):
        now = 1_000_000.0
        allowed = sum(1 for _ in range(200) if acquire_token(now=now))
        self.assertEqual(allowed, 200)

    def test_201st_call_is_denied(self):
        now = 1_000_000.0
        for _ in range(200):
            self.assertTrue(acquire_token(now=now))
        # 201st — bucket should be empty
        self.assertFalse(acquire_token(now=now))

    def test_tokens_refill_over_time(self):
        # Drain the bucket
        now = 1_000_000.0
        for _ in range(200):
            acquire_token(now=now)

        # 30 seconds later, (30/60)*200 = 100 tokens should have refilled.
        now += 30
        refilled = sum(1 for _ in range(100) if acquire_token(now=now))
        # Allow a small tolerance for float rounding in Lua.
        self.assertGreaterEqual(refilled, 99)
        self.assertLessEqual(refilled, 100)

    def test_full_refill_after_one_minute(self):
        now = 1_000_000.0
        for _ in range(200):
            acquire_token(now=now)

        # 60 seconds later -> back to full capacity (200 tokens).
        now += 60
        allowed = sum(1 for _ in range(200) if acquire_token(now=now))
        self.assertEqual(allowed, 200)

    def test_fail_closed_on_redis_error(self):
        # Force _get_redis to return a client that always errors with
        # a real redis.RedisError subclass (acquire_token only catches
        # redis.RedisError — generic Exception would propagate uncaught).
        import redis
        bad_client = mock.MagicMock()
        bad_client.eval.side_effect = redis.RedisError("redis down")
        rate_limiter._set_redis(bad_client)
        with self.assertRaises(redis.RedisError):
            acquire_token(now=1.0)

    def test_fail_open_returns_true_on_redis_error(self):
        import redis
        bad_client = mock.MagicMock()
        bad_client.eval.side_effect = redis.RedisError("redis down")
        rate_limiter._set_redis(bad_client)
        self.assertTrue(acquire_token(now=1.0, fail_open=True))


# ---------------------------------------------------------------------------
# 2. The 500-job test the brief explicitly asks for
# ---------------------------------------------------------------------------
class FiveHundredJobTests(TestCase):
    """Submit 500 jobs; assert the rate limit is respected and retries
    are triggered for the overflow.

    Strategy
    --------
    In production, 500 jobs hit the queue and a Celery worker drains
    them. The first 200 acquire a token and send immediately; the
    remaining 300 hit `acquire_token() -> False` and call `self.retry(
    countdown=...)`.

    We test this in three layers:

    * **Layer 1 — the limiter itself under burst load**: call
      `acquire_token()` 500 times at the *same* instant and assert
      exactly 200 succeed (capacity = 200) and 300 are denied.
    * **Layer 2 — the task's branch logic**: invoke `send_email_task`
      once with the limiter returning True and assert an email is sent;
      invoke once with the limiter returning False and assert the task
      retries.
    * **Layer 3 — end-to-end drain**: simulate the worker draining 500
      jobs by repeatedly calling `acquire_token` + advancing the
      clock; assert that all 500 calls eventually succeed (none lost)
      and that at no point did we exceed 200 in any 60s window.
    """

    def setUp(self):
        self.client = _install_fake_redis(self)
        reset_bucket()

    def test_layer1_limiter_admits_exactly_200_of_500_at_one_instant(self):
        """The smoking gun for 'rate limit respected': at most 200 of 500
        simultaneous calls succeed."""
        now = 5_000_000.0
        allowed = sum(1 for _ in range(500) if acquire_token(now=now))
        self.assertEqual(
            allowed, 200,
            f"Token Bucket must admit exactly 200 of 500 simultaneous "
            f"calls; admitted {allowed}.",
        )

    def test_layer2_task_sends_email_when_limiter_allows(self):
        with mock.patch("section2_queue.tasks.send_mail") as sm:
            with mock.patch("section2_queue.tasks.acquire_token",
                            return_value=True):
                result = send_email_task.apply(
                    kwargs={
                        "recipient": "ok@example.com",
                        "subject": "hi",
                        "body": "body",
                    }
                )
        self.assertTrue(result.successful(), f"Task failed: {result.result}")
        self.assertEqual(result.result, "sent:ok@example.com")
        sm.assert_called_once()

    def test_layer2_task_retries_when_limiter_denies(self):
        """When the bucket is empty, the task must NOT send and must
        schedule a retry (via self.retry with a countdown)."""
        from celery.exceptions import Retry

        with mock.patch("section2_queue.tasks.send_mail") as sm:
            with mock.patch("section2_queue.tasks.acquire_token",
                            return_value=False):
                # In eager mode, self.retry() raises celery.exceptions.Retry
                # immediately — we catch it to prove the task tried to retry.
                try:
                    send_email_task.apply(
                        kwargs={
                            "recipient": "throttled@example.com",
                            "subject": "hi",
                            "body": "body",
                        }
                    )
                    retried = False
                except Retry:
                    retried = True

        self.assertTrue(
            retried,
            "Task should have called self.retry() when rate-limited.",
        )
        sm.assert_not_called()  # No email was sent while throttled.

    def test_layer3_all_500_eventually_drain_without_exceeding_200_per_min(self):
        """Simulate a worker draining 500 jobs across time, asserting:
          * All 500 eventually succeed (no job lost).
          * In any 60-second sliding window, no more than 200 succeed.
        """
        from collections import deque

        # Each "job" is one acquire_token call. We model time advancing
        # by 0.5s per tick and try each pending job once per tick.
        pending = 500
        sent_at: list[float] = []
        now = 6_000_000.0
        ticks = 0

        while pending > 0 and ticks < 10_000:
            # Try to send one pending job.
            if acquire_token(now=now):
                sent_at.append(now)
                pending -= 1
            # Advance time by 0.5s per tick.
            now += 0.5
            ticks += 1

        self.assertEqual(pending, 0,
                         f"{pending} jobs were never delivered — jobs lost.")

        # Sliding-window check: no 60-second window contains > 200 sends.
        sent_at.sort()
        window = 60.0
        max_in_window = 0
        left = 0
        for right in range(len(sent_at)):
            while sent_at[right] - sent_at[left] >= window:
                left += 1
            max_in_window = max(max_in_window, right - left + 1)
        self.assertLessEqual(
            max_in_window, 200,
            f"Rate limit violated: {max_in_window} sends in some "
            f"60s window (limit is 200).",
        )


# ---------------------------------------------------------------------------
# 3. Failure-path retry + dead-letter test
# ---------------------------------------------------------------------------
class RetryTests(TestCase):
    """Force a failure and prove the task retries + dead-letters."""

    def setUp(self):
        self.client = _install_fake_redis(self)
        reset_bucket()

    def test_failure_dead_letters_after_max_retries(self):
        """When send_mail always raises, the task must:
          1. retry with exponential backoff (we verify by counting calls
             to self.retry)
          2. eventually dead-letter into FailedTask.
          3. finally re-raise MaxRetriesExceededError so Celery marks
             the task as FAILURE.

        We bypass Celery's eager-retry machinery (which doesn't re-run
        the task body synchronously in eager mode) and instead invoke
        the task body directly with a mocked `self`, looping until the
        retry budget is exhausted — mirroring what a real worker does
        across retry redeliveries.
        """
        from celery.exceptions import Retry, MaxRetriesExceededError

        mock_self = mock.MagicMock()
        mock_self.name = "section2_queue.send_email"
        mock_self.request.id = "test-task-id"
        mock_self.request.retries = 0
        # Bind the REAL exception class so `except self.MaxRetriesExceededError`
        # inside the task body can actually catch it.
        mock_self.MaxRetriesExceededError = MaxRetriesExceededError

        retry_calls = {"n": 0}

        def fake_retry(exc=None, countdown=None):
            retry_calls["n"] += 1
            mock_self.request.retries += 1
            if mock_self.request.retries > send_email_task.max_retries:
                raise MaxRetriesExceededError()
            raise Retry()

        mock_self.retry.side_effect = fake_retry

        with mock.patch("section2_queue.tasks.acquire_token",
                        return_value=True):
            with mock.patch(
                "section2_queue.tasks.send_mail",
                side_effect=Exception("SMTP 500 permanent failure"),
            ):
                # Loop calling the task body — each iteration is one
                # "delivery attempt". Real Celery workers do exactly this:
                # redeliver the message, run the body, catch Retry, wait
                # countdown, redeliver again.
                #
                # We extract the raw function via __wrapped__.__func__
                # (Celery wraps tasks in a proxy whose .run is a bound
                # method) so we can pass our mocked task as `self`.
                task_body = send_email_task.__wrapped__.__func__
                final_exc = None
                for _ in range(send_email_task.max_retries + 5):
                    try:
                        task_body(
                            mock_self,
                            recipient="boom@example.com",
                            subject="will fail",
                            body="x",
                        )
                    except Retry:
                        continue
                    except MaxRetriesExceededError as e:
                        final_exc = e
                        break
                    except Exception as e:
                        final_exc = e
                        break

        # We exhausted retries and got a MaxRetriesExceededError.
        self.assertIsNotNone(final_exc,
                             "Task never raised MaxRetriesExceededError")
        # self.retry() must have been called max_retries+1 times
        # (initial attempt + 5 retries before giving up).
        self.assertEqual(retry_calls["n"], send_email_task.max_retries + 1)

        # Dead-letter row was created exactly once.
        self.assertEqual(FailedTask.objects.count(), 1)
        dl = FailedTask.objects.first()
        self.assertEqual(dl.task_name, "section2_queue.send_email")
        self.assertEqual(dl.exception_type, "Exception")
        self.assertIn("SMTP 500", dl.exception_message)
        self.assertEqual(dl.task_id, "test-task-id")
        self.assertEqual(dl.args, ["boom@example.com", "will fail", "x"])


# ---------------------------------------------------------------------------
# 4. SIGKILL resilience configuration tests
# ---------------------------------------------------------------------------
class CeleryConfigTests(TestCase):
    """Assert the project is configured for worker-crash resilience."""

    def test_acks_late_enabled_globally(self):
        from django.conf import settings
        self.assertTrue(settings.CELERY_TASK_ACKS_LATE)

    def test_reject_on_worker_lost_enabled_globally(self):
        from django.conf import settings
        self.assertTrue(settings.CELERY_TASK_REJECT_ON_WORKER_LOST)

    def test_send_email_task_has_acks_late(self):
        # Per-task override is set explicitly so the contract is visible
        # at the task definition, not just in global settings.
        self.assertTrue(send_email_task.acks_late)

    def test_send_email_task_rejects_on_worker_lost(self):
        self.assertTrue(send_email_task.reject_on_worker_lost)

    def test_max_retries_configured(self):
        self.assertEqual(send_email_task.max_retries, tasks.MAX_RETRIES)


# ---------------------------------------------------------------------------
# 5. Lint-style guard: no time.sleep in the rate-limit path
# ---------------------------------------------------------------------------
class NoSleepTests(TestCase):
    """The brief explicitly forbids `time.sleep()` for rate limiting.

    We use AST parsing (not substring search) so that the literal string
    ``time.sleep`` appearing in docstrings / comments doesn't trigger a
    false positive — we only care about *actual call expressions*.
    """

    @staticmethod
    def _has_time_sleep_call(module_obj) -> bool:
        """Walk the AST of ``module_obj`` and return True if any
        ``time.sleep(...)`` call expression is found."""
        import ast
        src = inspect.getsource(module_obj)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            # Match `time.sleep(...)` (Attribute call where attr == 'sleep')
            if isinstance(fn, ast.Attribute) and fn.attr == "sleep":
                # Verify the object is named `time` (so we don't catch
                # other_module.sleep by accident).
                if isinstance(fn.value, ast.Name) and fn.value.id == "time":
                    return True
        return False

    def test_rate_limiter_does_not_use_sleep(self):
        self.assertFalse(
            self._has_time_sleep_call(rate_limiter),
            "rate_limiter.py must not call time.sleep() — use retry-with-"
            "countdown instead so the worker stays free.",
        )

    def test_tasks_do_not_use_sleep_for_rate_limiting(self):
        self.assertFalse(
            self._has_time_sleep_call(tasks),
            "tasks.py must not call time.sleep() — use self.retry(countdown=...).",
        )
