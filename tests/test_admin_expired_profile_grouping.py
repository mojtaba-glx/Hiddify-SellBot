import ast
from pathlib import Path
from typing import Any, Dict, List
import unittest


ROOT = Path(__file__).resolve().parents[1]
USERBOT = ROOT / "AdminBot" / "userbot.py"


def _function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            if segment:
                return segment
    raise AssertionError(f"{name} not found")


class ExpiredListDedupTests(unittest.TestCase):
    def _load_helpers(self):
        source = USERBOT.read_text(encoding="utf-8")
        names = (
            "_expired_item_uuid",
            "_expired_item_label",
            "_expired_item_priority",
            "_dedupe_expired_items",
        )

        def _service_primary_target(svc):
            return int(svc.get("server_id") or 0), str(svc.get("user_uuid") or "")

        def _extract_service_uuid(svc):
            return str(svc.get("user_uuid") or "")

        ns = {
            "Any": Any,
            "Dict": Dict,
            "List": List,
            "_service_primary_target": _service_primary_target,
            "_extract_service_uuid": _extract_service_uuid,
        }
        exec("\n\n".join(_function_source(source, name) for name in names), ns)
        return ns

    def test_same_agency_uuid_is_shown_only_once(self):
        ns = self._load_helpers()
        dedupe = ns["_dedupe_expired_items"]

        services = [
            {
                "_source": "agent",
                "id": 10,
                "agent_id": 7,
                "customer_id": None,
                "agent_username": "@Nedaajm76",
                "name": "Hadis",
                "panel_user_uuid": "uuid-hadis",
            },
            {
                "_source": "agent",
                "id": 11,
                "agent_id": 7,
                "customer_id": 201,
                "customer_full_name": "Hadis",
                "name": "Hadis",
                "panel_user_uuid": "uuid-hadis",
            },
        ]

        items = dedupe(services)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["customer_id"], 201)
        self.assertEqual(ns["_expired_item_label"](items[0]), "Hadis")

    def test_two_real_services_are_not_merged(self):
        ns = self._load_helpers()
        dedupe = ns["_dedupe_expired_items"]
        services = [
            {"_source": "agent", "id": 1, "name": "Hadis", "panel_user_uuid": "uuid-a"},
            {"_source": "agent", "id": 2, "name": "خانم عارفی", "panel_user_uuid": "uuid-b"},
        ]
        items = dedupe(services)
        self.assertEqual(len(items), 2)

    def test_direct_agent_row_uses_service_name_not_agent_name(self):
        ns = self._load_helpers()
        label = ns["_expired_item_label"](
            {
                "_source": "agent",
                "id": 1,
                "name": "Hadis",
                "agent_username": "@Nedaajm76",
                "panel_user_uuid": "uuid-a",
            }
        )
        self.assertEqual(label, "Hadis")


if __name__ == "__main__":
    unittest.main()
