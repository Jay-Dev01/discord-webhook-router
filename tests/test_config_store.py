import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config_store import load_config, save_config
from routing import RoutingConfig


class ConfigStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "data" / "config.json"

    def test_first_boot_creates_config_on_volume(self):
        config = load_config(self.path, "123456789012345678")
        self.assertEqual(config.source_channel_id, 123456789012345678)
        self.assertEqual(config.rules, [])
        self.assertEqual(load_config(self.path).data, config.data)

    def test_restart_preserves_saved_rules_and_source(self):
        load_config(self.path, "1")
        config = RoutingConfig.from_dict({"source_channel_id": "1", "routes": [
            {"source_channel_id": "1", "channel_id": "2", "mode": "keyword", "value": "08882"},
        ]})
        save_config(self.path, config)
        self.assertEqual(load_config(self.path, "3").data, config.data)

    def test_missing_source_does_not_create_config(self):
        with self.assertRaisesRegex(ValueError, "SOURCE_CHANNEL_ID"):
            load_config(self.path)
        self.assertFalse(self.path.exists())

    def test_invalid_source_does_not_create_config(self):
        with self.assertRaises(ValueError):
            load_config(self.path, "not-a-channel")
        self.assertFalse(self.path.exists())

    def test_corrupt_config_is_not_overwritten(self):
        self.path.parent.mkdir()
        self.path.write_text("broken config")
        with self.assertRaises(json.JSONDecodeError):
            load_config(self.path, "1")
        self.assertEqual(self.path.read_text(), "broken config")

    def test_failed_replace_preserves_file_and_cleans_temporary(self):
        config = load_config(self.path, "1")
        original = self.path.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                save_config(self.path, config)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob(".routing-*.tmp")), [])
