/* ShadowIADetector - content script
 * Captura prompts enviados, uploads de arquivos e a conta (e-mail) logada no
 * serviço de IA, encaminhando os eventos ao service worker da extensão.
 */
(function () {
  "use strict";
  if (window.__shadowiaLoaded) return;
  window.__shadowiaLoaded = true;

  const provider = shadowiaProviderFor(location.hostname);
  if (!provider) return;
  if (provider.id === "huggingchat" && !location.pathname.startsWith("/chat")) return;

  const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/;
  const SEND_RE = /(send|enviar|submit|perguntar|ask|mandar)/i;
  let accountEmail = null;
  let lastSent = { text: "", at: 0 };

  // ------------------------------------------------------------------ conta
  async function fetchJson(url) {
    try {
      const r = await fetch(url, { credentials: "include" });
      return r.ok ? await r.json() : null;
    } catch (_) {
      return null;
    }
  }

  function emailFromDom() {
    // Atributos com e-mail (menus de perfil, avatares: "Conta do Google: Nome (email)").
    const attrs = ["aria-label", "title", "data-email", "alt"];
    for (const a of attrs) {
      for (const el of document.querySelectorAll(`[${a}*="@"]`)) {
        const m = (el.getAttribute(a) || "").match(EMAIL_RE);
        if (m) return m[0];
      }
    }
    // Texto de elementos pequenos típicos de menus de conta.
    const candidates = document.querySelectorAll(
      '[data-testid*="profile" i], [data-testid*="account" i], [class*="profile" i], [class*="account" i], [class*="user" i], nav, aside, header');
    for (const el of candidates) {
      const txt = (el.innerText || "").slice(0, 2000);
      const m = txt.match(EMAIL_RE);
      if (m) return m[0];
    }
    return null;
  }

  async function detectAccount() {
    let email = null;
    if (provider.account === "chatgpt") {
      const s = await fetchJson("/api/auth/session");
      email = s?.user?.email || null;
    } else if (provider.account === "claude") {
      const a = await fetchJson("/api/account");
      email = a?.email_address || a?.account?.email_address || null;
    } else if (provider.account === "perplexity") {
      const s = await fetchJson("/api/auth/session");
      email = s?.user?.email || null;
    }
    email = email || emailFromDom();
    if (!email && provider.account === "google") {
      // Serviços Google usam a conta do perfil quando o menu ainda não carregou.
      const r = await chrome.runtime.sendMessage({ kind: "profileEmail" }).catch(() => null);
      email = r?.email || null;
    }
    if (email) accountEmail = email.toLowerCase();
    return accountEmail;
  }

  // ------------------------------------------------------------ composição
  function isEditor(el) {
    if (!el || el.nodeType !== 1) return false;
    if (el.tagName === "TEXTAREA") return true;
    if (el.tagName === "INPUT" && /^(text|search)$/i.test(el.type)) return true;
    return el.isContentEditable;
  }

  function editorRoot(el) {
    while (el && el.parentElement && el.parentElement.isContentEditable) el = el.parentElement;
    return el;
  }

  function textOf(el) {
    if (!el) return "";
    if (el.tagName === "TEXTAREA" || el.tagName === "INPUT") return el.value || "";
    return el.innerText || el.textContent || "";
  }

  function findComposer() {
    const a = document.activeElement;
    if (isEditor(a)) return editorRoot(a);
    const list = [...document.querySelectorAll('textarea, [contenteditable="true"], [contenteditable=""]')]
      .filter((e) => e.offsetParent !== null);
    return list.length ? editorRoot(list[list.length - 1]) : null;
  }

  async function sendPrompt(text) {
    text = (text || "").trim();
    if (!text) return;
    const now = Date.now();
    if (text === lastSent.text && now - lastSent.at < 3000) return; // evita duplicidade Enter+click
    lastSent = { text, at: now };
    if (!accountEmail) await detectAccount();
    emit({ type: "prompt", prompt: text });
  }

  function emit(payload) {
    payload = { ...payload, provider: provider.id, url: location.href, account_email: accountEmail };
    chrome.runtime.sendMessage({ kind: "event", payload }).then((res) => {
      if (res && res.result) maybeWarn(res.result);
    }).catch(() => { /* service worker indisponível */ });
  }

  // Enter (sem Shift) dentro do editor
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.shiftKey || e.isComposing || e.altKey) return;
    const el = e.target;
    if (!isEditor(el)) return;
    sendPrompt(textOf(editorRoot(el)));
  }, true);

  // Clique no botão de enviar
  document.addEventListener("click", (e) => {
    const btn = e.target.closest && e.target.closest('button, [role="button"]');
    if (!btn) return;
    const label = [btn.getAttribute("aria-label"), btn.getAttribute("data-testid"), btn.getAttribute("title"),
      btn.id, btn.type === "submit" ? "submit" : ""].join(" ");
    if (!SEND_RE.test(label)) return;
    sendPrompt(textOf(findComposer()));
  }, true);

  // ---------------------------------------------------------------- uploads
  function reportFiles(files) {
    for (const f of files || []) {
      emit({ type: "upload", file_name: f.name, file_size: f.size });
    }
  }
  document.addEventListener("change", (e) => {
    if (e.target && e.target.tagName === "INPUT" && e.target.type === "file") reportFiles(e.target.files);
  }, true);
  document.addEventListener("drop", (e) => {
    if (e.dataTransfer && e.dataTransfer.files.length) reportFiles(e.dataTransfer.files);
  }, true);
  document.addEventListener("paste", (e) => {
    const files = e.clipboardData && e.clipboardData.files;
    if (files && files.length) reportFiles(files);
  }, true);

  // ------------------------------------------------------- aviso ao usuário
  function maybeWarn(result) {
    if (!result.warn || !["alto", "critico"].includes(result.level)) return;
    let stack = document.getElementById("shadowia-toasts");
    if (!stack) {
      stack = document.createElement("div");
      stack.id = "shadowia-toasts";
      stack.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;display:flex;" +
        "flex-direction:column;gap:8px;max-width:360px;";
      document.body.appendChild(stack);
    }
    const box = document.createElement("div");
    box.setAttribute("role", "alert");
    box.style.cssText = "padding:12px 14px;" +
      "border-radius:10px;background:#1a1a19;color:#f0efec;font:13px/1.4 system-ui,sans-serif;" +
      "box-shadow:0 8px 28px rgba(0,0,0,.35);border-left:4px solid " + (result.level === "critico" ? "#d03b3b" : "#ec835a");
    const title = document.createElement("strong");
    title.textContent = `ShadowIADetector — risco ${result.level === "critico" ? "crítico" : "alto"}`;
    const msg = document.createElement("div");
    msg.textContent = "Este conteúdo pode violar a política de uso de IA: " + (result.labels || []).join(", ") +
      ". O evento foi registrado.";
    box.append(title, msg);
    stack.appendChild(box);
    setTimeout(() => box.remove(), 9000);
  }

  // Detecta a conta logo após o carregamento e periodicamente (SPA).
  setTimeout(async () => {
    await detectAccount();
    emit({ type: "visit" });
  }, 2500);
  setInterval(() => { if (!accountEmail) detectAccount(); }, 60000);
})();
