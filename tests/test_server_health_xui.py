import unittest
from unittest.mock import AsyncMock, patch

from Shared import server_health, xui_alireza, xui_sanaei


class ServerHealthXuiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        server_health._state.clear()

    def tearDown(self):
        server_health._state.clear()

    async def test_xnet_probe_uses_lightweight_health_probe(self):
        server = {"id": 6, "title": "X-NET France", "panel_type": "xnet"}
        with patch(
            "Shared.xnet_api.health_probe", new_callable=AsyncMock
        ) as health_probe, patch(
            "Shared.xnet_api.test_connect", new_callable=AsyncMock
        ) as test_connect:
            await server_health._probe_server(server)

        health_probe.assert_awaited_once_with(server)
        test_connect.assert_not_awaited()

    async def test_xui_probe_uses_live_test_connect(self):
        server = {"id": 2, "title": "France", "panel_type": "xui"}
        with patch("Shared.xui_api.test_connect", new_callable=AsyncMock) as test_connect, patch.object(
            server_health.hiddify_api, "list_users", new_callable=AsyncMock
        ) as list_users:
            await server_health._probe_server(server)

        test_connect.assert_awaited_once_with(server)
        list_users.assert_not_awaited()

    async def test_alireza_test_connect_bypasses_cached_inbounds(self):
        server = {"id": 3, "title": "XUI", "panel_type": "xui"}
        with patch.object(
            xui_alireza, "_list_inbounds", new_callable=AsyncMock, return_value=[]
        ) as list_inbounds:
            await xui_alireza.test_connect(server)

        list_inbounds.assert_awaited_once_with(server, _force_refresh=True)

    async def test_sanaei_test_connect_bypasses_cached_panel_data(self):
        server = {
            "id": 31,
            "title": "3XUI",
            "panel_type": "xui",
            "xui_api_token": "fake-token",
        }
        with patch.object(
            xui_sanaei, "_list_clients", new_callable=AsyncMock, return_value=[]
        ) as list_clients, patch.object(
            xui_sanaei, "_list_inbounds", new_callable=AsyncMock, return_value=[]
        ) as list_inbounds:
            await xui_sanaei.test_connect(server)

        list_clients.assert_awaited_once_with(server, _force_refresh=True)
        list_inbounds.assert_awaited_once_with(server, _force_refresh=True)

    async def test_hiddify_probe_keeps_existing_list_users_path(self):
        server = {"id": 1, "title": "Main", "panel_type": "hiddify"}
        with patch.object(
            server_health.hiddify_api, "list_users", new_callable=AsyncMock
        ) as list_users:
            await server_health._probe_server(server)

        list_users.assert_awaited_once_with(server)

    async def test_xui_down_alert_once_and_recovery_alert_once(self):
        server = {"id": 4, "title": "XUI & France", "panel_type": "xui"}
        notify = AsyncMock(return_value=True)
        probe = AsyncMock(side_effect=RuntimeError("panel offline <timeout>"))
        with patch.object(server_health.database, "get_servers", return_value=[server]), patch.object(
            server_health, "_probe_server", probe
        ), patch.object(server_health, "notify_admin", notify), patch.object(
            server_health, "SERVER_HEALTH_DOWN_THRESHOLD", 2
        ):
            first = await server_health.run_server_health_check()
            second = await server_health.run_server_health_check()
            third = await server_health.run_server_health_check()
            probe.side_effect = None
            recovered = await server_health.run_server_health_check()
            healthy_again = await server_health.run_server_health_check()

        self.assertEqual(first["alerts"], 0)
        self.assertEqual(second["alerts"], 1)
        self.assertEqual(third["alerts"], 0)
        self.assertEqual(recovered["alerts"], 1)
        self.assertEqual(healthy_again["alerts"], 0)
        self.assertEqual(notify.await_count, 2)
        down_text = notify.await_args_list[0].args[0]
        self.assertIn("پنل: <b>X-UI</b>", down_text)
        self.assertIn("XUI &amp; France", down_text)
        self.assertIn("&lt;timeout&gt;", down_text)
        self.assertIn("دوباره آنلاین شد", notify.await_args_list[1].args[0])

    async def test_failed_notification_is_retried_next_cycle(self):
        server = {"id": 5, "title": "XUI", "panel_type": "xui"}
        notify = AsyncMock(side_effect=[False, True])
        with patch.object(server_health.database, "get_servers", return_value=[server]), patch.object(
            server_health,
            "_probe_server",
            new_callable=AsyncMock,
            side_effect=RuntimeError("offline"),
        ), patch.object(server_health, "notify_admin", notify), patch.object(
            server_health, "SERVER_HEALTH_DOWN_THRESHOLD", 1
        ):
            failed_notice = await server_health.run_server_health_check()
            retried_notice = await server_health.run_server_health_check()
            no_duplicate = await server_health.run_server_health_check()

        self.assertEqual(failed_notice["alerts"], 0)
        self.assertEqual(failed_notice["errors"], 1)
        self.assertEqual(retried_notice["alerts"], 1)
        self.assertEqual(no_duplicate["alerts"], 0)
        self.assertEqual(notify.await_count, 2)


if __name__ == "__main__":
    unittest.main()
