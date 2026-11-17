"use strict";
const $ = (id) => document.getElementById(id);

async function load() {
  const s = await chrome.storage.local.get(["agentUrl", "token", "showWarnings"]);
  $("agentUrl").value = s.agentUrl || "http://127.0.0.1:8765";
  $("token").value = s.token || "";
  $("showWarnings").checked = s.showWarnings !== false;
  checkStatus();
}

async function checkStatus() {
  const box = $("status");
  box.className = "";
  $("status-text").textContent = "Verificando…";
  const r = await chrome.runtime.sendMessage({ kind: "status" });
  box.className = r.connected ? "ok" : "err";
  $("status-text").textContent = r.connected
    ? `Conectado ao agente (${r.detail})`
    : `Desconectado: ${r.detail}${r.queued ? ` · ${r.queued} evento(s) na fila` : ""}`;
}

$("save").addEventListener("click", async () => {
  await chrome.storage.local.set({
    agentUrl: $("agentUrl").value.trim().replace(/\/$/, "") || "http://127.0.0.1:8765",
    token: $("token").value.trim(),
    showWarnings: $("showWarnings").checked,
  });
  checkStatus();
});

$("open").addEventListener("click", () => chrome.tabs.create({ url: $("agentUrl").value.trim() || "http://127.0.0.1:8765" }));

load();
