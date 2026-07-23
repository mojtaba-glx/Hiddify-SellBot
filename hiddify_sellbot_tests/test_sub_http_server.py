import unittest

from Shared.sub_http_server import (
    _detect_file_format,
    _panel_server_id_from_token,
    _query_requests_base64,
    _sms_amount_candidates_toman,
)


class TestSubHttpServer(unittest.TestCase):
    def test_query_base64_aliases_are_detected(self):
        for query in ("base64=1", "base64=True", "b64=1", "format=base64", "type=b64"):
            with self.subTest(query=query):
                self.assertTrue(_query_requests_base64(query))

    def test_txt_endpoint_can_request_base64_by_query(self):
        self.assertFalse(_detect_file_format("all.txt"))
        self.assertTrue(_detect_file_format("all.txt", "base64=1"))
        self.assertTrue(_detect_file_format("hiddify.txt", "format=base64"))

    def test_b64_endpoint_still_works(self):
        self.assertTrue(_detect_file_format("all.b64"))
        self.assertTrue(_detect_file_format("hiddify.b64"))

    def test_sms_amount_candidates_convert_rial_to_toman(self):
        self.assertEqual(_sms_amount_candidates_toman(1_000_000, "rial"), [100_000])
        self.assertEqual(_sms_amount_candidates_toman(100_000, "toman"), [100_000])
        self.assertEqual(_sms_amount_candidates_toman(1_000_000, "unknown"), [1_000_000, 100_000])

    def test_panel_server_token_is_detected(self):
        self.assertEqual(_panel_server_id_from_token("panel-srv-12"), 12)
        self.assertEqual(_panel_server_id_from_token("srv_34"), 34)
        self.assertEqual(_panel_server_id_from_token("not-a-panel-token"), 0)
