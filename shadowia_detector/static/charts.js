/* Mini biblioteca de gráficos SVG (sem dependências). */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const tooltip = () => document.getElementById("tooltip");

  function el(tag, attrs, parent) {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs || {}) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  const fmt = (n) => Number(n || 0).toLocaleString("pt-BR");

  /* Máximo do eixo com 4 divisões inteiras e "redondas" (1, 2, 5 × 10^n). */
  function niceMax(v) {
    if (v <= 0) return 4;
    const raw = v / 4;
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    for (const m of [1, 2, 2.5, 5, 10]) {
      const step = m * p;
      if (step >= raw && Number.isInteger(step)) return step * 4;
    }
    return 40 * p;
  }

  function showTip(evt, title, rows) {
    const t = tooltip();
    t.innerHTML = `<div class="t-title">${esc(title)}</div>` + rows.map((r) =>
      `<div class="t-row"><span>${r.color ? `<i style="background:${r.color}"></i>` : ""}${esc(r.label)}</span><strong>${esc(r.value)}</strong></div>`).join("");
    t.hidden = false;
    const pad = 14, w = t.offsetWidth, h = t.offsetHeight;
    let x = evt.clientX + pad, y = evt.clientY + pad;
    if (x + w > window.innerWidth - 8) x = evt.clientX - w - pad;
    if (y + h > window.innerHeight - 8) y = evt.clientY - h - pad;
    t.style.left = x + "px";
    t.style.top = y + "px";
  }
  const hideTip = () => { tooltip().hidden = true; };

  /* Retângulo com cantos de 4px apenas na extremidade de dados. */
  function barPath(x, y, w, h, dir, r) {
    r = Math.max(0, Math.min(r, w / 2, h / 2));
    if (dir === "up") {   // coluna: base embaixo, cantos arredondados no topo
      return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
    }                     // barra horizontal: base à esquerda, cantos à direita
    return `M${x},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h - r}Q${x + w},${y + h} ${x + w - r},${y + h}H${x}Z`;
  }

  function prepare(container) {
    container.innerHTML = "";
    return Math.max(280, container.clientWidth || 600);
  }

  function empty(container, msg) {
    container.innerHTML = `<div class="empty">${esc(msg || "Sem dados no período")}</div>`;
  }

  /* Barras horizontais de série única. rows: [{label, value, tip?}] */
  function hbar(container, rows, opts = {}) {
    if (!rows.length || rows.every((r) => !r.value)) return empty(container);
    const W = prepare(container);
    const labelW = Math.min(170, W * 0.36), valueW = 48, rowH = 26, barH = 14;
    const H = rows.length * rowH + 4;
    const max = Math.max(...rows.map((r) => r.value)) || 1;
    const plotW = W - labelW - valueW;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.aria || "" }, container);
    el("line", { x1: labelW, x2: labelW, y1: 0, y2: H, class: "base-line" }, svg);
    rows.forEach((r, i) => {
      const y = i * rowH + 2;
      const w = Math.max(r.value > 0 ? 2 : 0, (r.value / max) * plotW);
      const t = el("text", { x: labelW - 8, y: y + rowH / 2 + 4, "text-anchor": "end", class: "label-primary" }, svg);
      t.textContent = r.label.length > 26 ? r.label.slice(0, 25) + "…" : r.label;
      el("path", { d: barPath(labelW, y + (rowH - barH) / 2, w, barH, "right", 4), style: `fill:${r.color || opts.color || "var(--series-1)"}`, class: "bar" }, svg);
      const v = el("text", { x: labelW + w + 6, y: y + rowH / 2 + 4, class: "value-label" }, svg);
      v.textContent = (opts.valueFmt || fmt)(r.value);
      const hit = el("rect", { x: 0, y, width: W, height: rowH, class: "hit" }, svg);
      hit.addEventListener("mousemove", (e) => showTip(e, r.label, r.tip || [{ label: opts.valueLabel || "Total", value: fmt(r.value) }]));
      hit.addEventListener("mouseleave", hideTip);
    });
  }

  /* Colunas empilhadas por categoria (ex.: dias). series: [{key,label,color}] */
  function stackedColumns(container, data, series, opts = {}) {
    const totals = data.map((d) => series.reduce((s, k) => s + (d[k.key] || 0), 0));
    if (!totals.some(Boolean)) return empty(container);
    const W = prepare(container);
    const H = opts.height || 220, padL = 36, padB = 24, padT = 8, padR = 4;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const max = niceMax(Math.max(...totals));
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.aria || "" }, container);
    for (let i = 0; i <= 4; i++) {
      const y = padT + plotH - (i / 4) * plotH;
      el("line", { x1: padL, x2: W - padR, y1: y, y2: y, class: i ? "grid-line" : "base-line" }, svg);
      const t = el("text", { x: padL - 6, y: y + 4, "text-anchor": "end" }, svg);
      t.textContent = fmt(Math.round((max * i) / 4));
    }
    const slot = plotW / data.length;
    const bw = Math.max(2, Math.min(28, slot * 0.62));
    const every = Math.ceil(data.length / Math.max(1, Math.floor(plotW / 56)));
    data.forEach((d, i) => {
      const x = padL + i * slot + (slot - bw) / 2;
      let yBase = padT + plotH;
      const nonZero = series.filter((s) => d[s.key] > 0);
      nonZero.forEach((s, j) => {
        const h = (d[s.key] / max) * plotH;
        const gap = j > 0 ? 2 : 0;          // 2px de separação entre segmentos
        const top = j === nonZero.length - 1;
        const y = yBase - h;
        const hh = Math.max(0, h - gap);
        el("path", { d: top ? barPath(x, y, bw, hh, "up", 4) : `M${x},${y}h${bw}v${hh}h${-bw}Z`, style: `fill:${s.color}`, class: "bar" }, svg);
        yBase = y;
      });
      if (i % every === 0) {
        const t = el("text", { x: x + bw / 2, y: H - 6, "text-anchor": "middle" }, svg);
        t.textContent = opts.xLabel ? opts.xLabel(d) : d.label;
      }
      const hit = el("rect", { x: padL + i * slot, y: padT, width: slot, height: plotH, class: "hit" }, svg);
      hit.addEventListener("mousemove", (e) => showTip(e, opts.tipTitle ? opts.tipTitle(d) : d.label,
        series.map((s) => ({ label: s.label, value: fmt(d[s.key]), color: s.color }))
          .concat(opts.extraTip ? opts.extraTip(d) : [])));
      hit.addEventListener("mouseleave", hideTip);
    });
  }

  /* Colunas simples (série única). rows: [{label, value}] */
  function columns(container, rows, opts = {}) {
    const series = [{ key: "value", label: opts.valueLabel || "Total", color: opts.color || "var(--series-1)" }];
    stackedColumns(container, rows, series, { ...opts, height: opts.height || 180 });
  }

  function legend(container, series) {
    container.innerHTML = series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.label)}</span>`).join("");
  }

  window.Charts = { hbar, stackedColumns, columns, legend, esc, fmt, hideTip };
})();
