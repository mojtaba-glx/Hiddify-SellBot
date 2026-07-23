from io import BytesIO

import qrcode


def make_qr_image(data: str) -> BytesIO:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(str(data or ""))
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    output = BytesIO()
    output.name = "subscription-qr.png"
    image.save(output, "PNG")
    output.seek(0)
    return output
