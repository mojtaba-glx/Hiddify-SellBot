import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "AdminBot" / "nodes.py").read_text(encoding="utf-8")


class XnetNodeWebPathTests(unittest.TestCase):
    def test_add_flow_discovers_and_persists_web_base_path(self):
        self.assertIn("await xnet_api.get_panel_config(new_node)", SOURCE)
        self.assertIn('new_node["xnet_web_base_path"] = web_base_path', SOURCE)
        self.assertIn(
            '"xnet_web_base_path": str(new_node.get("xnet_web_base_path") or "").strip("/")',
            SOURCE,
        )

    def test_existing_xnet_node_refreshes_web_base_path(self):
        self.assertIn("if xnet_api.is_xnet_server(child):", SOURCE)
        self.assertIn(
            'database.update_server(target_sid, {"xnet_web_base_path": web_base_path})',
            SOURCE,
        )

    def test_clickable_xnet_node_link_uses_hidden_web_path(self):
        self.assertIn(
            'web_base_path = str((child or {}).get("xnet_web_base_path") or "").strip("/")',
            SOURCE,
        )
        self.assertIn(
            'server_panel_link = f"{base}/{web_base_path}/" if web_base_path else base',
            SOURCE,
        )


if __name__ == "__main__":
    unittest.main()
