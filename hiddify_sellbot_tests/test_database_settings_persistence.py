import json
import tempfile
import unittest
from pathlib import Path

from Shared import database


class TestDatabaseSettingsPersistence(unittest.TestCase):
    def setUp(self):
        self._orig_db_path = database.DB_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        database.DB_PATH = str(Path(self._tmpdir.name) / "servers.json")

    def tearDown(self):
        database.DB_PATH = self._orig_db_path
        self._tmpdir.cleanup()

    def _read_db(self):
        p = Path(database.DB_PATH)
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    def test_add_server_preserves_cards_settings(self):
        database.add_or_update_card(owner="Test Owner", number="TEST-CARD-001", bank_name="Melli")
        before = self._read_db()
        self.assertIn("settings", before)
        self.assertEqual(len(before["settings"].get("cards", [])), 1)

        database.add_server(
            {
                "title": "server-1",
                "panel_url": "https://example.com",
                "admin_proxy_path": "admin",
                "admin_uuid": "uuid",
                "user_proxy_path": "user",
                "users_limit": 100,
            }
        )

        after = self._read_db()
        self.assertIn("settings", after)
        self.assertEqual(len(after["settings"].get("cards", [])), 1)
        self.assertEqual(after["settings"]["cards"][0]["number"], "TESTCARD001")

    def test_missing_servers_key_does_not_drop_settings(self):
        p = Path(database.DB_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
                    "settings": {
                        "cards": [
                            {
                                "number": "TEST-CARD-002",
                                "owner": "Owner",
                                "bank": "",
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        database.add_server(
            {
                "title": "server-2",
                "panel_url": "https://example.org",
                "admin_proxy_path": "admin",
                "admin_uuid": "uuid",
                "user_proxy_path": "user",
                "users_limit": 100,
            }
        )

        after = self._read_db()
        self.assertIn("settings", after)
        self.assertEqual(len(after["settings"].get("cards", [])), 1)
        self.assertEqual(after["settings"]["cards"][0]["number"], "TEST-CARD-002")


if __name__ == "__main__":
    unittest.main()
