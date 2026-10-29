"""Persistent device identities and fresh per-launch protocol sessions."""
import errno
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

APP = Path(__file__).resolve().parents[1] / "badge/apps/badgecluster"
spec = importlib.util.spec_from_file_location("cluster_identity", APP / "cluster_identity.py")
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "identity.json"
        self.random = Mock(return_value=b"\x12" * 16)

    def load(self):
        return identity.load_node_id(self.random, str(self.path))

    def test_creates_random_identity_once_and_reuses_without_writing(self):
        node = self.load()
        self.assertEqual(node, "node-" + "12" * 16)
        self.assertEqual(json.loads(self.path.read_text()), {"version": 1, "nodeId": node})
        original = self.path.read_bytes()
        with patch.object(identity.os, "rename", side_effect=AssertionError("Unexpected write")):
            self.assertEqual(self.load(), node)
        self.assertEqual(self.path.read_bytes(), original)
        self.random.assert_called_once_with(16)
        self.assertFalse(Path(str(self.path) + ".tmp").exists())

    def test_distinct_devices_get_distinct_identities(self):
        first = self.load()
        self.random.return_value = b"\x34" * 16
        second = identity.load_node_id(self.random, str(self.path.with_name("second.json")))
        self.assertNotEqual(first, second)

    def test_recovers_complete_temporary_file_without_rotating_identity(self):
        node = "node-" + "ab" * 16
        Path(str(self.path) + ".tmp").write_text(json.dumps({"version": 1, "nodeId": node}))
        self.assertEqual(self.load(), node)
        self.random.assert_not_called()
        self.assertTrue(self.path.exists())

    def test_rename_failure_preserves_id_for_retry(self):
        with patch.object(identity.os, "rename", side_effect=OSError(errno.EIO)):
            with self.assertRaises(OSError):
                self.load()
        self.assertEqual(self.load(), "node-" + "12" * 16)
        self.random.assert_called_once()

    def test_corrupt_or_unknown_identity_does_not_generate_replacement(self):
        for value in ("{", "[]", '{"version": 2, "nodeId": "node-' + "12" * 16 + '"}',
                      '{"version": true, "nodeId": "node-' + "12" * 16 + '"}',
                      '{"version": 1, "nodeId": "node-invalid"}'):
            with self.subTest(value=value):
                self.path.write_text(value)
                with self.assertRaises(ValueError):
                    self.load()
                self.assertEqual(self.path.read_text(), value)
        self.random.assert_not_called()

    def test_permission_error_does_not_generate_identity(self):
        with patch("builtins.open", side_effect=OSError(errno.EACCES)):
            with self.assertRaises(OSError):
                self.load()
        self.random.assert_not_called()

    def test_short_random_source_is_rejected(self):
        self.random.return_value = b"short"
        with self.assertRaises(ValueError):
            self.load()
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
