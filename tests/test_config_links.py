import unittest
from unittest.mock import patch

from AdminBot import servers as admin_servers
from CustomerBot.handlers import callback as customer_callback
from Shared import sub_links
from Shared.config_links import (
    VISIBLE_CONFIG_SCHEMES,
    config_protocol,
    extract_visible_config_link,
    filter_visible_config_links,
    is_visible_config_link,
    normalize_protocol,
)


class ConfigLinkPolicyTests(unittest.TestCase):
    def test_v14_schemes_are_visible(self):
        schemes = {
            "vless": "vless://uuid@example.com:443#VLESS",
            "vmess": "vmess://eyJhZGQiOiJleGFtcGxlLmNvbSJ9",
            "ss": "ss://YWVzLTI1Ni1nY206cGFzcw==@example.com:8388#SS",
            "ssr": "ssr://ZXhhbXBsZS5jb206ODM4ODpvcmlnaW46YWVzLTI1Ni1jZmI6cGxhaW46Y0dGemN3",
            "tuic": "tuic://uuid:pass@example.com:443#TUIC",
            "hysteria": "hysteria://example.com:443?auth=pass#Hysteria",
            "hysteria2": "hysteria2://pass@example.com:443#Hysteria2",
            "hy2": "hy2://pass@example.com:443#Hysteria2",
            "anytls": "anytls://uuid@example.com:443#AnyTLS",
            "wireguard": "wireguard://key@example.com:51820#WG",
            "wg": "wg://key@example.com:51820#WG",
            "socks": "socks://user:pass@example.com:1080#SOCKS",
            "ssh": "ssh://user:pass@example.com:22#SSH",
            "naive": "naive+https://user:pass@example.com:443#Naive",
            "naive+https": "naive+https://user:pass@example.com:443#Naive",
            "naive+quic": "naive+quic://user:pass@example.com:443#Naive",
            "mieru": "mieru://uuid@example.com:443#Mieru",
            "mierus": "mierus://uuid@example.com:443#Mieru",
            "snell": "snell://pass@example.com:443#Snell",
            "tg": "tg://proxy?server=example.com&port=443&secret=ee00",
            "mtproxies": "mtproxies://example.com:443#MTProxy",
        }
        self.assertEqual(set(schemes), set(VISIBLE_CONFIG_SCHEMES))
        for scheme, link in schemes.items():
            with self.subTest(scheme=scheme):
                self.assertTrue(is_visible_config_link(link))
                self.assertEqual(extract_visible_config_link(link), link)

    def test_real_trojan_is_hidden(self):
        link = "trojan://pass@example.com:443?sni=example.com#Trojan"
        self.assertFalse(is_visible_config_link(link))
        self.assertEqual(extract_visible_config_link(link), "")
        self.assertEqual(filter_visible_config_links([link]), [])

    def test_status_trojan_remains_hidden_from_config_extraction(self):
        link = "trojan://expired@status.invalid:443#subscription-expired"
        self.assertFalse(is_visible_config_link(link))

    def test_non_config_schemes_and_embedded_links_are_rejected(self):
        for link in (
            "https://example.com/sub",
            "hiddify://import/abc",
            "tg://resolve?domain=example",
            "vless://uuid@example.com:443#VLESS and more",
            "vless://short",
        ):
            with self.subTest(link=link):
                self.assertEqual(extract_visible_config_link(link), "")

    def test_protocol_aliases_are_canonical(self):
        cases = {
            "hy2://pass@example.com:443#Hysteria2": "hysteria2",
            "wg://key@example.com:51820#WG": "wireguard",
            "mierus://uuid@example.com:443#Mieru": "mieru",
            "naive+https://user:pass@example.com:443#Naive": "naive",
            "tg://proxy?server=example.com&port=443&secret=ee00": "telegram",
        }
        for link, expected in cases.items():
            with self.subTest(link=link):
                self.assertEqual(config_protocol(link), expected)
        self.assertEqual(normalize_protocol("shadowsocks"), "ss")
        self.assertEqual(normalize_protocol("hy2"), "hysteria2")

    def test_filter_deduplicates_and_matches_protocol(self):
        links = [
            "vless://uuid@example.com:443#VLESS",
            "vless://uuid@example.com:443#VLESS",
            "hy2://pass@example.com:443#Hysteria2",
            "trojan://pass@example.com:443#Trojan",
        ]
        self.assertEqual(
            filter_visible_config_links(links),
            [
                "vless://uuid@example.com:443#VLESS",
                "hy2://pass@example.com:443#Hysteria2",
            ],
        )
        self.assertEqual(
            filter_visible_config_links(links, "hysteria2"),
            ["hy2://pass@example.com:443#Hysteria2"],
        )


class DirectConfigCollectorTests(unittest.TestCase):
    def _collect(self, protocol=""):
        lines = [
            "vless://uuid@example.com:443#VLESS",
            "hy2://pass@example.com:443#Hysteria2",
            "trojan://pass@example.com:443#Trojan",
            "tg://proxy?server=example.com&port=443&secret=ee00",
        ]
        svc = {"id": 1, "server_id": 1, "panel_user_uuid": "uuid"}
        with (
            patch.object(sub_links, "_agency_service_is_locked", return_value=False),
            patch.object(
                sub_links,
                "get_service_panel_targets",
                return_value=[({"id": 1}, "uuid", "")],
            ),
            patch.object(
                sub_links,
                "_build_user_base_url",
                return_value="https://sub.example/uuid",
            ),
            patch.object(sub_links, "_fetch_remote_lines", return_value=lines),
        ):
            return sub_links.collect_all_direct_configs_for_service(
                svc,
                protocol,
            )

    def test_collector_keeps_v14_schemes_and_drops_real_trojan(self):
        self.assertEqual(
            self._collect(),
            [
                "vless://uuid@example.com:443#VLESS",
                "hy2://pass@example.com:443#Hysteria2",
                "tg://proxy?server=example.com&port=443&secret=ee00",
            ],
        )

    def test_collector_filters_requested_protocol(self):
        self.assertEqual(
            self._collect("vless"),
            ["vless://uuid@example.com:443#VLESS"],
        )
        self.assertEqual(
            self._collect("hysteria2"),
            ["hy2://pass@example.com:443#Hysteria2"],
        )


class _FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))


class _FakeContext:
    def __init__(self):
        self.bot = _FakeBot()


class AdminDirectConfigMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_menu_uses_protocols_returned_by_panel_and_omits_trojan(self):
        context = _FakeContext()
        configs = [
            {"link": "vless://uuid@example.com:443#VLESS"},
            {"link": "trojan://pass@example.com:443#Trojan"},
            {"link": "hy2://pass@example.com:443#Hysteria2"},
        ]
        with (
            patch.object(
                admin_servers.database,
                "get_server_by_id",
                return_value={"id": 1},
            ),
            patch.object(
                admin_servers.hiddify_api,
                "get_user_by_uuid",
                return_value={"name": "user"},
            ),
            patch.object(
                admin_servers.hiddify_api,
                "get_user_configs",
                return_value=configs,
            ),
        ):
            await admin_servers.send_direct_config_menu(
                1,
                "uuid",
                123,
                context,
            )

        self.assertEqual(len(context.bot.messages), 1)
        _, text, kwargs = context.bot.messages[0]
        buttons = [
            button
            for row in kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        labels = [button.text for button in buttons]
        callbacks = [button.callback_data for button in buttons]
        self.assertIn("🟢 VLESS", labels)
        self.assertIn("🟠 Hysteria2", labels)
        self.assertNotIn("🟠 TROJAN", labels)
        self.assertTrue(
            any(callback.endswith(":vless") for callback in callbacks)
        )
        self.assertFalse(
            any(callback.endswith(":trojan") for callback in callbacks)
        )


class CustomerDirectConfigDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_non_xui_result_reports_error_instead_of_empty_box(self):
        msg = _FakeMessage()
        with (
            patch.object(
                customer_callback,
                "collect_all_direct_configs_for_service",
                return_value=[],
            ),
            patch.object(
                customer_callback,
                "get_service_node_base_urls",
                return_value=["https://sub.example/abc"],
            ),
        ):
            await customer_callback._send_service_direct_configs(msg, {"id": 1})

        self.assertEqual(len(msg.replies), 1)
        self.assertIn("کانفیگی از لینک اشتراک استخراج نشد", msg.replies[0][0])
        self.assertIn("https://sub.example/abc/all.txt", msg.replies[0][0])

    async def test_protocol_filter_is_applied_to_delivery(self):
        msg = _FakeMessage()
        links = [
            "vless://uuid@example.com:443#VLESS",
            "hy2://pass@example.com:443#Hysteria2",
            "trojan://pass@example.com:443#Trojan",
        ]
        with (
            patch.object(
                customer_callback,
                "collect_all_direct_configs_for_service",
                return_value=links,
            ),
            patch.object(
                customer_callback,
                "get_service_node_base_urls",
                return_value=["https://sub.example/abc"],
            ),
            patch.object(
                customer_callback,
                "_resolve_live_server_title",
                return_value="",
            ),
        ):
            await customer_callback._send_service_direct_configs(
                msg,
                {"id": 1},
                "vless",
            )

        self.assertEqual(len(msg.replies), 1)
        text = msg.replies[0][0]
        self.assertIn("VLESS", text)
        self.assertIn("vless://uuid@example.com:443#VLESS", text)
        self.assertNotIn("hy2://", text)
        self.assertNotIn("trojan://", text)


if __name__ == "__main__":
    unittest.main()
