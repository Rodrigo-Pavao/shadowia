"""Regras de negócio: ingestão de eventos, análise, alertas e notificações."""

from __future__ import annotations

import getpass
import hashlib
import json
import logging
import socket
import threading
import urllib.request
from urllib.parse import urlparse

from .accounts import ACCOUNT_PERSONAL, classify_account, normalize_email
from .config import Config
from .db import Database
from .detector import CATEGORY_LABELS, SEVERITY_WEIGHT, PromptAnalyzer, score_to_level
from .providers import PROVIDERS_BY_ID, provider_for_host, provider_name

log = logging.getLogger("shadowia")

EVENT_TYPES = ("prompt", "upload", "visit")
LEVEL_ORDER = {"nenhum": 0, "baixo": 1, "medio": 2, "alto": 3, "critico": 4}
MAX_PROMPT_CHARS = 200_000

# Extensões de arquivo de maior risco quando enviadas a uma IA.
SENSITIVE_FILE_EXT = {
    ".xlsx": "alta", ".xls": "alta", ".csv": "alta", ".sql": "alta", ".db": "critica", ".sqlite": "critica",
    ".bak": "alta", ".pem": "critica", ".key": "critica", ".pfx": "critica", ".p12": "critica",
    ".env": "critica", ".kdbx": "critica", ".docx": "media", ".doc": "media", ".pdf": "media",
    ".pptx": "media", ".zip": "media", ".7z": "media", ".rar": "media", ".json": "media",
    ".py": "media", ".js": "media", ".ts": "media", ".java": "media", ".cs": "media", ".go": "media",
}


class ValidationError(ValueError):
    pass


class DetectorService:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.machine_user = _safe(getpass.getuser)
        self.hostname = _safe(socket.gethostname)
        self.reload()

    def reload(self) -> None:
        self.analyzer = PromptAnalyzer(self.config.custom_keywords, self.config.large_prompt_chars)

    # ------------------------------------------------------------------ #
    def ingest(self, payload: dict) -> dict:
        """Recebe um evento da extensão de navegador."""
        if not isinstance(payload, dict):
            raise ValidationError("payload deve ser um objeto JSON")
        ev_type = payload.get("type")
        if ev_type not in EVENT_TYPES:
            raise ValidationError(f"type deve ser um de {EVENT_TYPES}")

        url = str(payload.get("url") or "")[:2000]
        provider_id = str(payload.get("provider") or "")
        if provider_id not in PROVIDERS_BY_ID:
            p = provider_for_host(urlparse(url).hostname or "")
            if not p:
                raise ValidationError("provedor de IA não reconhecido")
            provider_id = p.id

        email = normalize_email(payload.get("account_email"))
        account_type = classify_account(email, self.config.corporate_domains)

        event = {
            "type": ev_type,
            "source": "extension",
            "provider": provider_id,
            "url": _strip_query(url),
            "account_email": email,
            "account_type": account_type,
            "machine_user": str(payload.get("machine_user") or self.machine_user)[:120],
            "hostname": str(payload.get("hostname") or self.hostname)[:120],
            "browser": str(payload.get("browser") or "")[:60],
        }

        result_dict = {"score": 0, "level": "nenhum", "categories": [], "findings": []}

        if ev_type == "prompt":
            text = str(payload.get("prompt") or "")[:MAX_PROMPT_CHARS]
            if not text.strip():
                raise ValidationError("prompt vazio")
            analysis = self.analyzer.analyze(text)
            result_dict = analysis.to_dict()
            event.update({
                "prompt_text": self._stored_text(text, analysis.redacted_text),
                "prompt_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "prompt_chars": len(text),
            })
        elif ev_type == "upload":
            name = str(payload.get("file_name") or "arquivo")[:255]
            size = _int(payload.get("file_size"))
            event.update({"file_name": name, "file_size": size})
            result_dict = self._analyze_upload(name, size)

        event.update({
            "score": result_dict["score"],
            "level": result_dict["level"],
            "categories": result_dict["categories"],
            "findings": result_dict["findings"],
        })
        event_id = self.db.insert_event(event)
        alert_id = self._maybe_alert(event_id, event)
        return {"event_id": event_id, "alert_id": alert_id, "account_type": account_type, **result_dict}

    def ingest_network(self, provider_id: str, process_name: str, remote_addr: str) -> int:
        return self.db.insert_event({
            "type": "network", "source": "network", "provider": provider_id,
            "account_type": "desconhecida", "machine_user": self.machine_user,
            "hostname": self.hostname, "process_name": process_name[:120], "remote_addr": remote_addr[:80],
        })

    def analyze_only(self, text: str) -> dict:
        return self.analyzer.analyze(text or "").to_dict()

    # ------------------------------------------------------------------ #
    def _stored_text(self, original: str, redacted: str) -> str:
        mode = self.config.store_mode
        if mode == "full":
            return original
        if mode == "hash":
            return f"[texto não armazenado - {len(original)} caracteres]"
        return redacted

    def _analyze_upload(self, name: str, size: int | None) -> dict:
        findings = []
        ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        sev = SENSITIVE_FILE_EXT.get(ext)
        if sev:
            findings.append({"rule_id": "upload_sensitive_ext", "category": "upload",
                             "category_label": CATEGORY_LABELS["upload"], "severity": sev,
                             "description": f"Envio de arquivo {ext} para IA", "count": 1, "samples": [name]})
        a = self.analyzer.analyze(name)  # nome do arquivo pode conter marcadores (ex.: "confidencial")
        findings += [f.to_dict() for f in a.findings]
        if size and size > 5 * 1024 * 1024:
            findings.append({"rule_id": "upload_large", "category": "exfiltracao",
                             "category_label": CATEGORY_LABELS["exfiltracao"], "severity": "media",
                             "description": "Arquivo grande enviado para IA", "count": 1,
                             "samples": [f"{size / 1024 / 1024:.1f} MB"]})
        score = 15  # todo upload para IA tem risco base
        if findings:
            weights = sorted((SEVERITY_WEIGHT[f["severity"]] for f in findings), reverse=True)
            score = min(100, int(weights[0] + sum(w * 0.3 for w in weights[1:])))
        return {"score": score, "level": score_to_level(score),
                "categories": sorted({f["category"] for f in findings} | {"upload"}), "findings": findings}

    def _maybe_alert(self, event_id: int, event: dict) -> int | None:
        risky = event["score"] >= self.config.alert_min_score
        personal = (self.config.alert_personal_accounts and event["account_type"] == ACCOUNT_PERSONAL
                    and event["type"] in ("prompt", "upload"))
        if not (risky or personal):
            return None
        cats = list(event["categories"])
        level = event["level"]
        if personal and not risky:
            cats = cats or ["conta_pessoal"]
            level = level if LEVEL_ORDER.get(level, 0) >= LEVEL_ORDER["medio"] else "medio"
        summary = self._summary(event, cats)
        alert = {
            "event_id": event_id, "provider": event["provider"], "account_email": event["account_email"],
            "account_type": event["account_type"], "machine_user": event["machine_user"],
            "level": level, "score": event["score"], "categories": cats, "summary": summary,
        }
        alert_id = self.db.insert_alert(alert)
        if self.config.webhook_url and LEVEL_ORDER.get(level, 0) >= LEVEL_ORDER.get(self.config.webhook_min_level, 4):
            threading.Thread(target=self._send_webhook, args=({**alert, "id": alert_id},), daemon=True).start()
        return alert_id

    @staticmethod
    def _summary(event: dict, cats: list[str]) -> str:
        labels = ", ".join(CATEGORY_LABELS.get(c, "Uso de IA com conta pessoal" if c == "conta_pessoal" else c)
                           for c in cats)
        who = event["account_email"] or "conta não identificada"
        what = "Upload" if event["type"] == "upload" else "Prompt"
        return f"{what} em {provider_name(event['provider'])} ({who}, conta {event['account_type']}): {labels}"

    def _send_webhook(self, alert: dict) -> None:
        text = f"🚨 ShadowIADetector [{alert['level'].upper()}] {alert['summary']} (score {alert['score']})"
        body = json.dumps({"text": text, "alert": alert}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.config.webhook_url, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10).close()
        except Exception as exc:  # noqa: BLE001 - nunca derrubar o agente por falha de webhook
            log.warning("Falha ao enviar webhook: %s", exc)


def _strip_query(url: str) -> str:
    try:
        u = urlparse(url)
        return f"{u.scheme}://{u.netloc}{u.path}" if u.scheme else url
    except ValueError:
        return ""


def _int(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _safe(fn) -> str:
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return "desconhecido"
