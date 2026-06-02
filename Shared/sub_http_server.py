import base64
import logging
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from Shared import sub_aggregator, userbot_db

logger = logging.getLogger(__name__)
BYTES_PER_GB = 1024 ** 3


def _query_requests_base64(query: str) -> bool:
    params = parse_qs(str(query or ""), keep_blank_values=True)
    for key, values in params.items():
        key_lower = str(key or "").strip().lower()
        value_set = {str(v or "").strip().lower() for v in (values or [])}
        if key_lower in {"base64", "b64"} and (
            not value_set or value_set & {"", "1", "true", "yes", "y", "on", "base64", "b64"}
        ):
            return True
        if key_lower in {"format", "type"} and value_set & {"base64", "b64"}:
            return True
    return False


def _detect_file_format(file_part: str, query: str = ""):
    file_lower = str(file_part or "").strip().lower()
    query_is_b64 = _query_requests_base64(query)
    if file_lower in {"all.txt", "hiddify.txt"}:
        return True if query_is_b64 else False
    if file_lower in {"all.b64", "hiddify.b64"}:
        return True
    return None


def _is_hiddify_client_user_agent(user_agent: str) -> bool:
    return "hiddify" in str(user_agent or "").strip().lower()


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

        try:
            usage_current_gb = max(float((service or {}).get("usage_current") or 0), 0.0)
        except (TypeError, ValueError):
            usage_current_gb = 0.0
        try:
            usage_limit_gb = max(float((service or {}).get("usage_limit") or 0), 0.0)
        except (TypeError, ValueError):
            usage_limit_gb = 0.0
        try:
            days_left = int((service or {}).get("days_left"))
        except (TypeError, ValueError):
            days_left = None

        userinfo_parts = [
            "upload=0",
            f"download={int(usage_current_gb * BYTES_PER_GB)}",
            f"total={int(usage_limit_gb * BYTES_PER_GB)}",
        ]
        if days_left is not None:
            userinfo_parts.append(f"expire={int(time.time()) + max(days_left, 0) * 86400}")

        return {
            "profile-title": f"base64:{encoded_title}",
            "profile-update-interval": "24",
            "subscription-userinfo": "; ".join(userinfo_parts),
            "Content-Disposition": f"inline; filename=\"{ascii_name}.{ext}\"; filename*=UTF-8''{utf8_filename}",
        }

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            p = parsed.path.strip("/")
            query = parsed.query
            parts = p.split("/")
            if not parts or parts[0] != "sub":
                self._write(404, "not found")
                return

            token = ""
            uuid_hint = ""
            is_b64 = False

            # New formats:
            # /sub/{token}/{uuid}/all.txt
            # /sub/{token}/{uuid}/all.txt?base64=1
            # /sub/{token}/{uuid}/all.b64
            if len(parts) == 4:
                token = parts[1].strip()
                uuid_hint = parts[2].strip()
                if not token or not uuid_hint:
                    self._write(404, "not found")
                    return
                detected = _detect_file_format(parts[3], query)
                if detected is None:
                    self._write(404, "not found")
                    return
                is_b64 = detected
            # New formats:
            # /sub/{token}/all.txt | /sub/{token}/all.txt?base64=1 | /sub/{token}/all.b64
            # backward compat:
            # /sub/{token}/hiddify.txt | /sub/{token}/hiddify.b64
            elif len(parts) == 3:
                token = parts[1].strip()
                detected = _detect_file_format(parts[2], query)
                if detected is None:
                    self._write(404, "not found")
                    return
                is_b64 = detected
            # Backward-compatible format: /sub/{token}.txt | /sub/{token}.txt?base64=1 | /sub/{token}.b64
            elif len(parts) == 2:
                file_part = parts[1]
                if file_part.endswith(".txt"):
                    token = file_part[:-4]
                    is_b64 = _query_requests_base64(query)
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
            service = (
                sub_aggregator.sync_service_runtime_from_panels(int(sid))
                or userbot_db.get_service_by_id(int(sid))
                or {}
            )
            lock_reason = sub_aggregator.get_service_lock_reason(sid)
            if lock_reason:
                self._write(403, f"subscription is locked: {lock_reason}")
                return

            include_status_config = not _is_hiddify_client_user_agent(
                self.headers.get("User-Agent", "")
            )
            if is_b64:
                body = sub_aggregator.build_subscription_b64_for_service(
                    sid,
                    include_status_config=include_status_config,
                )
            else:
                body = sub_aggregator.build_subscription_text_for_service(
                    sid,
                    include_status_config=include_status_config,
                )
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
