"""Focused regression tests for reconnect scheduling and startup DB recovery."""

import ast
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


def _load_source_function(relative_path, function_name):
    """Load one dependency-free function from a runtime-heavy module."""
    source_path = ROOT / relative_path
    tree = ast.parse(source_path.read_text(), filename=str(source_path))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    namespace = {}
    single_function = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(single_function)
    exec(compile(single_function, str(source_path), "exec"), namespace)
    return namespace[function_name]


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

    def test_pending_prediction_failure_keeps_full_registration_retryable(self):
        class Client:
            _scheduler_jobs_registered = False

            def clear_image_cache(self):
                return None

            def get_guild(self, guild_id):
                return guild_id

        class Scheduler:
            running = False

            def __init__(self):
                self.jobs = {}
                self.failed_prediction_once = False
                self.start_calls = 0

            def add_job(self, function, *args, **kwargs):
                job_id = kwargs.get("id")
                if not job_id or kwargs.get("replace_existing") is not True:
                    raise AssertionError("job lacks stable replacement semantics")
                if job_id == "scheduled_pred_2" and not self.failed_prediction_once:
                    self.failed_prediction_once = True
                    raise RuntimeError("pending prediction registration failed")
                self.jobs[job_id] = (function, args, kwargs)

            def start(self):
                self.start_calls += 1
                self.running = True

        class DatabaseManager:
            @staticmethod
            def fetch_all(query):
                return [(1, 1_800_000_000), (2, 1_800_000_100)]

        apscheduler = _module("apscheduler")
        apscheduler.__path__ = []
        triggers = _module("apscheduler.triggers")
        triggers.__path__ = []
        runtime_stubs = {
            "apscheduler": apscheduler,
            "apscheduler.triggers": triggers,
            "apscheduler.triggers.date": _module(
                "apscheduler.triggers.date", DateTrigger=_Trigger
            ),
            "database": _module("database", DatabaseManager=DatabaseManager),
            "lib.core.image_processing": _module(
                "lib.core.image_processing", maintain_render_engine=lambda: None
            ),
            "lib.economy.prediction_system": _module(
                "lib.economy.prediction_system",
                post_scheduled_prediction=lambda *args, **kwargs: None,
            ),
        }
        client = Client()
        scheduler = Scheduler()
        logger_was_disabled = self.scheduled_tasks.logger.disabled
        self.scheduled_tasks.logger.disabled = True
        try:
            with mock.patch.dict(sys.modules, runtime_stubs):
                with self.assertRaisesRegex(
                    RuntimeError, "pending prediction registration failed"
                ):
                    self.scheduled_tasks.schedule_client_jobs(client, scheduler)

                self.assertFalse(client._scheduler_jobs_registered)
                self.assertEqual(scheduler.start_calls, 0)
                self.assertIn("scheduled_pred_1", scheduler.jobs)
                self.assertNotIn("scheduled_pred_2", scheduler.jobs)

                self.scheduled_tasks.schedule_client_jobs(client, scheduler)
        finally:
            self.scheduled_tasks.logger.disabled = logger_was_disabled

        self.assertTrue(client._scheduler_jobs_registered)
        self.assertEqual(scheduler.start_calls, 1)
        self.assertIn("scheduled_pred_1", scheduler.jobs)
        self.assertIn("scheduled_pred_2", scheduler.jobs)


class UsageLogLocationTests(unittest.TestCase):
    def test_channel_label_supports_guild_channels_and_dms(self):
        label = _load_source_function(
            Path("lib/bot/setup_commands.py"), "_usage_channel_label"
        )

        guild_interaction = types.SimpleNamespace(
            channel=types.SimpleNamespace(mention="#deck", id=10),
            channel_id=10,
            guild=object(),
        )
        fallback_guild_interaction = types.SimpleNamespace(
            channel=types.SimpleNamespace(id=11),
            channel_id=11,
            guild=object(),
        )
        dm_interaction = types.SimpleNamespace(
            channel=types.SimpleNamespace(id=12),
            channel_id=12,
            guild=None,
        )

        self.assertEqual(label(guild_interaction), "#deck")
        self.assertEqual(label(fallback_guild_interaction), "<#11>")
        self.assertEqual(label(dm_interaction), "a DM")


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


def _valid_database_bytes(
    directory,
    *,
    include_all_tables=True,
    populate=True,
    user_balance=42,
    bot_balance=None,
    bank_balance=None,
):
    path = Path(directory) / "source.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE ukpence (user_id TEXT PRIMARY KEY, balance INTEGER NOT NULL)"
        )
        if populate:
            effective_bot_balance = (
                max(backup_manager.ECONOMY_TOTAL_SUPPLY - user_balance, 0)
                if bot_balance is None
                else bot_balance
            )
            connection.execute(
                "INSERT INTO ukpence VALUES ('sailor', ?)", (user_balance,)
            )
            connection.execute(
                "INSERT INTO ukpence VALUES (?, ?)",
                (str(backup_manager.BOT_ID), effective_bot_balance),
            )
        if include_all_tables:
            connection.execute(
                "CREATE TABLE xp "
                "(user_id TEXT PRIMARY KEY, xp INTEGER NOT NULL, "
                "last_xp_time INTEGER NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE bank (id INTEGER PRIMARY KEY, balance INTEGER NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE economy_transactions "
                "(id INTEGER PRIMARY KEY, timestamp INTEGER, log_text TEXT)"
            )
            if populate:
                effective_bank_balance = (
                    effective_bot_balance
                    if bank_balance is None
                    else bank_balance
                )
                connection.execute(
                    "INSERT INTO bank VALUES (1, ?)", (effective_bank_balance,)
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
            # Tracks the constant rather than a literal: this used to pin 100, which is
            # about eight hours of the channel's five-minute JSON backups, so a daily
            # database backup was never inside the window that was searched.
            self.assertEqual(channel.requested_limit, backup_manager.DB_RESTORE_SCAN_LIMIT)
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

    async def test_schema_only_database_is_rejected_without_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "database.db"
            attachment = _Attachment(
                "database_backup_2026-07-14.db",
                _valid_database_bytes(directory, populate=False),
            )
            client = _RecoveryClient(_Channel([_Message([attachment])]))

            with self._environment():
                with self.assertRaisesRegex(
                    backup_manager.DatabaseRecoveryError, "supply invariant"
                ):
                    await backup_manager.restore_database_if_missing(
                        destination, client_factory=lambda: client
                    )

            self.assertFalse(destination.exists())
            self.assertTrue(client.closed)
            self._assert_no_recovery_temps(directory)

    async def test_wrong_total_supply_is_rejected_without_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "database.db"
            attachment = _Attachment(
                "database_backup_2026-07-14.db",
                _valid_database_bytes(directory, bot_balance=700_000),
            )
            client = _RecoveryClient(_Channel([_Message([attachment])]))

            with self._environment():
                with self.assertRaisesRegex(
                    backup_manager.DatabaseRecoveryError, "supply invariant"
                ):
                    await backup_manager.restore_database_if_missing(
                        destination, client_factory=lambda: client
                    )

            self.assertFalse(destination.exists())
            self.assertTrue(client.closed)

    async def test_mismatched_bank_and_bot_balance_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "database.db"
            attachment = _Attachment(
                "database_backup_2026-07-14.db",
                _valid_database_bytes(directory, bank_balance=799_957),
            )
            client = _RecoveryClient(_Channel([_Message([attachment])]))

            with self._environment():
                with self.assertRaisesRegex(
                    backup_manager.DatabaseRecoveryError, "mismatched bank"
                ):
                    await backup_manager.restore_database_if_missing(
                        destination, client_factory=lambda: client
                    )

            self.assertFalse(destination.exists())
            self.assertTrue(client.closed)

    async def test_explicit_insolvency_mint_state_remains_restorable(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "database.db"
            attachment = _Attachment(
                "database_backup_2026-07-14.db",
                _valid_database_bytes(
                    directory,
                    user_balance=900_000,
                    bot_balance=0,
                    bank_balance=0,
                ),
            )
            client = _RecoveryClient(_Channel([_Message([attachment])]))

            with self._environment():
                restored = await backup_manager.restore_database_if_missing(
                    destination, client_factory=lambda: client
                )

            self.assertTrue(restored)
            self.assertTrue(destination.exists())
            self.assertTrue(client.closed)

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


class JSONRecoveryTests(unittest.IsolatedAsyncioTestCase):
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

    def _assert_no_restore_temps(self, directory):
        self.assertEqual(list(Path(directory).glob(".json.restore-*")), [])

    async def test_valid_json_backup_is_staged_validated_and_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            summaries = base / "daily_summaries"
            summaries.mkdir()
            (summaries / "existing.json").write_text(
                '{"preserved": true}', encoding="utf-8"
            )
            attachment = _Attachment(
                "json_backup_2026-07-14.zip",
                _zip_bytes(
                    [
                        ("data/json/predictions.json", b'{"123": {"locked": false}}'),
                        ("daily_summaries/restored.json", b'{"restored": true}'),
                    ]
                ),
            )
            channel = _Channel([_Message([attachment])])
            client = _RecoveryClient(channel)

            with self._environment():
                restored = await backup_manager.restore_json_if_missing(
                    base, client_factory=lambda: client
                )

            self.assertTrue(restored)
            self.assertTrue(client.closed)
            self.assertEqual(channel.requested_limit, 200)
            self.assertEqual(
                (base / "data/json/predictions.json").read_text(encoding="utf-8"),
                '{"123": {"locked": false}}',
            )
            self.assertTrue((summaries / "existing.json").exists())
            self.assertTrue((summaries / "restored.json").exists())
            self._assert_no_restore_temps(directory)

    async def test_unexpected_database_member_is_rejected_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            database_path = base / "database.db"
            database_path.write_bytes(b"live-database-sentinel")
            attachment = _Attachment(
                "json_backup_2026-07-14.zip",
                _zip_bytes(
                    [
                        ("data/json/state.json", b'{"safe": true}'),
                        ("database.db", b"attacker-controlled"),
                    ]
                ),
            )
            client = _RecoveryClient(_Channel([_Message([attachment])]))

            with self._environment():
                with self.assertRaisesRegex(
                    backup_manager.JSONRecoveryError, "Unexpected file"
                ):
                    await backup_manager.restore_json_if_missing(
                        base, client_factory=lambda: client
                    )

            self.assertEqual(database_path.read_bytes(), b"live-database-sentinel")
            self.assertFalse((base / "data/json/state.json").exists())
            self.assertTrue(client.closed)
            self._assert_no_restore_temps(directory)

    async def test_invalid_json_rejects_archive_without_partial_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            summaries = base / "daily_summaries"
            summaries.mkdir()
            existing = summaries / "existing.json"
            existing.write_text('{"original": true}', encoding="utf-8")
            attachment = _Attachment(
                "json_backup_2026-07-14.zip",
                _zip_bytes(
                    [
                        ("data/json/broken.json", b"{not-json"),
                        ("daily_summaries/existing.json", b'{"changed": true}'),
                    ]
                ),
            )
            client = _RecoveryClient(_Channel([_Message([attachment])]))

            with self._environment():
                with self.assertRaisesRegex(
                    backup_manager.JSONRecoveryError, "not valid UTF-8 JSON"
                ):
                    await backup_manager.restore_json_if_missing(
                        base, client_factory=lambda: client
                    )

            self.assertEqual(existing.read_text(encoding="utf-8"), '{"original": true}')
            self.assertFalse((base / "data/json/broken.json").exists())
            self.assertTrue(client.closed)
            self._assert_no_restore_temps(directory)

    async def test_json_promotion_failure_rolls_back_every_changed_root(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            summaries = base / "daily_summaries"
            summaries.mkdir()
            existing = summaries / "existing.json"
            existing.write_text('{"original": true}', encoding="utf-8")
            attachment = _Attachment(
                "json_backup_2026-07-14.zip",
                _zip_bytes(
                    [
                        ("data/json/state.json", b'{"restored": true}'),
                        ("daily_summaries/existing.json", b'{"changed": true}'),
                    ]
                ),
            )
            client = _RecoveryClient(_Channel([_Message([attachment])]))
            real_replace = os.replace
            replace_calls = 0

            def fail_second_root_promotion(source, destination):
                nonlocal replace_calls
                replace_calls += 1
                if replace_calls == 3:
                    raise OSError("injected second-root promotion failure")
                return real_replace(source, destination)

            with self._environment(), mock.patch.object(
                backup_manager.os,
                "replace",
                side_effect=fail_second_root_promotion,
            ):
                with self.assertRaisesRegex(
                    backup_manager.JSONRecoveryError, "atomically promote"
                ):
                    await backup_manager.restore_json_if_missing(
                        base, client_factory=lambda: client
                    )

            self.assertFalse((base / "data/json/state.json").exists())
            self.assertEqual(existing.read_text(encoding="utf-8"), '{"original": true}')
            self.assertTrue(client.closed)
            self._assert_no_restore_temps(directory)

    async def test_missing_json_backup_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            client = _RecoveryClient(_Channel([_Message()]))

            with self._environment():
                with self.assertRaisesRegex(
                    backup_manager.JSONRecoveryError, "No JSON backup"
                ):
                    await backup_manager.restore_json_if_missing(
                        directory, client_factory=lambda: client
                    )

            self.assertTrue(client.closed)
            self._assert_no_restore_temps(directory)

    async def test_missing_json_token_fails_before_network_client(self):
        with tempfile.TemporaryDirectory() as directory:
            factory = mock.Mock(side_effect=AssertionError("network client constructed"))

            with self._environment(token=""):
                with self.assertRaisesRegex(
                    backup_manager.JSONRecoveryError, "DISCORD_TOKEN"
                ):
                    await backup_manager.restore_json_if_missing(
                        directory, client_factory=factory
                    )

            factory.assert_not_called()

    async def test_explicit_first_install_flag_allows_empty_json_state(self):
        with tempfile.TemporaryDirectory() as directory:
            factory = mock.Mock(side_effect=AssertionError("network client constructed"))

            with self._environment(token="", allow_empty="true"):
                restored = await backup_manager.restore_json_if_missing(
                    directory, client_factory=factory
                )

            self.assertFalse(restored)
            factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
