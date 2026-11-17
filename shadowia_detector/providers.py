"""Catálogo de serviços de IA generativa monitorados."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    vendor: str
    web_domains: tuple[str, ...]
    api_domains: tuple[str, ...] = field(default_factory=tuple)


PROVIDERS: tuple[Provider, ...] = (
    Provider("chatgpt", "ChatGPT", "OpenAI",
             ("chatgpt.com", "chat.openai.com"),
             ("api.openai.com",)),
    Provider("claude", "Claude", "Anthropic",
             ("claude.ai",),
             ("api.anthropic.com",)),
    Provider("gemini", "Gemini", "Google",
             ("gemini.google.com", "aistudio.google.com"),
             ("generativelanguage.googleapis.com",)),
    Provider("copilot", "Microsoft Copilot", "Microsoft",
             ("copilot.microsoft.com", "m365.cloud.microsoft"),
             ()),
    Provider("perplexity", "Perplexity", "Perplexity AI",
             ("www.perplexity.ai", "perplexity.ai"),
             ("api.perplexity.ai",)),
    Provider("deepseek", "DeepSeek", "DeepSeek",
             ("chat.deepseek.com",),
             ("api.deepseek.com",)),
    Provider("mistral", "Le Chat", "Mistral AI",
             ("chat.mistral.ai",),
             ("api.mistral.ai",)),
    Provider("grok", "Grok", "xAI",
             ("grok.com",),
             ("api.x.ai",)),
    Provider("meta", "Meta AI", "Meta",
             ("www.meta.ai", "meta.ai"),
             ()),
    Provider("poe", "Poe", "Quora",
             ("poe.com",),
             ()),
    Provider("huggingchat", "HuggingChat", "Hugging Face",
             ("huggingface.co",),
             ("api-inference.huggingface.co",)),
    Provider("qwen", "Qwen Chat", "Alibaba",
             ("chat.qwen.ai",),
             ("dashscope.aliyuncs.com",)),
)

PROVIDERS_BY_ID = {p.id: p for p in PROVIDERS}


def provider_for_host(host: str) -> Provider | None:
    """Retorna o provedor associado a um hostname (web ou API)."""
    host = (host or "").lower().strip(".")
    for p in PROVIDERS:
        for d in p.web_domains + p.api_domains:
            if host == d or host.endswith("." + d):
                return p
    return None


def provider_name(provider_id: str) -> str:
    p = PROVIDERS_BY_ID.get(provider_id)
    return p.name if p else provider_id
