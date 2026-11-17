/* ShadowIADetector - dashboard */
(function () {
  "use strict";
  const { esc, fmt } = window.Charts;
  const $ = (s) => document.querySelector(s);
  const TZ = new Date().getTimezoneOffset();

  const ACCOUNT_SERIES = [
    { key: "corporate", type: "corporativa", label: "Corporativa", color: "var(--series-1)" },
    { key: "personal", type: "pessoal", label: "Pessoal", color: "var(--series-2)" },
    { key: "external", type: "externa", label: "Externa", color: "var(--series-3)" },
    { key: "unknown", type: "desconhecida", label: "Não identificada", color: "var(--series-4)" },
  ];
  const LEVELS = [
    { key: "critico", label: "Crítico", color: "var(--status-critical)", icon: "!!" },
    { key: "alto", label: "Alto", color: "var(--status-serious)", icon: "!" },
    { key: "medio", label: "Médio", color: "var(--status-warning)", icon: "•", ink: "#0b0b0b" },
    { key: "baixo", label: "Baixo", color: "var(--status-good)", icon: "✓" },
  ];
  const LEVEL_LABEL = { critico: "Crítico", alto: "Alto", medio: "Médio", baixo: "Baixo", nenhum: "Sem risco" };
  const STATUS_LABEL = { novo: "Novo", em_analise: "Em análise", resolvido: "Resolvido", falso_positivo: "Falso positivo" };
  const TYPE_LABEL = { prompt: "Prompt", upload: "Upload", network: "Rede", visit: "Acesso" };

  const state = { meta: null, stats: null, tab: "overview", evOffset: 0 };

  // ------------------------------------------------------------------ API
  async function api(path, opts = {}) {
    const res = await fetch(path, {
      ...opts,
      headers: { "Content-Type": "application/json", "X-ShadowIA-Dashboard": "1", ...(opts.headers || {}) },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }
  const days = () => Number($("#period").value);

  // --------------------------------------------------------------- helpers
  const providerName = (id) => (state.meta?.providers.find((p) => p.id === id) || {}).name || id;
  const catLabel = (c) => (c === "conta_pessoal" ? "Conta pessoal" : state.meta?.categories[c] || c);
  const dt = (iso) => (iso ? new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "");
  const levelPill = (lv, score) => `<span class="pill lv-${esc(lv)}"><span class="dot"></span>${esc(LEVEL_LABEL[lv] || lv)}${score != null ? ` · ${score}` : ""}</span>`;
  const accPill = (t) => `<span class="pill acc-${esc(t)}">${esc(t)}</span>`;
  const catPills = (cats) => (cats || []).map((c) => `<span class="pill cat">${esc(catLabel(c))}</span>`).join(" ");
  const snippet = (ev) => ev.type === "upload" ? `📎 ${esc(ev.file_name)}` : ev.type === "network" ? `🌐 ${esc(ev.process_name)} → ${esc(ev.remote_addr)}` : esc((ev.prompt_text || "").slice(0, 200));

  // ------------------------------------------------------------------ tabs
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => selectTab(b.dataset.tab)));
  function selectTab(tab) {
    state.tab = tab;
    document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
    document.querySelectorAll(".tab-panel").forEach((p) => (p.hidden = p.id !== "tab-" + tab));
    try { localStorage.setItem("shadowia.tab", tab); } catch (_) { /* ignore */ }
    refresh();
  }

  // -------------------------------------------------------------- overview
  async function loadOverview() {
    const s = (state.stats = await api(`/api/stats?days=${days()}&tz=${TZ}`));
    const t = s.totals;
    const interactions = t.interactions || 0;
    $("#kpis").innerHTML = [
      kpi("Interações com IA", fmt(interactions), `${fmt(t.prompts)} prompts · ${fmt(t.uploads)} uploads`),
      kpi("Uso com conta pessoal", `${t.personal_share.toLocaleString("pt-BR")}%`, `${fmt(t.personal_interactions)} interações · ${fmt(t.personal_accounts)} contas`, t.personal_share >= 20),
      kpi("Alertas novos", fmt(t.alerts_open), `${fmt(t.alerts_total)} no período`, t.alerts_open > 0),
      kpi("Alertas críticos", fmt(t.alerts_critical), `${fmt(t.alerts_high)} de nível alto`, t.alerts_critical > 0),
      kpi("Contas identificadas", fmt(t.accounts), `${fmt(t.machine_users)} usuário(s) da máquina`),
      kpi("Serviços de IA usados", fmt(t.providers), `${fmt(t.network)} conexões de rede`),
    ].join("");
    $("#badge-alerts").hidden = !t.alerts_open;
    $("#badge-alerts").textContent = t.alerts_open;

    // Série temporal empilhada por tipo de conta
    Charts.legend($("#legend-timeline"), ACCOUNT_SERIES);
    Charts.stackedColumns($("#chart-timeline"), s.timeline, ACCOUNT_SERIES, {
      aria: "Interações por dia por tipo de conta",
      xLabel: (d) => d.day.slice(8, 10) + "/" + d.day.slice(5, 7),
      tipTitle: (d) => new Date(d.day + "T12:00").toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "short" }),
      extraTip: (d) => [{ label: "Eventos de alto risco", value: fmt(d.high_risk) }],
    });
    $("#table-timeline").innerHTML = table(["Dia", ...ACCOUNT_SERIES.map((x) => x.label), "Alto risco"],
      s.timeline.filter((d) => d.prompts + d.uploads).map((d) => [d.day, ...ACCOUNT_SERIES.map((x) => d[x.key]), d.high_risk]), [1, 2, 3, 4, 5]);

    // Provedores
    Charts.hbar($("#chart-providers"), s.by_provider.filter((p) => p.prompts + p.uploads > 0).map((p) => ({
      label: providerName(p.provider), value: p.prompts + p.uploads,
      tip: [
        { label: "Prompts", value: fmt(p.prompts) }, { label: "Uploads", value: fmt(p.uploads) },
        { label: "Com conta pessoal", value: fmt(p.personal) }, { label: "Alto risco", value: fmt(p.high_risk) },
        { label: "Contas", value: fmt(p.accounts) },
      ],
    })), { aria: "Uso por serviço de IA" });

    // Tipos de conta
    const accMap = Object.fromEntries(s.by_account_type.map((r) => [r.account_type, r.interactions]));
    Charts.hbar($("#chart-account-types"), ACCOUNT_SERIES.map((x) => ({
      label: x.label, value: accMap[x.type] || 0, color: x.color,
      tip: [{ label: "Interações", value: fmt(accMap[x.type] || 0) },
        { label: "Participação", value: interactions ? `${Math.round(((accMap[x.type] || 0) / interactions) * 100)}%` : "0%" }],
    })), { aria: "Interações por tipo de conta" });

    // Níveis
    $("#levels").innerHTML = LEVELS.map((l) => `
      <div class="level"><span class="ico" style="background:${l.color};color:${l.ink || "#fff"}" aria-hidden="true">${l.icon}</span>
      <div><div class="num">${fmt(s.by_level[l.key])}</div><div class="lbl">${l.label}</div></div></div>`).join("");

    // Categorias
    Charts.hbar($("#chart-categories"), s.by_category.map((c) => ({ label: catLabel(c.category), value: c.count })),
      { aria: "Categorias de risco", valueLabel: "Eventos" });

    // Horas
    Charts.columns($("#chart-hours"), s.by_hour.map((h) => ({ label: String(h.hour).padStart(2, "0") + "h", value: h.prompts })),
      { aria: "Prompts por hora", valueLabel: "Prompts" });

    // Top risco
    $("#top-risky").innerHTML = s.top_risky.length ? `<div class="table-wrap"><table class="data">
      <tr><th>Quando</th><th>Serviço</th><th>Conta</th><th>Risco</th><th>Categorias</th><th>Conteúdo</th></tr>
      ${s.top_risky.map((e) => `<tr class="clickable" data-event="${e.id}">
        <td>${dt(e.ts)}</td><td>${esc(providerName(e.provider))}</td>
        <td>${esc(e.account_email || "—")} ${accPill(e.account_type)}</td>
        <td>${levelPill(e.level, e.score)}</td><td>${catPills(e.categories)}</td>
        <td><div class="prompt-snippet">${snippet(e)}</div></td></tr>`).join("")}</table></div>`
      : `<div class="empty">Nenhum evento de risco no período 🎉</div>`;

    // Rede
    $("#network").innerHTML = s.network_processes.length
      ? table(["Serviço", "Processo", "Ocorrências", "Última vez"], s.network_processes.map((n) => [providerName(n.provider), n.process_name, n.sightings, dt(n.last_seen)]), [2])
      : `<div class="empty">${state.meta?.network_monitor_available ? "Nenhuma conexão detectada no período" : "Monitor de rede indisponível — instale o pacote psutil para ativá-lo"}</div>`;
  }

  function kpi(label, value, foot, attention) {
    return `<div class="kpi${attention ? " attention" : ""}"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="foot">${esc(foot)}</div></div>`;
  }

  function table(headers, rows, numCols = []) {
    if (!rows.length) return `<div class="empty">Sem dados</div>`;
    return `<table class="data"><tr>${headers.map((h, i) => `<th class="${numCols.includes(i) ? "num" : ""}">${esc(h)}</th>`).join("")}</tr>
      ${rows.map((r) => `<tr>${r.map((c, i) => `<td class="${numCols.includes(i) ? "num" : ""}">${esc(numCols.includes(i) ? fmt(c) : c)}</td>`).join("")}</tr>`).join("")}</table>`;
  }

  // ---------------------------------------------------------------- alerts
  async function loadAlerts() {
    const status = $("#alert-status").value;
    const alerts = await api(`/api/alerts?days=${days()}${status ? `&status=${status}` : ""}`);
    $("#export-alerts").href = `/api/export/alerts.csv?days=${days()}`;
    $("#alerts-list").innerHTML = alerts.length ? alerts.map((a) => `
      <div class="alert-item lv-${esc(a.level)}">
        <div>${levelPill(a.level, a.score)}</div>
        <div>
          <div class="summary">${esc(a.summary)}</div>
          <div class="meta">${dt(a.ts)} · usuário ${esc(a.machine_user || "?")} · <a href="#" data-event="${a.event_id}">ver detalhes</a></div>
          <div class="cats">${catPills(a.categories)}</div>
          ${a.prompt_text ? `<div class="prompt-snippet" style="margin-top:6px">${esc(a.prompt_text.slice(0, 220))}</div>` : ""}
        </div>
        <div class="alert-actions">
          <select data-alert="${a.id}" aria-label="Status do alerta">
            ${Object.entries(STATUS_LABEL).map(([k, v]) => `<option value="${k}" ${k === a.status ? "selected" : ""}>${v}</option>`).join("")}
          </select>
        </div>
      </div>`).join("") : `<div class="empty">Nenhum alerta ${status ? `com status “${esc(STATUS_LABEL[status])}”` : ""} no período</div>`;
  }
  $("#alert-status").addEventListener("change", loadAlerts);
  $("#alerts-list").addEventListener("change", async (e) => {
    const id = e.target.dataset.alert;
    if (!id) return;
    await api(`/api/alerts/${id}`, { method: "PATCH", body: JSON.stringify({ status: e.target.value }) });
    loadAlerts();
  });

  // ---------------------------------------------------------------- events
  function evQuery() {
    const p = new URLSearchParams({ days: days(), limit: 100, offset: state.evOffset });
    for (const [id, key] of [["#ev-search", "q"], ["#ev-provider", "provider"], ["#ev-account", "account_type"], ["#ev-type", "type"], ["#ev-score", "min_score"]]) {
      const v = $(id).value;
      if (v && v !== "0") p.set(key, v);
    }
    return p.toString();
  }
  async function loadEvents(append = false) {
    if (!append) state.evOffset = 0;
    const rows = await api(`/api/events?${evQuery()}`);
    const head = `<tr><th>Quando</th><th>Tipo</th><th>Serviço</th><th>Conta</th><th>Usuário</th><th>Risco</th><th>Conteúdo</th></tr>`;
    const body = rows.map((e) => `<tr class="clickable" data-event="${e.id}">
      <td>${dt(e.ts)}</td><td>${esc(TYPE_LABEL[e.type] || e.type)}</td><td>${esc(providerName(e.provider))}</td>
      <td>${esc(e.account_email || "—")} ${accPill(e.account_type)}</td><td>${esc(e.machine_user || "")}</td>
      <td>${levelPill(e.level, e.score)}</td><td><div class="prompt-snippet">${snippet(e)}</div></td></tr>`).join("");
    const tableEl = $("#events-table");
    if (append) tableEl.insertAdjacentHTML("beforeend", body);
    else tableEl.innerHTML = head + (body || `<tr><td colspan="7" class="empty">Nenhum evento encontrado</td></tr>`);
    $("#ev-more").hidden = rows.length < 100;
    $("#export-events").href = `/api/export/events.csv?days=${days()}`;
  }
  let searchTimer;
  $("#ev-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadEvents(), 300); });
  ["#ev-provider", "#ev-account", "#ev-type", "#ev-score"].forEach((s) => $(s).addEventListener("change", () => loadEvents()));
  $("#ev-more").addEventListener("click", () => { state.evOffset += 100; loadEvents(true); });

  // -------------------------------------------------------------- accounts
  async function loadAccounts() {
    const s = (state.stats = await api(`/api/stats?days=${days()}&tz=${TZ}`));
    $("#accounts-table").innerHTML = s.by_account.length ? `<tr><th>Conta</th><th>Tipo</th><th>Serviços</th><th>Usuários</th>
      <th class="num">Prompts</th><th class="num">Uploads</th><th class="num">Alto risco</th><th class="num">Score máx.</th><th>Último uso</th></tr>
      ${s.by_account.map((a) => `<tr><td>${esc(a.account_email || "não identificada")}</td><td>${accPill(a.account_type)}</td>
        <td>${esc((a.providers || "").split(",").map(providerName).join(", "))}</td><td>${esc(a.machine_users || "")}</td>
        <td class="num">${fmt(a.prompts)}</td><td class="num">${fmt(a.uploads)}</td><td class="num">${fmt(a.high_risk)}</td>
        <td class="num">${fmt(a.max_score)}</td><td>${dt(a.last_seen)}</td></tr>`).join("")}`
      : `<tr><td class="empty">Sem dados no período</td></tr>`;
    $("#users-table").innerHTML = s.by_machine_user.length ? `<tr><th>Usuário</th><th>Máquina</th><th class="num">Interações</th>
      <th class="num">Contas distintas</th><th class="num">Com conta pessoal</th><th class="num">Alto risco</th></tr>
      ${s.by_machine_user.map((u) => `<tr><td>${esc(u.machine_user)}</td><td>${esc(u.hostname)}</td><td class="num">${fmt(u.interactions)}</td>
        <td class="num">${fmt(u.accounts)}</td><td class="num">${fmt(u.personal)}</td><td class="num">${fmt(u.high_risk)}</td></tr>`).join("")}`
      : `<tr><td class="empty">Sem dados no período</td></tr>`;
  }

  // --------------------------------------------------------------- drawer
  document.addEventListener("click", async (e) => {
    const trg = e.target.closest("[data-event]");
    if (!trg) return;
    e.preventDefault();
    const ev = await api(`/api/events/${trg.dataset.event}`);
    $("#drawer-title").textContent = `${TYPE_LABEL[ev.type] || ev.type} #${ev.id}`;
    $("#drawer-content").innerHTML = `
      <dl class="kv">
        <dt>Data/hora</dt><dd>${dt(ev.ts)}</dd>
        <dt>Serviço</dt><dd>${esc(providerName(ev.provider))}</dd>
        <dt>URL</dt><dd>${esc(ev.url || "—")}</dd>
        <dt>Conta</dt><dd>${esc(ev.account_email || "não identificada")} ${accPill(ev.account_type)}</dd>
        <dt>Usuário / máquina</dt><dd>${esc(ev.machine_user || "")} @ ${esc(ev.hostname || "")}</dd>
        <dt>Navegador</dt><dd>${esc(ev.browser || "—")}</dd>
        <dt>Risco</dt><dd>${levelPill(ev.level, ev.score)}</dd>
        ${ev.file_name ? `<dt>Arquivo</dt><dd>${esc(ev.file_name)} (${fmt(Math.round((ev.file_size || 0) / 1024))} KB)</dd>` : ""}
        ${ev.process_name ? `<dt>Processo</dt><dd>${esc(ev.process_name)} → ${esc(ev.remote_addr)}</dd>` : ""}
        ${ev.prompt_chars ? `<dt>Tamanho</dt><dd>${fmt(ev.prompt_chars)} caracteres</dd>` : ""}
      </dl>
      ${ev.prompt_text ? `<h2>Prompt</h2><pre class="prompt">${esc(ev.prompt_text)}</pre>` : ""}
      ${findingsHtml(ev.findings)}`;
    $("#drawer").hidden = false;
  });
  const closeDrawer = () => ($("#drawer").hidden = true);
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") closeDrawer(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

  function findingsHtml(findings) {
    if (!findings || !findings.length) return `<p class="hint">Nenhum risco identificado.</p>`;
    const sevLevel = { critica: "critico", alta: "alto", media: "medio", baixa: "baixo" };
    return `<h2 style="margin:16px 0 8px">Achados</h2>` + findings.map((f) => `
      <div class="finding">${levelPill(sevLevel[f.severity] || f.severity)} <strong>${esc(f.description)}</strong>
        <span class="hint">· ${esc(f.category_label || f.category)} · ${f.count}×</span>
        ${f.samples?.length ? `<div class="samples">${f.samples.map(esc).join(" · ")}</div>` : ""}</div>`).join("");
  }

  // -------------------------------------------------------------- analyze
  $("#analyze-btn").addEventListener("click", async () => {
    const r = await api("/api/analyze", { method: "POST", body: JSON.stringify({ text: $("#analyze-text").value }) });
    $("#analyze-result").innerHTML = `<div style="display:flex;align-items:center;gap:12px;margin:8px 0">
      <span class="score-big">${r.score}</span>${levelPill(r.level)} ${catPills(r.categories)}</div>${findingsHtml(r.findings)}`;
  });

  // ------------------------------------------------------------- settings
  async function loadSettings() {
    const c = await api("/api/config");
    const f = $("#settings-form");
    f.corporate_domains.value = c.corporate_domains.join("\n");
    f.custom_keywords.value = c.custom_keywords.join("\n");
    for (const k of ["store_mode", "alert_min_score", "large_prompt_chars", "retention_days", "webhook_url", "webhook_min_level"]) f[k].value = c[k];
    f.alert_personal_accounts.checked = c.alert_personal_accounts;
    f.network_monitor.checked = c.network_monitor;
    $("#netmon-hint").textContent = state.meta?.network_monitor_available ? "" : "(requer o pacote psutil)";
    $("#agent-url").textContent = location.origin;
  }
  $("#settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    const lines = (v) => v.split(/\n|,/).map((x) => x.trim()).filter(Boolean);
    try {
      await api("/api/config", {
        method: "PUT", body: JSON.stringify({
          corporate_domains: lines(f.corporate_domains.value), custom_keywords: lines(f.custom_keywords.value),
          store_mode: f.store_mode.value, alert_min_score: Number(f.alert_min_score.value),
          large_prompt_chars: Number(f.large_prompt_chars.value), retention_days: Number(f.retention_days.value),
          webhook_url: f.webhook_url.value.trim(), webhook_min_level: f.webhook_min_level.value,
          alert_personal_accounts: f.alert_personal_accounts.checked, network_monitor: f.network_monitor.checked,
        }),
      });
      $("#settings-msg").textContent = "Configurações salvas.";
    } catch (err) {
      $("#settings-msg").textContent = "Erro: " + err.message;
    }
    setTimeout(() => ($("#settings-msg").textContent = ""), 3000);
  });
  let tokenValue = null;
  $("#show-token").addEventListener("click", async () => {
    tokenValue = tokenValue || (await api("/api/config/token")).token;
    const t = $("#token");
    const shown = !t.classList.contains("masked");
    t.textContent = shown ? "••••••••••••" : tokenValue;
    t.classList.toggle("masked", shown);
    $("#show-token").textContent = shown ? "Mostrar token" : "Ocultar token";
  });
  $("#copy-token").addEventListener("click", async () => {
    tokenValue = tokenValue || (await api("/api/config/token")).token;
    await navigator.clipboard.writeText(tokenValue);
    $("#copy-token").textContent = "Copiado!";
    setTimeout(() => ($("#copy-token").textContent = "Copiar"), 1500);
  });

  // ---------------------------------------------------------------- boot
  const loaders = { overview: loadOverview, alerts: loadAlerts, events: loadEvents, accounts: loadAccounts, analyze: async () => {}, settings: loadSettings };
  async function refresh() {
    try {
      await loaders[state.tab]();
    } catch (err) {
      console.error(err);
    }
  }
  $("#period").addEventListener("change", refresh);
  $("#refresh").addEventListener("click", refresh);
  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (state.tab === "overview") loadOverview(); }, 200);
  });

  (async function boot() {
    state.meta = await api("/api/meta");
    $("#host-info").textContent = `${state.meta.machine_user} @ ${state.meta.hostname} · v${state.meta.version}`;
    $("#ev-provider").insertAdjacentHTML("beforeend", state.meta.providers.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join(""));
    let tab = "overview";
    try { tab = localStorage.getItem("shadowia.tab") || tab; } catch (_) { /* ignore */ }
    selectTab(loaders[tab] ? tab : "overview");
    setInterval(() => { if (state.tab === "overview" || state.tab === "alerts") refresh(); }, 30000);
  })();
})();
