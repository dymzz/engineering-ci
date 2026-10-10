"""Native package cache identity and locked-command contracts, without Dagger."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "uv_sync", ROOT / ".dagger/src/engineering_ci/uv_sync.py"
)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class UvCacheTests(unittest.TestCase):
    def test_python_and_lock_changes_isolate_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "uv.lock"
            lock.write_bytes(b"first lock")
            first = sync.cache_environment("3.14", lock, environment={})
            self.assertEqual(
                first, sync.cache_environment("3.14", lock, environment={})
            )
            self.assertNotEqual(
                first["UV_CACHE_DIR"],
                sync.cache_environment("3.13", lock)["UV_CACHE_DIR"],
            )
            lock.write_bytes(b"different lock")
            self.assertNotEqual(
                first["UV_CACHE_DIR"],
                sync.cache_environment("3.14", lock)["UV_CACHE_DIR"],
            )
            self.assertEqual(first["UV_LINK_MODE"], "copy")
            self.assertNotIn(".venv", first["UV_CACHE_DIR"])

    def test_missing_lock_is_left_for_uv_to_reject(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = sync.cache_environment("3.14", Path(tmp) / "absent")
            self.assertTrue(env["UV_CACHE_DIR"].endswith("missing-lock"))

    def test_exec_keeps_native_locked_command_and_execution_error(self):
        with (
            patch.object(sync, "cache_environment", return_value={"test": "env"}),
            patch.object(
                sync.os, "execvpe", side_effect=OSError("native tool unavailable")
            ) as execute,
        ):
            with self.assertRaisesRegex(OSError, "native tool unavailable"):
                sync.main(["3.14"])
            execute.assert_called_once_with(
                "uv",
                ["uv", "sync", "--locked", "--refresh", "--python", "3.14"],
                {"test": "env"},
            )

    def test_invalid_identity_fails_before_cache_use(self):
        with self.assertRaises(ValueError):
            sync.cache_environment("../../other")
