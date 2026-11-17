"""Servidor HTTP local: API REST para a extensão e dashboard web."""

from __future__ import annotations

import csv
import hmac
import io
import json
import logging
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import APP_NAME, __version__
from .detector import CATEGORY_LABELS
from .providers import PROVIDERS
from .service import DetectorService, ValidationError

log = logging.getLogger("shadowia.http")
STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 1_000_000


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def make_handler(service: DetectorService):
    allowed_hosts = {f"127.0.0.1:{service.config.port}", f"localhost:{service.config.port}",
                     f"[::1]:{service.config.port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = f"{APP_NAME}/{__version__}"

        # ----------------------------------------------------------- utils
        def log_message(self, fmt, *args):  # noqa: D401 - silencia log padrão
            log.debug("%s - %s", self.address_string(), fmt % args)

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, data, status: int = 200):
            self._send(status, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ApiError(413, "requisição muito grande")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw or b"{}")
            except json.JSONDecodeError as exc:
                raise ApiError(400, "JSON inválido") from exc
            if not isinstance(data, dict):
                raise ApiError(400, "esperado objeto JSON")
            return data

        def _check_host(self):
            # Proteção contra DNS rebinding: só aceita Host local.
            if (self.headers.get("Host") or "") not in allowed_hosts:
                raise ApiError(403, "host não permitido")

        def _require_token(self):
            token = self.headers.get("X-ShadowIA-Token") or ""
            if not hmac.compare_digest(token, service.config.token):
                raise ApiError(401, "token inválido")

        def _require_dashboard(self):
            # Escritas pelo dashboard: exige cabeçalho customizado (força preflight CORS,
            # bloqueando requisições forjadas por outros sites).
            if self.headers.get("X-ShadowIA-Dashboard") != "1":
                raise ApiError(403, "requisição não permitida")

        def _dispatch(self, method: str):
            try:
                self._check_host()
                url = urlparse(self.path)
                q = {k: v[-1] for k, v in parse_qs(url.query).items()}
                route = getattr(self, "_route_" + method)
                route(url.path, q)
            except ApiError as e:
                self._json({"error": e.message}, e.status)
            except ValidationError as e:
                self._json({"error": str(e)}, 400)
            except Exception:  # noqa: BLE001
                log.exception("Erro ao processar %s %s", method, self.path)
                self._json({"error": "erro interno"}, 500)

        def do_GET(self):
            self._dispatch("GET")

        def do_HEAD(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_OPTIONS(self):
            # Sem CORS: navegadores bloqueiam chamadas cross-origin não simples.
            self._send(204, b"", "text/plain")

        # ---------------------------------------------------------- routes
        def _route_GET(self, path: str, q: dict):
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/health":
                return self._json({"app": APP_NAME, "version": __version__, "status": "ok"})
            if path == "/api/ping":
                self._require_token()
                return self._json({"ok": True, "machine_user": service.machine_user, "hostname": service.hostname})
            if path == "/api/meta":
                return self._json({
                    "app": APP_NAME, "version": __version__,
                    "providers": [{"id": p.id, "name": p.name, "vendor": p.vendor} for p in PROVIDERS],
                    "categories": CATEGORY_LABELS,
                    "machine_user": service.machine_user, "hostname": service.hostname,
                    "network_monitor_available": getattr(service, "netmon_available", False),
                })
            if path == "/api/stats":
                return self._json(service.db.stats(_int(q.get("days"), 30, 1, 365), _int(q.get("tz"), 0, -900, 900)))
            if path == "/api/events":
                return self._json(service.db.list_events(
                    limit=_int(q.get("limit"), 100, 1, 1000), offset=_int(q.get("offset"), 0, 0, 10**9),
                    provider=q.get("provider") or None, account_type=q.get("account_type") or None,
                    min_score=_int(q.get("min_score"), 0, 0, 100), type_=q.get("type") or None,
                    search=q.get("q") or None, days=_int(q.get("days"), 0, 0, 3650) or None))
            m = re.fullmatch(r"/api/events/(\d+)", path)
            if m:
                ev = service.db.get_event(int(m.group(1)))
                if not ev:
                    raise ApiError(404, "evento não encontrado")
                return self._json(ev)
            if path == "/api/alerts":
                return self._json(service.db.list_alerts(status=q.get("status") or None,
                                                         limit=_int(q.get("limit"), 200, 1, 1000),
                                                         days=_int(q.get("days"), 0, 0, 3650) or None))
            if path == "/api/config":
                return self._json(service.config.public_dict())
            if path == "/api/config/token":
                # Exibido no dashboard para configurar a extensão (mesma origem apenas).
                self._require_dashboard()
                return self._json({"token": service.config.token})
            if path == "/api/export/events.csv":
                rows = service.db.list_events(limit=100000, days=_int(q.get("days"), 0, 0, 3650) or None)
                return self._csv("eventos.csv", rows, ["id", "ts", "type", "source", "provider", "account_email",
                                                       "account_type", "machine_user", "hostname", "browser",
                                                       "score", "level", "categories", "prompt_chars",
                                                       "prompt_text", "file_name", "file_size", "process_name", "url"])
            if path == "/api/export/alerts.csv":
                rows = service.db.list_alerts(limit=100000, days=_int(q.get("days"), 0, 0, 3650) or None)
                return self._csv("alertas.csv", rows, ["id", "ts", "provider", "account_email", "account_type",
                                                       "machine_user", "level", "score", "categories", "summary",
                                                       "status", "notes", "prompt_text", "file_name"])
            raise ApiError(404, "rota não encontrada")

        def _route_POST(self, path: str, q: dict):
            if path == "/api/events":
                self._require_token()
                return self._json(service.ingest(self._body()), 201)
            if path == "/api/analyze":
                self._require_dashboard()
                return self._json(service.analyze_only(str(self._body().get("text") or "")))
            raise ApiError(404, "rota não encontrada")

        def _route_PUT(self, path: str, q: dict):
            if path == "/api/config":
                self._require_dashboard()
                service.config.update(self._body())
                service.reload()
                return self._json(service.config.public_dict())
            raise ApiError(404, "rota não encontrada")

        def _route_PATCH(self, path: str, q: dict):
            m = re.fullmatch(r"/api/alerts/(\d+)", path)
            if m:
                self._require_dashboard()
                body = self._body()
                try:
                    ok = service.db.update_alert(int(m.group(1)), body.get("status"), body.get("notes"))
                except ValueError as e:
                    raise ApiError(400, str(e)) from e
                if not ok:
                    raise ApiError(404, "alerta não encontrado")
                return self._json({"ok": True})
            raise ApiError(404, "rota não encontrada")

        # ---------------------------------------------------------- helpers
        def _static(self, rel: str):
            target = (STATIC_DIR / rel).resolve()
            if STATIC_DIR.resolve() not in target.parents or not target.is_file():
                raise ApiError(404, "arquivo não encontrado")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype)

        def _csv(self, filename: str, rows: list[dict], cols: list[str]):
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=";")
            w.writerow(cols)
            for r in rows:
                w.writerow([_csv_cell(r.get(c)) for c in cols])
            body = ("﻿" + buf.getvalue()).encode("utf-8")  # BOM para abrir corretamente no Excel
            self._send(200, body, "text/csv; charset=utf-8",
                       {"Content-Disposition": f'attachment; filename="{filename}"'})

    return Handler


def _csv_cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, dict)):
        v = json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else ",".join(map(str, v))
    s = str(v)
    # Evita injeção de fórmulas em planilhas.
    if s[:1] in ("=", "+", "-", "@", "\t", "\r"):
        s = "'" + s
    return s


def _int(v, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def create_server(service: DetectorService) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((service.config.host, service.config.port), make_handler(service))
    httpd.daemon_threads = True
    return httpd

