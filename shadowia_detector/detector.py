"""Motor de detecção de prompts suspeitos e perigosos.

Analisa o texto enviado a uma IA e identifica:
  * vazamento de credenciais e segredos
  * dados pessoais sensíveis (LGPD): CPF, CNPJ, cartão de crédito, etc.
  * informação corporativa confidencial e código-fonte
  * tentativas de jailbreak / prompt injection
  * intenção maliciosa (malware, phishing, fraude, ataques)
  * volume anômalo de dados (possível exfiltração)

Cada achado tem severidade e peso; o somatório gera um score de 0 a 100.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Iterable

SEVERITY_WEIGHT = {"baixa": 10, "media": 30, "alta": 55, "critica": 85}
LEVELS = ((80, "critico"), (50, "alto"), (25, "medio"), (1, "baixo"), (0, "nenhum"))

CATEGORY_LABELS = {
    "credenciais": "Credenciais e segredos",
    "dados_pessoais": "Dados pessoais (LGPD)",
    "dados_financeiros": "Dados financeiros",
    "confidencial": "Informação corporativa confidencial",
    "codigo_fonte": "Código-fonte",
    "jailbreak": "Jailbreak / Prompt injection",
    "malicioso": "Intenção maliciosa / Ataque",
    "fraude": "Fraude / Engenharia social",
    "exfiltracao": "Volume anômalo de dados",
    "upload": "Upload de arquivo",
    "personalizada": "Regra personalizada",
}


# --------------------------------------------------------------------------- #
# Validadores
# --------------------------------------------------------------------------- #

def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def valid_cpf(raw: str) -> bool:
    d = _digits(raw)
    if len(d) != 11 or d == d[0] * 11:
        return False
    for size in (9, 10):
        total = sum(int(d[i]) * (size + 1 - i) for i in range(size))
        check = (total * 10) % 11 % 10
        if check != int(d[size]):
            return False
    return True


def valid_cnpj(raw: str) -> bool:
    d = _digits(raw)
    if len(d) != 14 or d == d[0] * 14:
        return False
    weights1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    weights2 = [6] + weights1
    for weights, pos in ((weights1, 12), (weights2, 13)):
        total = sum(int(d[i]) * w for i, w in enumerate(weights))
        rest = total % 11
        check = 0 if rest < 2 else 11 - rest
        if check != int(d[pos]):
            return False
    return True


def valid_luhn(raw: str) -> bool:
    d = _digits(raw)
    if not 13 <= len(d) <= 19 or d == d[0] * len(d):
        return False
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def valid_card(raw: str) -> bool:
    """Luhn + prefixo de bandeira conhecida, descartando CNPJs válidos."""
    d = _digits(raw)
    if not valid_luhn(d) or d[0] not in "3456":
        return False
    return not (len(d) == 14 and valid_cnpj(d))


# --------------------------------------------------------------------------- #
# Regras
# --------------------------------------------------------------------------- #

@dataclass
class Rule:
    id: str
    category: str
    severity: str
    description: str
    pattern: re.Pattern
    validator: Callable[[str], bool] | None = None
    redact: bool = False          # mascarar o trecho ao armazenar
    on_normalized: bool = False   # aplicar no texto normalizado (sem acento/minúsculo)
    min_matches: int = 1


@dataclass
class Finding:
    rule_id: str
    category: str
    severity: str
    description: str
    count: int
    samples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "category": self.category,
            "category_label": CATEGORY_LABELS.get(self.category, self.category),
            "severity": self.severity,
            "description": self.description,
            "count": self.count,
            "samples": self.samples,
        }


@dataclass
class AnalysisResult:
    score: int
    level: str
    findings: list[Finding]
    redacted_text: str

    @property
    def categories(self) -> list[str]:
        return sorted({f.category for f in self.findings})

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "level": self.level,
            "categories": self.categories,
            "findings": [f.to_dict() for f in self.findings],
        }


def _r(p: str, flags: int = re.IGNORECASE) -> re.Pattern:
    return re.compile(p, flags)


def _kw(words: Iterable[str]) -> re.Pattern:
    """Cria regex de palavras/frases (para texto normalizado, sem acentos)."""
    alts = "|".join(sorted((re.escape(w).replace(r"\ ", r"\s+") for w in words), key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alts})(?![a-z0-9])")


BUILTIN_RULES: list[Rule] = [
    # ----------------------------- credenciais ----------------------------- #
    Rule("cred_private_key", "credenciais", "critica", "Chave privada (PEM/SSH)",
         _r(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----"), redact=True),
    Rule("cred_aws_key", "credenciais", "critica", "Chave de acesso AWS",
         _r(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", 0), redact=True),
    Rule("cred_github_token", "credenciais", "critica", "Token do GitHub",
         _r(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b", 0), redact=True),
    Rule("cred_ai_api_key", "credenciais", "critica", "Chave de API de IA (OpenAI/Anthropic/Google)",
         _r(r"\b(?:sk-ant-[A-Za-z0-9_\-]{20,}|sk-(?:proj-)?[A-Za-z0-9_\-]{32,}|AIza[0-9A-Za-z_\-]{35})\b", 0),
         redact=True),
    Rule("cred_slack_token", "credenciais", "alta", "Token do Slack",
         _r(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b", 0), redact=True),
    Rule("cred_jwt", "credenciais", "alta", "Token JWT",
         _r(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b", 0), redact=True),
    Rule("cred_conn_string", "credenciais", "critica", "String de conexão com senha",
         _r(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|mssql|sqlserver)://[^\s:/@]+:[^\s@/]+@[^\s]+"),
         redact=True),
    Rule("cred_password_assignment", "credenciais", "alta", "Senha ou segredo explícito",
         _r(r"(?:\b(?:password|passwd|pwd|senha|secret|segredo|api[_\-]?key|access[_\-]?token|client[_\-]?secret)\b"
            r"\s*[:=]\s*[\"']?[^\s\"']{6,})"),
         redact=True),

    # --------------------------- dados pessoais ---------------------------- #
    Rule("pii_cpf", "dados_pessoais", "alta", "CPF válido",
         _r(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)", 0), validator=valid_cpf, redact=True),
    Rule("pii_cnpj", "dados_pessoais", "media", "CNPJ válido",
         _r(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)", 0), validator=valid_cnpj, redact=True),
    Rule("pii_rg", "dados_pessoais", "media", "Número de RG",
         _r(r"\bRG\s*[:nº°.]*\s*\d{1,2}\.?\d{3}\.?\d{3}-?[\dxX]\b"), redact=True),
    Rule("pii_email_list", "dados_pessoais", "media", "Lista de e-mails (3 ou mais)",
         _r(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), min_matches=3, redact=True),
    Rule("pii_phone_list", "dados_pessoais", "media", "Lista de telefones (3 ou mais)",
         _r(r"(?<!\d)(?:\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}[\s\-]?\d{4}(?!\d)", 0), min_matches=3, redact=True),
    Rule("pii_health", "dados_pessoais", "alta", "Dados de saúde (dado sensível LGPD)",
         _kw(["prontuario", "diagnostico do paciente", "cid-10", "laudo medico", "exame de hiv",
              "historico medico", "medical record", "patient diagnosis"]), on_normalized=True),

    # -------------------------- dados financeiros -------------------------- #
    Rule("fin_credit_card", "dados_financeiros", "critica", "Número de cartão de crédito",
         _r(r"(?<!\d)(?:\d[ \-]?){13,19}(?!\d)", 0), validator=valid_card, redact=True),
    Rule("fin_bank_account", "dados_financeiros", "media", "Dados bancários (agência/conta/PIX)",
         _kw(["agencia e conta", "conta corrente", "chave pix", "numero da conta", "iban", "swift code"]),
         on_normalized=True),

    # ---------------------------- confidencial ----------------------------- #
    Rule("conf_markers", "confidencial", "alta", "Marcação de documento confidencial",
         _kw(["confidencial", "estritamente confidencial", "uso interno", "uso restrito", "nao divulgar",
              "sigiloso", "segredo industrial", "confidential", "internal use only", "do not distribute",
              "proprietary and confidential", "nda"]), on_normalized=True),
    Rule("conf_business", "confidencial", "media", "Informação estratégica/financeira interna",
         _kw(["balanco nao publicado", "resultado trimestral", "fusao e aquisicao", "plano estrategico",
              "folha de pagamento", "planilha de salarios", "salario dos funcionarios", "salarios dos funcionarios", "lista de clientes",
              "base de clientes", "contrato com o cliente", "proposta comercial", "roadmap interno",
              "due diligence", "earnings before release", "customer list", "payroll"]), on_normalized=True),

    # ---------------------------- jailbreak -------------------------------- #
    Rule("jb_ignore_instructions", "jailbreak", "alta", "Tentativa de ignorar instruções do sistema",
         _kw(["ignore previous instructions", "ignore all previous instructions", "ignore your instructions",
              "disregard all prior", "forget your rules", "ignore suas instrucoes", "ignore as instrucoes anteriores",
              "esqueca suas regras", "desconsidere as instrucoes", "reveal your system prompt",
              "mostre seu prompt de sistema"]), on_normalized=True),
    Rule("jb_persona", "jailbreak", "alta", "Persona de jailbreak (DAN, modo sem restrições)",
         _kw(["do anything now", "dan mode", "developer mode enabled", "jailbreak", "jailbroken",
              "modo desenvolvedor", "sem restricoes", "sem filtros", "without any restrictions",
              "no ethical guidelines", "sem limites eticos", "evil confidant", "aim mode",
              "unfiltered ai"]), on_normalized=True),

    # ---------------------------- malicioso -------------------------------- #
    Rule("mal_malware", "malicioso", "critica", "Criação de malware",
         _kw(["ransomware", "keylogger", "trojan", "cavalo de troia", "rootkit", "botnet", "worm que se espalhe",
              "criar um virus", "crie um virus", "create a virus", "write malware", "escreva um malware",
              "crie um malware", "malware indetectavel", "fud payload", "reverse shell", "shellcode",
              "stealer", "infostealer", "rat (remote access trojan)", "cryptojacker"]), on_normalized=True),
    Rule("mal_evasion", "malicioso", "critica", "Evasão de defesas / antivírus",
         _kw(["bypass antivirus", "burlar antivirus", "evadir antivirus", "evade edr", "bypass edr",
              "desativar o defender", "disable windows defender", "ofuscar payload", "obfuscate payload",
              "bypass amsi", "indetectavel por antivirus"]), on_normalized=True),
    Rule("mal_attack", "malicioso", "alta", "Ataques a sistemas/redes",
         _kw(["ataque ddos", "ddos attack", "derrubar o site", "sql injection para invadir", "invadir o sistema",
              "hackear a conta", "hack into", "brute force na senha", "quebrar senha", "crack password",
              "exploit para", "zero-day", "escalar privilegio", "privilege escalation", "roubar credenciais",
              "steal credentials", "dump de senhas", "mimikatz", "pass the hash", "sequestro de sessao"]),
         on_normalized=True),
    Rule("mal_weapons", "malicioso", "critica", "Armas, explosivos ou substâncias perigosas",
         _kw(["fabricar bomba", "como fazer uma bomba", "how to make a bomb", "explosivo caseiro",
              "fabricar explosivo", "arma quimica", "chemical weapon", "arma biologica", "bioweapon",
              "sintetizar droga", "synthesize meth", "fazer metanfetamina"]), on_normalized=True),

    # ------------------------------ fraude --------------------------------- #
    Rule("fraud_phishing", "fraude", "alta", "Phishing / engenharia social",
         _kw(["email de phishing", "phishing email", "pagina de phishing", "phishing page", "pagina falsa de login",
              "fake login page", "se passar pelo banco", "impersonate the bank", "golpe do pix",
              "engenharia social para", "clonar whatsapp", "smishing", "vishing"]), on_normalized=True),
    Rule("fraud_documents", "fraude", "alta", "Falsificação de documentos/fraude",
         _kw(["documento falso", "falsificar documento", "falsificar assinatura", "atestado falso",
              "nota fiscal falsa", "gerar cpf valido", "comprovante falso", "fake id", "forge a signature",
              "lavagem de dinheiro", "money laundering", "sonegar imposto", "burlar auditoria",
              "manipular balanco"]), on_normalized=True),
]

CODE_HINTS = re.compile(
    r"(^\s*(?:def |class |import |from \S+ import |function |const |let |var |public |private |"
    r"package |#include|using |SELECT |INSERT |UPDATE |CREATE TABLE)|[{};]\s*$|=>|\)\s*\{)",
    re.MULTILINE | re.IGNORECASE,
)


def normalize(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _mask(s: str) -> str:
    s = s.strip()
    if len(s) <= 6:
        return "*" * len(s)
    return s[:3] + "*" * (len(s) - 5) + s[-2:]


def score_to_level(score: int) -> str:
    for threshold, name in LEVELS:
        if score >= threshold:
            return name
    return "nenhum"


class PromptAnalyzer:
    def __init__(self, custom_keywords: Iterable[str] = (), large_prompt_chars: int = 8000,
                 code_min_lines: int = 8):
        self.rules = list(BUILTIN_RULES)
        kws = [normalize(k).strip() for k in custom_keywords if k and k.strip()]
        if kws:
            self.rules.append(Rule("custom_keywords", "personalizada", "alta",
                                   "Palavra-chave monitorada pela organização", _kw(kws), on_normalized=True))
        self.large_prompt_chars = large_prompt_chars
        self.code_min_lines = code_min_lines

    def analyze(self, text: str) -> AnalysisResult:
        text = text or ""
        norm = normalize(text)
        findings: list[Finding] = []
        redact_spans: list[tuple[int, int]] = []

        for rule in self.rules:
            target = norm if rule.on_normalized else text
            matches = []
            for m in rule.pattern.finditer(target):
                if rule.validator and not rule.validator(m.group(0)):
                    continue
                matches.append(m)
            if len(matches) < rule.min_matches:
                continue
            samples = []
            for m in matches[:3]:
                frag = m.group(0)
                samples.append(_mask(frag) if rule.redact else frag.strip()[:80])
            if rule.redact and not rule.on_normalized:
                redact_spans.extend(m.span() for m in matches)
            findings.append(Finding(rule.id, rule.category, rule.severity, rule.description,
                                    len(matches), samples))

        # Código-fonte (heurística por linhas)
        code_lines = len(CODE_HINTS.findall(text))
        if code_lines >= self.code_min_lines:
            findings.append(Finding("code_block", "codigo_fonte", "media",
                                    "Trecho extenso de código-fonte", code_lines,
                                    [f"{code_lines} linhas com padrão de código"]))

        # Volume anômalo
        if len(text) >= self.large_prompt_chars:
            sev = "alta" if len(text) >= self.large_prompt_chars * 4 else "media"
            findings.append(Finding("large_prompt", "exfiltracao", sev,
                                    "Prompt muito extenso (possível colagem de documento/base de dados)",
                                    1, [f"{len(text)} caracteres"]))

        score = self._score(findings)
        return AnalysisResult(score, score_to_level(score), findings, self._redact(text, redact_spans))

    @staticmethod
    def _score(findings: list[Finding]) -> int:
        if not findings:
            return 0
        weights = sorted((SEVERITY_WEIGHT[f.severity] for f in findings), reverse=True)
        # Maior achado conta integralmente; os demais com retorno decrescente.
        score = weights[0] + sum(w * 0.4 for w in weights[1:])
        return min(100, int(round(score)))

    @staticmethod
    def _redact(text: str, spans: list[tuple[int, int]]) -> str:
        if not spans:
            return text
        out, last = [], 0
        for start, end in sorted(spans):
            if start < last:
                start = last
            if start >= end:
                continue
            out.append(text[last:start])
            out.append("[REDIGIDO]")
            last = end
        out.append(text[last:])
        return "".join(out)
