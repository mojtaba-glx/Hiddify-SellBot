import ast
import re
import unittest
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse


SOURCE_PATH = Path(__file__).resolve().parents[1] / "UserBot" / "main.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _load_functions(*names):
    wanted = set(names)
    body = [
        node
        for node in TREE.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in wanted
    ]
    missing = wanted - {node.name for node in body}
    if missing:
        raise AssertionError(f"missing UserBot helpers: {sorted(missing)}")
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    ns = {
        "Any": Any,
        "Optional": Optional,
        "re": re,
        "urlparse": urlparse,
    }
    exec(compile(module, str(SOURCE_PATH), "exec"), ns)
    return ns


class UserBotXnetConfigDeliveryTests(unittest.TestCase):
    def test_xnet_build_user_base_url_uses_native_subscription_endpoint(self):
        ns = _load_functions("_build_user_base_url")
        server = {
            "panel_type": "xnet",
            "panel_url": "http://127.0.0.1:8080",
            "xnet_sub_domain": "xnet.speedll.ir",
        }

        url = ns["_build_user_base_url"](server, "user-uuid")

        self.assertEqual(
            url,
            "https://xnet.speedll.ir/api/v1/sub/user-uuid",
        )
        self.assertNotIn("/all.txt", url)

    def test_xnet_native_subscription_url_is_detected(self):
        ns = _load_functions("_is_native_subscription_url")
        is_native = ns["_is_native_subscription_url"]

        self.assertTrue(
            is_native("https://xnet.speedll.ir/api/v1/sub/user-uuid")
        )
        self.assertFalse(
            is_native("https://user.example.com/abc/user-uuid")
        )

    def test_anytls_and_hysteria2_are_accepted_as_direct_configs(self):
        ns = _load_functions(
            "_sanitize_config_text",
            "_extract_config_link_from_line",
        )
        extract = ns["_extract_config_link_from_line"]

        anytls = (
            "anytls://uuid@xnet.speedll.ir:25544"
            "?sni=xnet.speedll.ir#AnyTLS"
        )
        hy2 = (
            "hysteria2://uuid@xnet.speedll.ir:9662"
            "?sni=xnet.speedll.ir#Hysteria2"
        )

        self.assertEqual(extract(anytls), anytls)
        self.assertEqual(extract(hy2), hy2)

    def test_userbot_xnet_paths_do_not_force_hiddify_all_txt(self):
        # Regression contract for delivery/status code paths. X-NET native
        # subscriptions must remain complete URLs rather than gaining /all.txt.
        self.assertIn(
            "base_url if native_subscription else f\"{base_url}/all.txt\"",
            SOURCE,
        )
        self.assertIn(
            "if _is_native_subscription_url(fallback_base)",
            SOURCE,
        )
        self.assertIn(
            "or _xnet_fallback_check.is_xnet_server",
            SOURCE,
        )


if __name__ == "__main__":
    unittest.main()
