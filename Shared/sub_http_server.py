import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from Shared import sub_aggregator, userbot_db

logger = logging.getLogger(__name__)


class _SubHandler(BaseHTTPRequestHandler):
    def _write(self, status: int, body: str, content_type: str = "text/plain; charset=utf-8") -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        try:
            p = urlparse(self.path).path.strip("/")
            parts = p.split("/")
            if not parts or parts[0] != "sub":
                self._write(404, "not found")
                return

            token = ""
            is_b64 = False

            # New format: /sub/{token}/hiddify.txt | /sub/{token}/hiddify.b64
            if len(parts) == 3:
                token = parts[1].strip()
                file_part = parts[2].strip().lower()
                if file_part == "hiddify.txt":
                    is_b64 = False
                elif file_part == "hiddify.b64":
                    is_b64 = True
                else:
                    self._write(404, "not found")
                    return
            # Backward-compatible format: /sub/{token}.txt | /sub/{token}.b64
            elif len(parts) == 2:
                file_part = parts[1]
                if file_part.endswith(".txt"):
                    token = file_part[:-4]
                    is_b64 = False
                elif file_part.endswith(".b64"):
                    token = file_part[:-4]
                    is_b64 = True
                else:
                    self._write(404, "not found")
                    return
            else:
                self._write(404, "not found")
                return

            sid = userbot_db.get_service_id_by_sub_token(token)
            if not sid:
                self._write(404, "subscription token not found")
                return

            if is_b64:
                body = sub_aggregator.build_subscription_b64_for_service(sid)
            else:
                body = sub_aggregator.build_subscription_text_for_service(sid)
            if not body:
                self._write(404, "subscription is empty")
                return
            self._write(200, body)
        except Exception as e:
            logger.exception("sub server request failed: %s", e)
            self._write(500, "internal error")

    def log_message(self, format: str, *args):  # noqa: A003
        return


_server_thread: threading.Thread | None = None


def start_sub_server(host: str, port: int) -> None:
    global _server_thread
    if _server_thread and _server_thread.is_alive():
        return

    def _run():
        httpd = ThreadingHTTPServer((host, int(port)), _SubHandler)
        logger.info("Subscription HTTP server started on %s:%s", host, port)
        httpd.serve_forever()

    _server_thread = threading.Thread(target=_run, daemon=True)
    _server_thread.start()
