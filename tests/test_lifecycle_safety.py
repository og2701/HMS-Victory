"""Focused regression tests for reconnect scheduling and startup DB recovery."""

import importlib.util
import io
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from lib.bot import backup_manager


def _module(name, **attributes):
    module = types.ModuleType(name)
    module.__dict__.update(attributes)
    return module


class _Trigger:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


def _load_scheduled_tasks_for_test():
    """Load the real module while replacing optional runtime-only dependencies."""
    noop = lambda *args, **kwargs: None
    stubs = {
        "pytz": _module("pytz", timezone=noop),
        "apscheduler": _module("apscheduler"),
        "apscheduler.triggers": _module("apscheduler.triggers"),
        "apscheduler.triggers.cron": _module(
            "apscheduler.triggers.cron", CronTrigger=_Trigger
        ),
        "apscheduler.triggers.interval": _module(
            "apscheduler.triggers.interval", IntervalTrigger=_Trigger
        ),
        "lib.features.summary": _module(
            "lib.features.summary",
            initialize_summary_data=noop,
            update_summary_data=noop,
            post_summary=noop,
        ),
        "lib.economy.economy_manager": _module(
            "lib.economy.economy_manager",
            add_bb=noop,
            get_bb=noop,
            get_all_balances=noop,
        ),
        "lib.economy.bank_manager": _module(
            "lib.economy.bank_manager", BankManager=object
        ),
        "lib.economy.economy_stats_html": _module(
            "lib.economy.economy_stats_html", create_economy_stats_image=noop
        ),
        "lib.core.file_operations": _module(
            "lib.core.file_operations",
            load_webhook_deletions=noop,
            save_webhook_deletions=noop,
            atomic_write_json=noop,
        ),
        "lib.economy.prediction_system": _module(
            "lib.economy.prediction_system",
            _save=noop,
            _load=noop,
            Prediction=object,
        ),
        "commands.moderation.overnight_mute": _module(
            "commands.moderation.overnight_mute",
            mute_visitors=noop,
            unmute_visitors=noop,
        ),
    }
    module_path = Path(__file__).parents[1] / "lib" / "bot" / "scheduled_tasks.py"
    spec = importlib.util.spec_from_file_location(
        "_lifecycle_scheduled_tasks", module_path
    )
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


class SchedulerLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scheduled_tasks = _load_scheduled_tasks_for_test()

    def test_repeated_registration_and_start_are_no_ops(self):
        class Client:
            _scheduler_jobs_registered = False

        class Scheduler:
            running = False
            start_calls = 0

            def start(self):
                self.start_calls += 1
                self.running = True

        client = Client()
        scheduler = Scheduler()
        registrations = []
        original_register = self.scheduled_tasks._register_client_jobs
        self.scheduled_tasks._register_client_jobs = (
            lambda passed_client, passed_scheduler: registrations.append(
                (passed_client, passed_scheduler)
            )
        )
        try:
            self.scheduled_tasks.schedule_client_jobs(client, scheduler)
            self.scheduled_tasks.schedule_client_jobs(client, scheduler)
        finally:
            self.scheduled_tasks._register_client_jobs = original_register

        self.assertEqual(registrations, [(client, scheduler)])
        self.assertEqual(scheduler.start_calls, 1)
        self.assertTrue(client._scheduler_jobs_registered)

    def test_start_failure_retries_without_registering_jobs_again(self):
        class Client:
            _scheduler_jobs_registered = False

        class Scheduler:
            running = False

            def __init__(self):
                self.start_calls = 0

            def start(self):
                self.start_calls += 1
                if self.start_calls == 1:
                    raise RuntimeError("start failed")
                self.running = True

        client = Client()
        scheduler = Scheduler()
        registrations = []
        original_register = self.scheduled_tasks._register_client_jobs
        self.scheduled_tasks._register_client_jobs = (
            lambda passed_client, passed_scheduler: registrations.append(
                (passed_client, passed_scheduler)
            )
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "start failed"):
                self.scheduled_tasks.schedule_client_jobs(client, scheduler)
            self.scheduled_tasks.schedule_client_jobs(client, scheduler)
        finally:
            self.scheduled_tasks._register_client_jobs = original_register

        self.assertEqual(registrations, [(client, scheduler)])
        self.assertEqual(scheduler.start_calls, 2)

    def test_partial_registration_retry_replaces_stable_job_ids(self):
        class Client:
            _scheduler_jobs_registered = False

            def clear_image_cache(self):
                return None

            def get_guild(self, guild_id):
                return guild_id

        class Scheduler:
            running = False

            def __init__(self):
                self.add_calls = []
                self.jobs = {}
                self.failed_once = False
                self.start_calls = 0

            def add_job(self, function, *args, **kwargs):
                job_id = kwargs.get("id")
                if not job_id or kwargs.get("replace_existing") is not True:
                    raise AssertionError("process job lacks stable replacement semantics")
                self.add_calls.append(job_id)
                if not self.failed_once and len(self.add_calls) == 5:
                    self.failed_once = True
                    raise RuntimeError("partial registration failure")
                self.jobs[job_id] = (function, args, kwargs)

            def start(self):
                self.start_calls += 1
                self.running = True

        client = Client()
        scheduler = Scheduler()
        original_pending = self.scheduled_tasks._register_pending_scheduled_predictions
        self.scheduled_tasks._register_pending_scheduled_predictions = (
            lambda passed_client, passed_scheduler: None
        )
        image_module = _module(
            "lib.core.image_processing", maintain_render_engine=lambda: None
        )
        try:
            with mock.patch.dict(
                sys.modules, {"lib.core.image_processing": image_module}
            ):
                with self.assertRaisesRegex(RuntimeError, "partial registration"):
                    self.scheduled_tasks.schedule_client_jobs(client, scheduler)
                first_pass_ids = set(scheduler.jobs)
                self.scheduled_tasks.schedule_client_jobs(client, scheduler)
        finally:
            self.scheduled_tasks._register_pending_scheduled_predictions = original_pending

        self.assertTrue(first_pass_ids)
        self.assertTrue(first_pass_ids.issubset(scheduler.jobs))
        self.assertGreater(len(scheduler.add_calls), len(scheduler.jobs))
        self.assertTrue(client._scheduler_jobs_registered)
        self.assertEqual(scheduler.start_calls, 1)


class _Attachment:
    def __init__(self, filename, payload):
        self.filename = filename
        self.payload = payload
        self.saved_paths = []

    async def save(self, destination):
        path = Path(destination)
        self.saved_paths.append(path)
        path.write_bytes(self.payload)


class _Message:
    def __init__(self, attachments=()):
        self.attachments = list(attachments)


class _Channel:
    def __init__(self, messages=()):
        self.messages = list(messages)
        self.requested_limit = None

    def history(self, *, limit):
        self.requested_limit = limit

        async def messages():
            for message in self.messages:
                yield message

        return messages()


class _RecoveryClient:
    def __init__(self, channel):
        self.channel = channel
        self.logged_in_with = None
        self.closed = False

    async def login(self, token):
        self.logged_in_with = token

    async def fetch_channel(self, channel_id):
        self.fetched_channel_id = channel_id
        return self.channel

    async def close(self):
        self.closed = True


def _valid_database_bytes(directory, *, include_all_tables=True):
    path = Path(directory) / "source.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE ukpence (user_id TEXT PRIMARY KEY, balance INTEGER NOT NULL)"
        )
        connection.execute("INSERT INTO ukpence VALUES ('sailor', 42)")
        if include_all_tables:
            connection.execute(
                "CREATE TABLE xp (user_id TEXT PRIMARY KEY, xp INTEGER NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE bank (id INTEGER PRIMARY KEY, balance INTEGER NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE economy_transactions "
                "(id INTEGER PRIMARY KEY, timestamp INTEGER, log_text TEXT)"
            )
    payload = path.read_bytes()
    path.unlink()
    return payload


def _zip_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries:
            archive.writestr(name, payload)
    return buffer.getvalue()


class DatabaseRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._logger_was_disabled = backup_manager.logger.disabled
        backup_manager.logger.disabled = True

    def tearDown(self):
        backup_manager.logger.disabled = self._logger_was_disabled

    def _environment(self, *, token="test-token", allow_empty=""):
        return mock.patch.dict(
            os.environ,
            {
                "DISCORD_TOKEN": token,
                backup_manager.ALLOW_EMPTY_DB_BOOTSTRAP_ENV: allow_empty,
            },
        )

    def _assert_no_recovery_temps(self, directory, database_name="database.db"):
        self.assertEqual(
            list(Path(directory).glob(f".{database_name}.restore-*")),
            [],
        )

    async def test_valid_direct_database_is_validated_and_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "database.db"
            attachment = _Attachment(
                "database_backup_2026-07-14.db",
                _valid_database_bytes(directory),
            )
            channel = _Channel([_Message([attachment])])
            client = _RecoveryClient(channel)

            with self._environment():
                restored = await backup_manager.restore_database_if_missing(
                    destination, client_factory=lambda: client
                )

            self.assertTrue(restored)
            self.assertTrue(client.closed)
            self.assertEqual(channel.requested_limit, 100)
            with sqlite3.connect(destination) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT balance FROM ukpence WHERE user_id = 'sailor'"
                    ).fetchone(),
                    (42,),
                )
            self._assert_no_recovery_temps(directory)

    async def test_valid_zip_database_is_validated_and_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "database.db"
            payload = _valid_database_bytes(directory)
            attachment = _Attachment(
                "database_backup_2026-07-14.zip",
                _zip_bytes([("database.db", payload)]),
            )
            client = _RecoveryClient(_Channel([_Message([attachment])]))

            with self._environment():
                restored = await backup_manager.restore_database_if_missing(
                    destination, client_factory=lambda: client
                )

            self.assertTrue(restored)
            self.assertTrue(destination.is_file())
            self.assertTrue(client.closed)
            self._assert_no_recovery_temps(directory)

    async def test_corrupt_database_is_rejected_without_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "database.db"
            attachment = _Attachment(
                "database_backup_2026-07-14.db", b"not a sqlite database"
            )
            client = _RecoveryClient(_Channel([_Message([attachment])]))

            with self._environment():
                with self.assertRaises(backup_manager.DatabaseRecoveryError):
                    await backup_manager.restore_database_if_missing(
                        destination, client_factory=lambda: client
                    )

            self.assertFalse(destination.exists())
            self.assertTrue(client.closed)
            self._assert_no_recovery_temps(directory)

    async def test_oversized_direct_database_is_rejected_before_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "database.db"
            attachment = _Attachment(
                "database_backup_2026-07-14.db",
                _valid_database_bytes(directory),
            )
            client = _RecoveryClient(_Channel([_Message([attachment])]))

            with self._environment(), mock.patch.object(
                backup_manager, "MAX_DATABASE_BACKUP_BYTES", 16
            ):
                with self.assertRaisesRegex(
                    backup_manager.DatabaseRecoveryError, "exceeds the supported"
                ):
                    await backup_manager.restore_database_if_missing(
                        destination, client_factory=lambda: client
                    )

            self.assertFalse(destination.exists())
            self.assertTrue(client.closed)
            self._assert_no_recovery_temps(directory)

    async def test_database_missing_essential_tables_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "database.db"
            attachment = _Attachment(
                "database_backup_2026-07-14.db",
                _valid_database_bytes(directory, include_all_tables=False),
            )
            client = _RecoveryClient(_Channel([_Message([attachment])]))

            with self._environment():
                with self.assertRaisesRegex(
                    backup_manager.DatabaseRecoveryError, "missing essential tables"
                ):
                    await backup_manager.restore_database_if_missing(
                        destination, client_factory=lambda: client
                    )

            self.assertFalse(destination.exists())
            self.assertTrue(client.closed)
            self._assert_no_recovery_temps(directory)

    async def test_unsafe_zip_member_is_rejected_and_never_extracted(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "database.db"
            outside = Path(directory).parent / "escaped-by-backup.txt"
            outside.unlink(missing_ok=True)
            payload = _valid_database_bytes(directory)
            attachment = _Attachment(
                "database_backup_2026-07-14.zip",
                _zip_bytes(
                    [("database.db", payload), ("../escaped-by-backup.txt", b"unsafe")]
                ),
            )
            client = _RecoveryClient(_Channel([_Message([attachment])]))

            try:
                with self._environment():
                    with self.assertRaisesRegex(
                        backup_manager.DatabaseRecoveryError, "Unsafe path"
                    ):
                        await backup_manager.restore_database_if_missing(
                            destination, client_factory=lambda: client
                        )
                self.assertFalse(outside.exists())
            finally:
                outside.unlink(missing_ok=True)

            self.assertFalse(destination.exists())
            self.assertTrue(client.closed)
            self._assert_no_recovery_temps(directory)

    async def test_oversized_zip_member_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "database.db"
            payload = _valid_database_bytes(directory)
            attachment = _Attachment(
                "database_backup_2026-07-14.zip",
                _zip_bytes([("database.db", payload)]),
            )
            client = _RecoveryClient(_Channel([_Message([attachment])]))

            with self._environment(), mock.patch.object(
                backup_manager, "MAX_DATABASE_BACKUP_BYTES", 64
            ):
                with self.assertRaisesRegex(
                    backup_manager.DatabaseRecoveryError, "expands beyond"
                ):
                    await backup_manager.restore_database_if_missing(
                        destination, client_factory=lambda: client
                    )

            self.assertFalse(destination.exists())
            self.assertTrue(client.closed)
            self._assert_no_recovery_temps(directory)

    async def test_missing_remote_backup_raises_instead_of_creating_database(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "database.db"
            client = _RecoveryClient(_Channel([_Message()]))

            with self._environment():
                with self.assertRaisesRegex(
                    backup_manager.DatabaseRecoveryError, "No database backup"
                ):
                    await backup_manager.restore_database_if_missing(
                        destination, client_factory=lambda: client
                    )

            self.assertFalse(destination.exists())
            self.assertTrue(client.closed)
            self._assert_no_recovery_temps(directory)

    async def test_missing_token_raises_without_constructing_network_client(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "database.db"
            factory = mock.Mock(side_effect=AssertionError("network client constructed"))

            with self._environment(token=""):
                with self.assertRaisesRegex(
                    backup_manager.DatabaseRecoveryError, "DISCORD_TOKEN"
                ):
                    await backup_manager.restore_database_if_missing(
                        destination, client_factory=factory
                    )

            factory.assert_not_called()
            self.assertFalse(destination.exists())

    async def test_explicit_first_install_flag_allows_caller_to_bootstrap(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "database.db"
            factory = mock.Mock(side_effect=AssertionError("network client constructed"))

            with self._environment(token="", allow_empty="true"):
                restored = await backup_manager.restore_database_if_missing(
                    destination, client_factory=factory
                )

            self.assertFalse(restored)
            factory.assert_not_called()
            self.assertFalse(destination.exists())

    async def test_existing_database_is_a_no_op_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "database.db"
            payload = _valid_database_bytes(directory)
            destination.write_bytes(payload)
            factory = mock.Mock(side_effect=AssertionError("network client constructed"))

            with self._environment(token=""):
                restored = await backup_manager.restore_database_if_missing(
                    destination, client_factory=factory
                )

            self.assertFalse(restored)
            factory.assert_not_called()
            self.assertEqual(destination.read_bytes(), payload)

    async def test_existing_invalid_database_fails_even_when_bootstrap_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "database.db"
            destination.write_bytes(b"")
            factory = mock.Mock(side_effect=AssertionError("network client constructed"))

            with self._environment(token="", allow_empty="true"):
                with self.assertRaisesRegex(
                    backup_manager.DatabaseRecoveryError, "empty or missing"
                ):
                    await backup_manager.restore_database_if_missing(
                        destination, client_factory=factory
                    )

            factory.assert_not_called()
            self.assertTrue(destination.exists())
            self.assertEqual(destination.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
