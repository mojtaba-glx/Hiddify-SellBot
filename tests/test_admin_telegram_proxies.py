import unittest
from html import escape
from unittest.mock import AsyncMock, patch

from AdminBot import servers as admin_servers
from Shared import hiddify_api


SERVER = {
    "id": 1,
    "title": "server",
    "panel_url": "https://panel.example",
    "panel_type": "hiddify",
    "user_proxy_path": "usr",
}


class TelegramProxyApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_mtproxies_endpoint_and_normalizes_links(self):
        response = [
            {
                "title": "cdn.example.com Web",
                "link": "tg://webproxy?server=cdn.example.com%2Fpath&secret=abc",
            },
            {
                "title": "proxy.example.com",
                "link": "tg://proxy?server=proxy.example.com&port=443&secret=ee00",
            },
            {
                "title": "duplicate",
                "link": "tg://proxy?server=proxy.example.com&port=443&secret=ee00",
            },
            {
                "title": "not telegram",
                "link": "vless://uuid@example.com:443#VLESS",
            },
        ]
        with patch.object(
            hiddify_api,
            "_request",
            new=AsyncMock(return_value=response),
        ) as request:
            proxies = await hiddify_api.get_user_telegram_proxies(
                SERVER,
                "11111111-2222-3333-4444-555555555555",
            )

        self.assertEqual(len(proxies), 2)
        self.assertEqual(
            proxies[0]["title"],
            "cdn.example.com Web",
        )
        self.assertIn("tg://webproxy", proxies[0]["link"])
        self.assertEqual(proxies[1]["title"], "proxy.example.com")
        request.assert_awaited_once_with(
            "GET",
            "https://panel.example/usr/11111111-2222-3333-4444-555555555555"
            "/api/v2/user/mtproxies/",
            SERVER,
        )

    async def test_disabled_or_old_panel_returns_empty_list(self):
        with patch.object(
            hiddify_api,
            "_request",
            new=AsyncMock(
                side_effect=hiddify_api.HiddifyApiError(
                    "HTTP 404: Telegram mtproxy is not enable"
                )
            ),
        ):
            proxies = await hiddify_api.get_user_telegram_proxies(SERVER, "uuid")

        self.assertEqual(proxies, [])

    async def test_non_hiddify_panels_are_skipped(self):
        for panel_type in ("xui", "x-net"):
            server = dict(SERVER, panel_type=panel_type)
            with self.subTest(panel_type=panel_type):
                with patch.object(
                    hiddify_api,
                    "_request",
                    new=AsyncMock(),
                ) as request:
                    proxies = await hiddify_api.get_user_telegram_proxies(
                        server,
                        "uuid",
                    )
                self.assertEqual(proxies, [])
                request.assert_not_awaited()


class TelegramProxyUiTests(unittest.IsolatedAsyncioTestCase):
    def test_webproxy_title_is_derived_from_server_parameter(self):
        title = admin_servers._telegram_proxy_title(
            "tg://webproxy?server=cdn.example.com%2Fpath&secret=abc"
        )
        self.assertEqual(title, "cdn.example.com Web")

    async def test_falls_back_to_all_configs_for_older_panels(self):
        with (
            patch.object(
                hiddify_api,
                "get_user_telegram_proxies",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(
                hiddify_api,
                "get_user_configs",
                new=AsyncMock(
                    return_value=[
                        {
                            "link": "tg://proxy?server=old.example.com"
                            "&port=443&secret=ee00"
                        }
                    ]
                ),
            ),
        ):
            proxies = await admin_servers._get_user_telegram_proxies(
                SERVER,
                "uuid",
            )

        self.assertEqual(len(proxies), 1)
        self.assertEqual(proxies[0]["title"], "old.example.com")

    async def _config_menu_labels(self, *, panel_type, xnet=False):
        server = dict(SERVER, panel_type=panel_type)
        message = _FakeMessage()

        with (
            patch.object(
                admin_servers.database,
                "get_server_by_id",
                return_value=server,
            ),
            patch.object(
                hiddify_api,
                "get_user_by_uuid",
                new=AsyncMock(return_value={"name": "user", "uuid": "uuid"}),
            ),
            patch.object(
                admin_servers,
                "_build_user_base_url",
                return_value="https://panel.example/user",
            ),
            patch.object(
                admin_servers,
                "build_user_detail_html_text",
                return_value="detail",
            ),
            patch.object(
                admin_servers.xnet_api,
                "is_xnet_server",
                return_value=xnet,
            ),
            patch.object(
                admin_servers.xui_api,
                "is_xui_server",
                return_value=panel_type == "xui",
            ),
        ):
            await admin_servers.send_user_configs_menu(
                1,
                "uuid",
                10,
                _FakeContext(),
                message=message,
            )

        return [
            button.text
            for row in message.reply_markup.inline_keyboard
            for button in row
        ]

    async def test_hiddify_config_menu_has_telegram_proxy_button(self):
        labels = await self._config_menu_labels(panel_type="hiddify")
        self.assertIn("✈️ پروکسی تلگرام", labels)

    async def test_xui_and_xnet_config_menus_do_not_show_telegram_button(self):
        for panel_type, xnet in (("xui", False), ("xnet", True)):
            with self.subTest(panel_type=panel_type):
                labels = await self._config_menu_labels(
                    panel_type=panel_type,
                    xnet=xnet,
                )
                self.assertNotIn("✈️ پروکسی تلگرام", labels)

    async def test_single_proxy_sends_qr_with_link_and_connect_button(self):
        link = "tg://proxy?server=proxy.example.com&port=443&secret=ee00"
        context = _FakeContext()
        with (
            patch.object(
                admin_servers.database,
                "get_server_by_id",
                return_value=SERVER,
            ),
            patch.object(
                admin_servers,
                "_get_user_telegram_proxies",
                new=AsyncMock(
                    return_value=[
                        {"title": "proxy.example.com", "link": link}
                    ]
                ),
            ),
        ):
            await admin_servers.send_telegram_proxy_menu(
                1,
                "uuid",
                10,
                context,
            )

        context.bot.send_photo.assert_awaited_once()
        kwargs = context.bot.send_photo.await_args.kwargs
        self.assertIn(escape(link), kwargs["caption"])
        self.assertEqual(kwargs["reply_markup"].inline_keyboard[0][0].url, link)


class _FakeMessage:
    text = "old"
    reply_markup = None

    async def edit_text(self, text, **kwargs):
        self.text = text
        self.reply_markup = kwargs.get("reply_markup")


class _FakeBot:
    def __init__(self):
        self.send_photo = AsyncMock()
        self.send_message = AsyncMock()


class _FakeContext:
    def __init__(self):
        self.bot = _FakeBot()


if __name__ == "__main__":
    unittest.main()
