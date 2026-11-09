"""Account-wide Browserless capacity coordination tests."""

import multiprocessing
import os
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from tests.db_support import require_test_database_url


def _capacity_contender(
    database_url,
    role,
    namespace,
    account_limit,
    cache_dir,
    hold_event,
    result_queue,
    opener_count,
):
    """Acquire one slot in an isolated process and report observable behavior."""
    environment = {
        "DATABASE_URL": database_url,
        "BROWSERLESS_CAPACITY_BACKEND": "postgresql",
        "BROWSERLESS_CAPACITY_NAMESPACE": namespace,
        "BROWSERLESS_ACCOUNT_MAX_SESSIONS": str(account_limit),
        "ROSTER_SCOUT_CACHE_DIR": cache_dir,
    }
    os.environ.update(environment)

    from usage_limits import BrowserCapacityExceeded, browserless_slot

    try:
        with browserless_slot():
            with opener_count.get_lock():
                opener_count.value += 1
            result_queue.put((role, "acquired"))
            hold_event.wait(10)
    except BrowserCapacityExceeded:
        result_queue.put((role, "capacity-exceeded"))
    except Exception as exc:  # pragma: no cover - surfaced to the parent assertion
        result_queue.put((role, "error", type(exc).__name__, str(exc)))


def _crash_capacity_holder(database_url, namespace, cache_dir, result_queue):
    """Hold a lease until the parent terminates this process."""
    os.environ.update({
        "DATABASE_URL": database_url,
        "BROWSERLESS_CAPACITY_BACKEND": "postgresql",
        "BROWSERLESS_CAPACITY_NAMESPACE": namespace,
        "BROWSERLESS_ACCOUNT_MAX_SESSIONS": "1",
        "ROSTER_SCOUT_CACHE_DIR": cache_dir,
    })
    from usage_limits import browserless_slot

    with browserless_slot():
        result_queue.put(("crashed-holder", "acquired"))
        time.sleep(30)


class DistributedBrowserlessCapacityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_url = require_test_database_url()
        cls.context = multiprocessing.get_context("spawn")

    def namespace(self):
        return f"roster-scout-test-{uuid.uuid4()}"

    def result(self, queue):
        result = queue.get(timeout=10)
        self.assertNotEqual(result[1], "error", result)
        return result

    def test_app_and_worker_share_one_account_wide_limit(self):
        release = self.context.Event()
        no_hold = self.context.Event()
        no_hold.set()
        results = self.context.Queue()
        opener_count = self.context.Value("i", 0)
        processes = [
            self.context.Process(
                target=_capacity_contender,
                args=(
                    self.database_url,
                    role,
                    self.namespace_value,
                    2,
                    os.path.join(self.temp_dir.name, role),
                    release,
                    results,
                    opener_count,
                ),
            )
            for role in ("app", "worker")
        ]
        for process in processes:
            process.start()
        acquired = {self.result(results) for _ in processes}
        self.assertEqual(acquired, {("app", "acquired"), ("worker", "acquired")})

        third = self.context.Process(
            target=_capacity_contender,
            args=(
                self.database_url,
                "third-contender",
                self.namespace_value,
                2,
                os.path.join(self.temp_dir.name, "third-contender"),
                no_hold,
                results,
                opener_count,
            ),
        )
        third.start()
        self.assertEqual(self.result(results), ("third-contender", "capacity-exceeded"))
        third.join(10)
        self.assertEqual(third.exitcode, 0)
        self.assertEqual(opener_count.value, 2, "third contender opened Browserless")

        release.set()
        for process in processes:
            process.join(10)
            self.assertEqual(process.exitcode, 0)

    def test_process_loss_releases_slot_without_cleanup(self):
        results = self.context.Queue()
        opener_count = self.context.Value("i", 0)
        holder = self.context.Process(
            target=_crash_capacity_holder,
            args=(
                self.database_url,
                self.namespace_value,
                os.path.join(self.temp_dir.name, "crash-machine"),
                results,
            ),
        )
        holder.start()
        self.assertEqual(self.result(results), ("crashed-holder", "acquired"))
        holder.terminate()
        holder.join(10)
        self.assertIsNotNone(holder.exitcode)

        replacement_release = self.context.Event()
        replacement_release.set()
        replacement = self.context.Process(
            target=_capacity_contender,
            args=(
                self.database_url,
                "replacement",
                self.namespace_value,
                1,
                os.path.join(self.temp_dir.name, "replacement-machine"),
                replacement_release,
                results,
                opener_count,
            ),
        )
        replacement.start()
        self.assertEqual(self.result(results), ("replacement", "acquired"))
        replacement.join(10)
        self.assertEqual(replacement.exitcode, 0)

    def test_postgresql_backend_failure_is_sanitized_and_fails_closed(self):
        from usage_limits import BrowserCapacityUnavailable, browserless_slot

        environment = {
            "DATABASE_URL": "postgresql+psycopg://secret@database.invalid/production",
            "BROWSERLESS_CAPACITY_BACKEND": "postgresql",
            "BROWSERLESS_CAPACITY_NAMESPACE": self.namespace_value,
            "BROWSERLESS_ACCOUNT_MAX_SESSIONS": "2",
        }
        with patch.dict(os.environ, environment, clear=False), patch(
            "roster_scout.database.get_engine",
            side_effect=RuntimeError("could not connect using secret"),
        ):
            with self.assertRaisesRegex(
                BrowserCapacityUnavailable,
                "Browserless capacity service is unavailable",
            ) as raised:
                with browserless_slot():
                    self.fail("uncoordinated local fallback opened Browserless")
        self.assertNotIn("secret", str(raised.exception))

    def test_capacity_backend_failure_returns_sanitized_http_response(self):
        import app as app_module
        from usage_limits import BrowserCapacityUnavailable

        app_module.app.config.update(TESTING=True)
        with patch.object(app_module, "check_request_limit", return_value=0), patch.object(
            app_module,
            "scrape_ncaa_by_name",
            side_effect=BrowserCapacityUnavailable(
                "Browserless capacity service is unavailable. Please try again shortly."
            ),
        ):
            response = app_module.app.test_client().post(
                "/scrape",
                json={"team": "Tampa", "division": "d1"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Retry-After"], "30")
        self.assertEqual(
            response.get_json(),
            {
                "error": (
                    "Browserless capacity service is unavailable. "
                    "Please try again shortly."
                )
            },
        )

    def test_worker_concurrency_cannot_exceed_account_capacity(self):
        from scripts.run_worker import worker_command

        with self.assertRaisesRegex(
            ValueError,
            "CELERY_WORKER_CONCURRENCY cannot exceed",
        ):
            worker_command({
                "CELERY_WORKER_CONCURRENCY": "2",
                "BROWSERLESS_ACCOUNT_MAX_SESSIONS": "1",
            })
        command = worker_command({
            "CELERY_WORKER_CONCURRENCY": "2",
            "BROWSERLESS_ACCOUNT_MAX_SESSIONS": "2",
        })
        self.assertEqual(command[-2:], ["--concurrency", "2"])

    def setUp(self):
        self.namespace_value = self.namespace()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)


if __name__ == "__main__":
    unittest.main()
