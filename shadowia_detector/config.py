"""Configuração do ShadowIADetector (arquivo JSON local)."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

STORE_MODES = ("redacted", "full", "hash")


def app_home() -> Path:
    base = os.environ.get("SHADOWIA_HOME")
    path = Path(base) if base else Path.home() / ".shadowia"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8765
    # Token exigido pela extensão para enviar eventos ao agente.
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    # Domínios de e-mail da organização (contas consideradas corporativas).
    corporate_domains: list[str] = field(default_factory=list)
    # Palavras-chave adicionais (nomes de projetos, clientes, códigos internos...).
    custom_keywords: list[str] = field(default_factory=list)
    # Como armazenar o texto dos prompts: redacted | full | hash
    store_mode: str = "redacted"
    # Score mínimo para gerar alerta.
    alert_min_score: int = 50
    # Gera alerta para qualquer uso de IA com conta pessoal, mesmo sem risco no prompt.
    alert_personal_accounts: bool = False
    large_prompt_chars: int = 8000
    retention_days: int = 180
    # Webhook (Teams/Slack/genérico) para alertas a partir deste nível.
    webhook_url: str = ""
    webhook_min_level: str = "critico"
    # Monitor de conexões de rede a domínios de IA (requer psutil).
    network_monitor: bool = True
    network_interval_sec: int = 30

    @classmethod
    def path(cls) -> Path:
        return app_home() / "config.json"

    @classmethod
    def load(cls) -> "Config":
        p = cls.path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            known = {f.name for f in fields(cls)}
            cfg = cls(**{k: v for k, v in data.items() if k in known})
        else:
            cfg = cls()
        cfg.validate()
        cfg.save()
        return cfg

    def validate(self) -> None:
        if self.store_mode not in STORE_MODES:
            self.store_mode = "redacted"
        self.corporate_domains = sorted({d.strip().lower().lstrip("@") for d in self.corporate_domains if d.strip()})
        self.custom_keywords = [k.strip() for k in self.custom_keywords if k.strip()]
        self.alert_min_score = max(1, min(100, int(self.alert_min_score)))
        self.port = int(self.port)

    def save(self) -> None:
        p = self.path()
        p.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass

    def public_dict(self) -> dict:
        d = asdict(self)
        d.pop("token")
        return d

    # Campos que podem ser alterados pelo dashboard.
    EDITABLE = ("corporate_domains", "custom_keywords", "store_mode", "alert_min_score",
                "alert_personal_accounts", "large_prompt_chars", "retention_days",
                "webhook_url", "webhook_min_level", "network_monitor")

    def update(self, data: dict) -> None:
        for key in self.EDITABLE:
            if key in data:
                setattr(self, key, data[key])
        self.validate()
        self.save()
