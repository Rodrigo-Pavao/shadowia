/* Catálogo de serviços de IA monitorados (espelha shadowia_detector/providers.py). */
/* eslint-disable no-unused-vars */
const SHADOWIA_PROVIDERS = [
  { id: "chatgpt", hosts: ["chatgpt.com", "chat.openai.com"], account: "chatgpt" },
  { id: "claude", hosts: ["claude.ai"], account: "claude" },
  { id: "gemini", hosts: ["gemini.google.com", "aistudio.google.com"], account: "google" },
  { id: "copilot", hosts: ["copilot.microsoft.com", "m365.cloud.microsoft"], account: "dom" },
  { id: "perplexity", hosts: ["www.perplexity.ai", "perplexity.ai"], account: "perplexity" },
  { id: "deepseek", hosts: ["chat.deepseek.com"], account: "dom" },
  { id: "mistral", hosts: ["chat.mistral.ai"], account: "dom" },
  { id: "grok", hosts: ["grok.com"], account: "dom" },
  { id: "meta", hosts: ["www.meta.ai", "meta.ai"], account: "dom" },
  { id: "poe", hosts: ["poe.com"], account: "dom" },
  { id: "huggingchat", hosts: ["huggingface.co"], account: "dom" },
  { id: "qwen", hosts: ["chat.qwen.ai"], account: "dom" },
];

function shadowiaProviderFor(host) {
  host = (host || "").toLowerCase();
  return SHADOWIA_PROVIDERS.find((p) => p.hosts.some((h) => host === h || host.endsWith("." + h))) || null;
}
