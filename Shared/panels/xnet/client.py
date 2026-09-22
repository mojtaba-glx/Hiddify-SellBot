from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping, Optional

from ..base import PanelError, PanelUnavailable


class XNetClient:
    """Minimal dependency-free HTTP client for X-NET's documented Bearer API.

    Endpoint paths are supplied by the adapter/config rather than guessed here.
    This lets us bind the exact routes from X-NET's in-panel API playground
    after installation without touching bot business logic.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 10.0,
        verify_tls: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.timeout = float(timeout)
        self.verify_tls = bool(verify_tls)
        if not self.base_url:
            raise ValueError("X-NET base_url is required")
        if not self.token:
            raise ValueError("X-NET Bearer token is required")

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: Optional[Mapping[str, Any]] = None,
        query: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        if not path.startswith("/"):
            path = "/" + path
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)

        data = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        if payload is not None:
            data = json.dumps(dict(payload)).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        context = None
        if url.lower().startswith("https://") and not self.verify_tls:
            context = ssl._create_unverified_context()

        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=context) as resp:
                raw = resp.read()
                if not raw:
                    return {}
                ctype = resp.headers.get("Content-Type", "")
                if "json" in ctype.lower():
                    return json.loads(raw.decode("utf-8"))
                try:
                    return json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return {"raw": raw.decode("utf-8", errors="replace")}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise PanelError(f"X-NET API HTTP {exc.code}: {body[:500]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PanelUnavailable(f"X-NET API unavailable: {exc}") from exc
