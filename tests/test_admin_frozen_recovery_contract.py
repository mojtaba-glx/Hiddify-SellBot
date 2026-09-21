import unittest
from pathlib import Path


class AdminFrozenRecoveryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = Path(__file__).resolve().parents[1] / "AdminBot" / "servers.py"
        cls.source = cls.path.read_text(encoding="utf-8")

    def test_servers_module_compiles(self):
        compile(self.source, str(self.path), "exec")

    def test_recovery_button_and_callback_are_wired(self):
        self.assertIn("🔄 بررسی و بازیابی همین الان", self.source)
        self.assertIn('callback_data=f"server:{server_id}:fzrecover:', self.source)
        self.assertIn('if action == "fzrecover":', self.source)
        self.assertIn("async def _recover_frozen_service_now(", self.source)
        self.assertIn("async def send_frozen_recovery_result(", self.source)


if __name__ == "__main__":
    unittest.main()
