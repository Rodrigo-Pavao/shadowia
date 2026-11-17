/* ShadowIADetector - service worker
 * Recebe eventos dos content scripts e os envia ao agente local. Quando o
 * agente está indisponível, os eventos ficam em fila e são reenviados.
 */
"use strict";

const DEFAULTS = { agentUrl: "http://127.0.0.1:8765", token: "", showWarnings: true };
const QUEUE_KEY = "shadowiaQueue";
const MAX_QUEUE = 1000;

async function settings() {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS))) };
}

function browserName() {
  const ua = navigator.userAgent;
  if (ua.includes("Edg/")) return "Edge";
  if (ua.includes("OPR/")) return "Opera";
  if (ua.includes("Brave")) return "Brave";
  return "Chrome";
}

async function post(payload) {
  const s = await settings();
  if (!s.token) throw new Error("token não configurado");
  const res = await fetch(`${s.agentUrl.replace(/\/$/, "")}/api/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-ShadowIA-Token": s.token },
    body: JSON.stringify(payload),
  });
  if (res.status === 400) return { ignored: true };       // evento inválido: não reenfileirar
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function enqueue(payload) {
  const { [QUEUE_KEY]: q = [] } = await chrome.storage.local.get(QUEUE_KEY);
  q.push(payload);
  await chrome.storage.local.set({ [QUEUE_KEY]: q.slice(-MAX_QUEUE) });
}

async function flushQueue() {
  const { [QUEUE_KEY]: q = [] } = await chrome.storage.local.get(QUEUE_KEY);
  if (!q.length) return 0;
  const remaining = [];
  for (let i = 0; i < q.length; i++) {
    try {
      await post(q[i]);
    } catch (_) {
      remaining.push(...q.slice(i));
      break;
    }
  }
  await chrome.storage.local.set({ [QUEUE_KEY]: remaining });
  return q.length - remaining.length;
}

async function handleEvent(payload) {
  payload = { ...payload, browser: browserName(), client_ts: new Date().toISOString() };
  try {
    const result = await post(payload);
    flushQueue();
    const s = await settings();
    return {
      ok: true,
      result: result && !result.ignored ? {
        level: result.level, score: result.score, warn: s.showWarnings,
        labels: [...new Set((result.findings || []).map((f) => f.category_label))],
      } : null,
    };
  } catch (err) {
    if (payload.type !== "visit") await enqueue(payload);
    return { ok: false, queued: true, error: String(err) };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.kind === "event") {
    handleEvent(msg.payload).then(sendResponse);
    return true;
  }
  if (msg?.kind === "profileEmail") {
    chrome.identity.getProfileUserInfo({ accountStatus: "ANY" }, (info) => sendResponse({ email: info?.email || null }));
    return true;
  }
  if (msg?.kind === "status") {
    (async () => {
      const s = await settings();
      const { [QUEUE_KEY]: q = [] } = await chrome.storage.local.get(QUEUE_KEY);
      let connected = false, detail = "";
      try {
        const r = await fetch(`${s.agentUrl.replace(/\/$/, "")}/api/ping`, { headers: { "X-ShadowIA-Token": s.token } });
        connected = r.ok;
        detail = r.ok ? (await r.json()).hostname : (r.status === 401 ? "token inválido" : `HTTP ${r.status}`);
      } catch (_) {
        detail = "agente não encontrado";
      }
      if (connected) await flushQueue();
      sendResponse({ connected, detail, queued: connected ? 0 : q.length });
    })();
    return true;
  }
  return false;
});

chrome.runtime.onInstalled.addListener(() => chrome.alarms.create("flush", { periodInMinutes: 1 }));
chrome.runtime.onStartup.addListener(() => chrome.alarms.create("flush", { periodInMinutes: 1 }));
chrome.alarms.onAlarm.addListener((a) => { if (a.name === "flush") flushQueue(); });
