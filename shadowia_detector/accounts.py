"""Classificação de contas (e-mail pessoal x corporativo)."""

from __future__ import annotations

import re

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})$")

# Provedores de e-mail pessoal/gratuito mais comuns (globais e Brasil).
PERSONAL_EMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "outlook.com.br", "hotmail.com",
    "hotmail.com.br", "live.com", "live.com.br", "msn.com", "yahoo.com",
    "yahoo.com.br", "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com",
    "proton.me", "protonmail.com", "pm.me", "tutanota.com", "tuta.io",
    "zoho.com", "gmx.com", "gmx.net", "mail.com", "yandex.com", "yandex.ru",
    "uol.com.br", "bol.com.br", "terra.com.br", "ig.com.br", "globo.com",
    "globomail.com", "r7.com", "zipmail.com.br", "oi.com.br", "qq.com",
    "163.com", "fastmail.com", "hey.com",
})

ACCOUNT_PERSONAL = "pessoal"
ACCOUNT_CORPORATE = "corporativa"
ACCOUNT_EXTERNAL = "externa"      # domínio não pessoal, mas fora da lista corporativa
ACCOUNT_UNKNOWN = "desconhecida"


def normalize_email(email: str | None) -> str | None:
    if not email:
        return None
    email = email.strip().lower()
    return email if EMAIL_RE.match(email) else None


def email_domain(email: str | None) -> str | None:
    email = normalize_email(email)
    return email.split("@", 1)[1] if email else None


def classify_account(email: str | None, corporate_domains: list[str] | tuple[str, ...] = ()) -> str:
    """Classifica a conta usada na IA.

    - corporativa: domínio pertence à lista configurada de domínios da empresa
    - pessoal: domínio de provedor de e-mail gratuito/pessoal (Shadow AI)
    - externa: outro domínio (ex.: conta de terceiro/cliente)
    - desconhecida: não foi possível identificar o e-mail
    """
    domain = email_domain(email)
    if not domain:
        return ACCOUNT_UNKNOWN
    corp = {d.lower().strip() for d in corporate_domains if d}
    if any(domain == d or domain.endswith("." + d) for d in corp):
        return ACCOUNT_CORPORATE
    if domain in PERSONAL_EMAIL_DOMAINS:
        return ACCOUNT_PERSONAL
    return ACCOUNT_EXTERNAL
