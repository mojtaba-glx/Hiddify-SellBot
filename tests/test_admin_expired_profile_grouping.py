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


class ExpiredProfileGroupingTests(unittest.TestCase):
    def _load_helpers(self):
        source = USERBOT.read_text(encoding="utf-8")
        names = (
            "_expired_profile_identity",
            "_expired_profile_label",
            "_group_expired_profiles",
        )
        ns = {"Any": Any, "Dict": Dict, "List": List}
        exec("\n\n".join(_function_source(source, name) for name in names), ns)
        return ns

    def test_duplicate_agency_services_count_as_one_profile(self):
        ns = self._load_helpers()
        group = ns["_group_expired_profiles"]

        services = [
            {"_source": "user", "id": 1, "user_id": 101, "full_name": "کاربر اول", "name": "u1"},
            {"_source": "user", "id": 2, "user_id": 102, "full_name": "کاربر دوم", "name": "u2"},
            {
                "_source": "agent", "id": 3, "agent_id": 7, "customer_id": 201,
                "customer_full_name": "مشتری اول", "name": "a1",
            },
            {
                "_source": "agent", "id": 4, "agent_id": 7, "customer_id": 201,
                "customer_full_name": "مشتری اول", "name": "a2",
            },
            {
                "_source": "agent", "id": 5, "agent_id": 8, "customer_id": 202,
                "customer_full_name": "مشتری دوم", "name": "b1",
            },
            {
                "_source": "agent", "id": 6, "agent_id": 8, "customer_id": 202,
                "customer_full_name": "مشتری دوم", "name": "b2",
            },
        ]

        profiles = group(services)
        self.assertEqual(len(profiles), 4)

        customer_201 = next(p for p in profiles if p["kind"] == "customer" and p["owner_id"] == 201)
        customer_202 = next(p for p in profiles if p["kind"] == "customer" and p["owner_id"] == 202)
        self.assertEqual(len(customer_201["services"]), 2)
        self.assertEqual(len(customer_202["services"]), 2)

    def test_direct_agent_services_group_by_agent_id(self):
        ns = self._load_helpers()
        group = ns["_group_expired_profiles"]
        services = [
            {"_source": "agent", "id": 10, "agent_id": 55, "name": "one"},
            {"_source": "agent", "id": 11, "agent_id": 55, "name": "two"},
        ]
        profiles = group(services)
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["kind"], "agent")
        self.assertEqual(profiles[0]["owner_id"], 55)
        self.assertEqual(len(profiles[0]["services"]), 2)


if __name__ == "__main__":
    unittest.main()
