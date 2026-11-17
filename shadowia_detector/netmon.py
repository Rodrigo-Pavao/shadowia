"""Monitor de conexões de rede a serviços de IA.

Detecta processos locais (apps desktop do ChatGPT/Claude, scripts usando APIs,
navegadores sem a extensão) com conexões ativas a endpoints de IA. Requer o
pacote opcional `psutil`. Os registros são indícios: serviços atrás de CDNs
podem compartilhar IPs com outros sites.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

from .providers import PROVIDERS

log = logging.getLogger("shadowia.netmon")

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover - dependência opcional
    psutil = None

DEDUP_MINUTES = 10
RESOLVE_EVERY_SEC = 900


def resolve_provider_ips() -> dict[str, str]:
    """Mapa IP -> provider_id a partir dos domínios do catálogo."""
    ip_map: dict[str, str] = {}
    for p in PROVIDERS:
        for host in p.web_domains + p.api_domains:
            try:
                for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP):
                    ip_map.setdefault(info[4][0], p.id)
            except OSError:
                continue
    return ip_map


class NetworkMonitor(threading.Thread):
    def __init__(self, service, interval: int = 30):
        super().__init__(name="shadowia-netmon", daemon=True)
        self.service = service
        self.interval = max(5, interval)
        self._stop = threading.Event()
        self._ip_map: dict[str, str] = {}
        self._resolved_at = 0.0

    @staticmethod
    def available() -> bool:
        return psutil is not None

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        if psutil is None:
            log.info("psutil não instalado: monitor de rede desativado")
            return
        log.info("Monitor de rede iniciado (intervalo %ss)", self.interval)
        while not self._stop.is_set():
            try:
                if self.service.config.network_monitor:
                    self.scan()
            except Exception as exc:  # noqa: BLE001
                log.warning("Erro no monitor de rede: %s", exc)
            self._stop.wait(self.interval)

    def scan(self) -> int:
        if time.time() - self._resolved_at > RESOLVE_EVERY_SEC:
            self._ip_map = resolve_provider_ips()
            self._resolved_at = time.time()
        try:
            conns = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, PermissionError):
            conns = self._per_process_connections()
        since = (datetime.now(timezone.utc) - timedelta(minutes=DEDUP_MINUTES)).isoformat(timespec="seconds")
        recorded = 0
        seen: set[tuple[str, str]] = set()
        for c in conns:
            if not c.raddr or c.status != psutil.CONN_ESTABLISHED:
                continue
            provider = self._ip_map.get(c.raddr.ip)
            if not provider:
                continue
            pname = self._process_name(c.pid)
            key = (provider, pname)
            if key in seen:
                continue
            seen.add(key)
            if self.service.db.recent_network_event(provider, pname, since):
                continue
            self.service.ingest_network(provider, pname, f"{c.raddr.ip}:{c.raddr.port}")
            recorded += 1
        return recorded

    @staticmethod
    def _per_process_connections():
        out = []
        for proc in psutil.process_iter(["pid"]):
            try:
                for c in proc.net_connections(kind="inet"):
                    out.append(_WithPid(c, proc.pid))
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
        return out

    @staticmethod
    def _process_name(pid) -> str:
        if not pid:
            return "desconhecido"
        try:
            return psutil.Process(pid).name()
        except Exception:  # noqa: BLE001
            return f"pid:{pid}"


class _WithPid:
    """Adapta conexões de Process.net_connections() (sem pid) ao formato global."""

    def __init__(self, conn, pid):
        self.raddr = conn.raddr
        self.status = conn.status
        self.pid = pid
