import unittest
from unittest.mock import patch

from Shared import sub_aggregator


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return b"vless://test-config\n"


class TestSubAggregatorFetch(unittest.TestCase):
    def test_fetch_uses_browser_user_agent(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return _FakeResponse()

        with patch.object(sub_aggregator, "urlopen", side_effect=fake_urlopen):
            lines = sub_aggregator._fetch_lines("https://node.example.com/user/all.txt")

        self.assertEqual(lines, ["vless://test-config"])
        self.assertEqual(captured["timeout"], 12)
        self.assertIn("Mozilla/5.0", captured["request"].get_header("User-agent"))
        self.assertNotIn("HiddifySellBot", captured["request"].get_header("User-agent"))
