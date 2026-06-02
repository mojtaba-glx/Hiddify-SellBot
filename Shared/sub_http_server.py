import base64
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urlparse

from Shared import sub_aggregator, userbot_db

logger = logging.getLogger(__name__)


class _SubHandler(BaseHTTPRequestHandler):
    def _write(
        self,
        status: int,
        body: str,
        content_type: str = "text/plain; charset=utf-8",
        headers: dict | None = None,
    ) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            if key and value is not None:
                self.send_header(str(key), str(value))
        self.end_headers()
        self.wfile.write(encoded)

    @staticmethod
    def _subscription_headers(service: dict, is_b64: bool) -> dict:
        title = str((service or {}).get("name") or "subscription").strip() or "subscription"
        encoded_title = base64.b64encode(title.encode("utf-8")).decode("ascii")
        ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", title).strip("._-") or "subscription"
        ext = "b64" if is_b64 else "txt"
        utf8_filename = quote(f"{title}.{ext}")
        return {
            "profile-title": f"base64:{encoded_title}",
            "Content-Disposition": f"inline; filename=\"{ascii_name}.{ext}\"; filename*=UTF-8''{utf8_filename}",
        }

    def do_GET(self) -> None:  # noqa: N802
        try:
            p = urlparse(self.path).path.strip("/")
            parts = p.split("/")
            if not parts or parts[0] != "sub":
                self._write(404, "not found")
                return

            token = ""
            uuid_hint = ""
            is_b64 = False

            def _detect_file_format(file_part: str):
                file_lower = str(file_part or "").strip().lower()
                if file_lower in {"all.txt", "hiddify.txt"}:
                    return False
                if file_lower in {"all.b64", "hiddify.b64"}:
                    return True
                return None

            # New formats:
            # /sub/{token}/{uuid}/all.txt
            # /sub/{token}/{uuid}/all.b64
            if len(parts) == 4:
                token = parts[1].strip()
                uuid_hint = parts[2].strip()
                if not token or not uuid_hint:
                    self._write(404, "not found")
                    return
                detected = _detect_file_format(parts[3])
                if detected is None:
                    self._write(404, "not found")
                    return
                is_b64 = detected
            # New formats:
            # /sub/{token}/all.txt | /sub/{token}/all.b64
            # backward compat:
            # /sub/{token}/hiddify.txt | /sub/{token}/hiddify.b64
            elif len(parts) == 3:
                token = parts[1].strip()
                detected = _detect_file_format(parts[2])
                if detected is None:
                    self._write(404, "not found")
                    return
                is_b64 = detected
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
            if not sid and uuid_hint:
                owner = userbot_db.get_service_owner_by_panel_uuid(uuid_hint)
                if owner and owner.get("service_id"):
                    sid = int(owner["service_id"])
            if not sid:
                owner = userbot_db.get_service_owner_by_panel_uuid(token)
                if owner and owner.get("service_id"):
                    sid = int(owner["service_id"])
            if not sid:
                self._write(404, "subscription token not found")
                return
            service = userbot_db.get_service_by_id(int(sid)) or {}
            lock_reason = sub_aggregator.get_service_lock_reason(sid)
            if lock_reason:
                self._write(403, f"subscription is locked: {lock_reason}")
                return

            if is_b64:
                body = sub_aggregator.build_subscription_b64_for_service(sid)
            else:
                body = sub_aggregator.build_subscription_text_for_service(sid)
            if not body:
                self._write(404, "subscription is empty")
                return
            self._write(200, body, headers=self._subscription_headers(service, is_b64))
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
