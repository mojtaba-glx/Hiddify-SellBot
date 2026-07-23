import unittest

from Shared.qr_utils import make_qr_image


class TestQrUtils(unittest.TestCase):
    def test_make_qr_image_returns_png_file(self):
        image = make_qr_image("https://sell.example.com/sub/test-user/all.txt")

        self.assertEqual(image.name, "subscription-qr.png")
        self.assertEqual(image.read(8), b"\x89PNG\r\n\x1a\n")
