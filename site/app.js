/* GPU Compute Index dashboard. Reads the published files in index/ (no backend):
   latest.json for the headline numbers, index/series/<name>.json for history.
   Everything that comes from data is written with textContent, never innerHTML. */
(function () {
  "use strict";

  const BASE = "index/";
  const MODELS = [
    ["h100-sxm-80gb", "H100 SXM 80GB"],
    ["h100-nvl-94gb", "H100 NVL 94GB"],
    ["a100-sxm-80gb", "A100 SXM 80GB"],
    ["a100-pcie-80gb", "A100 PCIe 80GB"],
    ["a100-sxm-40gb", "A100 SXM 40GB"],
  ];
  const SOURCE_NAMES = { aws: "AWS", azure: "Azure" };
  // Market events worth pointing at (dated by the price list that carried them).
  const EVENTS = [{ date: "2025-07-03", label: "AWS GPU price cut" }];
  const DAY = 86400000;
  const SVG = "http://www.w3.org/2000/svg";

  const $ = (id) => document.getElementById(id);
  const el = (tag, attrs = {}, text) => {
    const n = tag.startsWith("svg:") ? document.createElementNS(SVG, tag.slice(4)) : document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (text != null) n.textContent = text;
    return n;
  };
  const usd = (v) => "$" + v.toFixed(2);
  const pct = (a, b) => {
    const p = ((a - b) / b) * 100;
    return (p > 0 ? "+" : p < 0 ? "−" : "") + Math.abs(p).toFixed(0) + "%";
  };
  const t = (d) => Date.parse(d + "T00:00:00Z");
  const fmtDate = (d, opts = { year: "numeric", month: "short", day: "numeric" }) =>
    new Date(t(d)).toLocaleDateString("en-US", { ...opts, timeZone: "UTC" });
  const sourcesText = (p) => Object.keys(p.sources).map((s) => SOURCE_NAMES[s] || s).join(" + ");

  const cache = new Map();
  async function series(name) {
    if (!cache.has(name)) {
      cache.set(name, fetch(BASE + "series/" + name + ".json").then((r) => (r.ok ? r.json() : null)).catch(() => null));
    }
    return cache.get(name);
  }

  // ---------- state (kept in the URL so views can be shared) ----------
  const state = { model: "h100-sxm-80gb", tier: "on-demand", range: 0 };
  function readHash() {
    const [model, tier, range] = location.hash.slice(1).split("/");
    if (MODELS.some(([m]) => m === model)) state.model = model;
    if (tier === "on-demand" || tier === "spot") state.tier = tier;
    if (["90", "365", "0"].includes(range)) state.range = +range;
  }
  function writeHash() {
    history.replaceState(null, "", `#${state.model}/${state.tier}/${state.range}`);
  }

  let latest = null;

  // A change is only reported between two days with the same set of sources. When a
  // source joins, the level moves without any price moving, and "+47% vs a year ago"
  // would be a lie (see METHODOLOGY.md, known limitations).
  const sameSources = (a, b) => Object.keys(a.sources).join() === Object.keys(b.sources).join();
  function changeText(pts, now) {
    const last = pts[pts.length - 1];
    let start = pts.length - 1;
    while (start > 0 && sameSources(pts[start - 1], last)) start--;
    const target = t(last.date) - 365 * DAY;
    const yearAgo = [...pts].reverse().find((x) => t(x.date) <= target);
    if (yearAgo && t(yearAgo.date) >= t(pts[start].date)) return `${pct(now.value, yearAgo.value)} vs a year ago`;
    if (start < pts.length - 1) {
      return `${pct(now.value, pts[start].value)} since ${fmtDate(pts[start].date, { year: "numeric", month: "short" })}`;
    }
    return `${sourcesText(last)} from ${fmtDate(last.date, { month: "short", day: "numeric" })}: no like-for-like change yet`;
  }

  // ---------- headline tiles ----------
  async function renderTiles() {
    const box = $("tiles");
    box.replaceChildren();
    for (const [model, label] of MODELS) {
      const name = `${model}-on-demand`;
      const p = latest.series[name];
      if (!p) continue;
      const b = el("button", { class: "tile", type: "button", "aria-pressed": String(state.model === model) });
      b.append(el("div", { class: "tile-label" }, label));
      const v = el("div", { class: "tile-value" }, usd(p.value));
      v.append(el("small", {}, "/ GPU-hr"));
      b.append(v);
      const delta = el("div", { class: "tile-delta" }, " ");
      b.append(delta);
      b.addEventListener("click", () => { state.model = model; state.tier = "on-demand"; update(); });
      box.append(b);

      series(name).then((s) => {
        if (!s || !s.points.length) return;
        delta.textContent = changeText(s.points, p);
      });
    }
  }

  // ---------- table ----------
  function renderTable() {
    const body = $("table").querySelector("tbody");
    body.replaceChildren();
    for (const [model, label] of MODELS) {
      for (const tier of ["on-demand", "spot"]) {
        const p = latest.series[`${model}-${tier}`];
        if (!p) continue;
        const tr = el("tr", { tabindex: "0" });
        const asOf = Object.entries(p.source_dates || {}).map(([s, d]) => `${SOURCE_NAMES[s] || s} ${d}`).join(", ");
        tr.append(
          el("td", {}, label),
          el("td", {}, tier === "spot" ? "Spot" : "On-demand"),
          el("td", { class: "num" }, usd(p.value)),
          el("td", { class: "num" }, `${usd(p.p25)} – ${usd(p.p75)}`),
          el("td", { class: "num" }, String(p.observations_used)),
          el("td", {}, asOf),
        );
        const go = () => { state.model = model; state.tier = tier; update(); $("chart").scrollIntoView({ behavior: "smooth", block: "center" }); };
        tr.addEventListener("click", go);
        tr.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
        body.append(tr);
      }
    }
  }

  // ---------- controls ----------
  function renderControls() {
    const sel = $("model");
    if (!sel.options.length) {
      for (const [m, label] of MODELS) sel.append(el("option", { value: m }, label));
      sel.addEventListener("change", () => { state.model = sel.value; update(); });
      $("range").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) { state.range = +b.dataset.range; update(); } });
      $("tier").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b && !b.disabled) { state.tier = b.dataset.tier; update(); } });
    }
    sel.value = state.model;
    for (const b of $("range").querySelectorAll("button")) b.setAttribute("aria-checked", String(+b.dataset.range === state.range));
    for (const b of $("tier").querySelectorAll("button")) {
      b.disabled = !latest.series[`${state.model}-${b.dataset.tier}`];
      b.setAttribute("aria-checked", String(b.dataset.tier === state.tier));
    }
    for (const tile of $("tiles").querySelectorAll(".tile")) tile.setAttribute("aria-pressed", "false");
    const idx = MODELS.findIndex(([m]) => m === state.model);
    const tile = $("tiles").children[idx];
    if (tile && state.tier === "on-demand") tile.setAttribute("aria-pressed", "true");
  }

  // ---------- chart ----------
  let current = null; // { points, label, tier }

  function niceStep(span, target) {
    const raw = span / target;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const n = raw / mag;
    return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * mag;
  }

  function xTicks(t0, t1) {
    const span = (t1 - t0) / DAY;
    const ticks = [];
    const d = new Date(t0);
    if (span > 730) {
      for (let y = d.getUTCFullYear() + 1; Date.UTC(y, 0, 1) <= t1; y++) ticks.push([Date.UTC(y, 0, 1), String(y)]);
    } else {
      const every = span > 200 ? 2 : 1;
      let y = d.getUTCFullYear(), m = d.getUTCMonth() + 1;
      for (; Date.UTC(y, m, 1) <= t1; m += every) {
        const when = Date.UTC(y, m, 1);
        ticks.push([when, new Date(when).toLocaleDateString("en-US", { month: "short", year: m % 12 === 0 ? "numeric" : undefined, timeZone: "UTC" })]);
      }
    }
    return ticks;
  }

  function drawChart() {
    const box = $("chart");
    const tooltip = $("tooltip");
    box.querySelector("svg")?.remove();
    tooltip.hidden = true;
    const W = box.clientWidth, H = box.clientHeight;
    const m = { l: 46, r: 12, t: 22, b: 26 };
    const svg = el("svg:svg", { viewBox: `0 0 ${W} ${H}`, tabindex: "0", "aria-label": "Price history chart. Use left and right arrow keys to read values." });
    box.prepend(svg);
    if (!current || !current.points.length) {
      svg.append(el("svg:text", { x: W / 2, y: H / 2, "text-anchor": "middle", class: "empty" }, "No data for this series."));
      return;
    }

    const all = current.points;
    const end = t(all[all.length - 1].date);
    const pts = state.range ? all.filter((p) => t(p.date) >= end - state.range * DAY) : all;
    const t0 = t(pts[0].date), t1 = Math.max(end, t0 + DAY);
    let lo = Math.min(...pts.map((p) => Math.min(p.value, p.p25)));
    let hi = Math.max(...pts.map((p) => Math.max(p.value, p.p75)));
    const pad = (hi - lo) * 0.08 || hi * 0.1 || 1;
    lo = Math.max(0, lo - pad); hi = hi + pad;
    const step = niceStep(hi - lo, 5);
    lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;

    const x = (ms) => m.l + ((ms - t0) / (t1 - t0 || 1)) * (W - m.l - m.r);
    const y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);

    // grid + y axis
    const grid = el("svg:g", { class: "grid" }), axis = el("svg:g", { class: "axis" });
    for (let v = lo; v <= hi + step / 2; v += step) {
      grid.append(el("svg:line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }));
      axis.append(el("svg:text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }, "$" + (step < 1 ? v.toFixed(1) : v.toFixed(0))));
    }
    for (const [ms, label] of xTicks(t0, t1)) {
      if (x(ms) < m.l + 10 || x(ms) > W - m.r - 10) continue;
      axis.append(el("svg:text", { x: x(ms), y: H - 6, "text-anchor": "middle" }, label));
    }
    svg.append(grid, axis);

    // segments: break the line where days are missing (no value published)
    const segments = [];
    let seg = [];
    pts.forEach((p, i) => {
      if (i && t(p.date) - t(pts[i - 1].date) > DAY) { segments.push(seg); seg = []; }
      seg.push(p);
    });
    segments.push(seg);
    for (const s of segments) {
      const top = s.map((p) => `${x(t(p.date)).toFixed(1)},${y(p.p75).toFixed(1)}`);
      const bot = s.slice().reverse().map((p) => `${x(t(p.date)).toFixed(1)},${y(p.p25).toFixed(1)}`);
      const one = s.length === 1 ? ` ${(x(t(s[0].date)) + 2).toFixed(1)},${y(s[0].p25).toFixed(1)}` : "";
      svg.append(el("svg:polygon", { class: "band", points: top.concat(bot).join(" ") + one }));
    }
    for (const s of segments) {
      const d = s.map((p, i) => `${i ? "L" : "M"}${x(t(p.date)).toFixed(1)},${y(p.value).toFixed(1)}`).join("");
      svg.append(el("svg:path", { class: "line", d: s.length === 1 ? d + `h1` : d }));
    }

    // markers: when the set of sources changes, and known market events
    const markers = [];
    pts.forEach((p, i) => {
      if (!i) return;
      const a = Object.keys(pts[i - 1].sources).join(), b = Object.keys(p.sources).join();
      if (a !== b) {
        const added = Object.keys(p.sources).filter((s) => !pts[i - 1].sources[s]).map((s) => "+" + (SOURCE_NAMES[s] || s));
        const gone = Object.keys(pts[i - 1].sources).filter((s) => !p.sources[s]).map((s) => "−" + (SOURCE_NAMES[s] || s));
        markers.push([t(p.date), [...added, ...gone].join(" ")]);
      }
    });
    for (const e of EVENTS) if (t(e.date) > t0 && t(e.date) <= t1) markers.push([t(e.date), e.label]);
    const mg = el("svg:g", { class: "marker" });
    let lastLabelX = -Infinity, row = 0;
    for (const [ms, label] of markers.sort((a, b) => a[0] - b[0])) {
      const mx = x(ms);
      mg.append(el("svg:line", { x1: mx, x2: mx, y1: m.t - 6, y2: H - m.b }));
      row = mx - lastLabelX < 120 ? row + 1 : 0;
      const anchor = mx > W - 140 ? "end" : "start";
      mg.append(el("svg:text", { x: anchor === "end" ? mx - 4 : mx + 4, y: m.t - 10 + row * 13 + 8, "text-anchor": anchor }, label));
      lastLabelX = mx;
    }
    svg.append(mg);

    // hover layer: crosshair, dot and one tooltip
    const cross = el("svg:line", { class: "cross", y1: m.t, y2: H - m.b, visibility: "hidden" });
    const dot = el("svg:circle", { class: "dot", r: 4.5, visibility: "hidden" });
    const hit = el("svg:rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent" });
    svg.append(cross, dot, hit);

    let active = pts.length - 1;
    function show(i) {
      active = Math.max(0, Math.min(pts.length - 1, i));
      const p = pts[active], px = x(t(p.date)), py = y(p.value);
      cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
      dot.setAttribute("cx", px); dot.setAttribute("cy", py); dot.setAttribute("visibility", "visible");
      tooltip.replaceChildren(
        el("div", { class: "t-date" }, fmtDate(p.date)),
        (() => { const r = el("div", { class: "t-row" }); r.append(el("span", { class: "t-key" }), el("strong", {}, usd(p.value)), el("span", {}, "per GPU-hour")); return r; })(),
        el("div", { class: "t-meta" }, `Range ${usd(p.p25)} – ${usd(p.p75)}`),
        el("div", { class: "t-meta" }, `${p.observations_used} prices · ${sourcesText(p)}`),
        el("div", { class: "t-meta" }, "Prices as of " + Object.entries(p.source_dates || {}).map(([s, d]) => `${SOURCE_NAMES[s] || s} ${d}`).join(", ")),
      );
      tooltip.hidden = false;
      const tw = tooltip.offsetWidth, th = tooltip.offsetHeight;
      let left = px + 14;
      if (left + tw > W) left = px - tw - 14;
      tooltip.style.left = Math.max(0, left) + "px";
      tooltip.style.top = Math.max(0, Math.min(py - th / 2, H - th)) + "px";
    }
    function hide() { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); tooltip.hidden = true; }
    function nearest(clientX) {
      const r = svg.getBoundingClientRect();
      const ms = t0 + ((clientX - r.left - m.l) / (W - m.l - m.r)) * (t1 - t0);
      let a = 0, b = pts.length - 1;
      while (a < b) { const mid = (a + b) >> 1; if (t(pts[mid].date) < ms) a = mid + 1; else b = mid; }
      return a > 0 && Math.abs(t(pts[a - 1].date) - ms) < Math.abs(t(pts[a].date) - ms) ? a - 1 : a;
    }
    hit.addEventListener("pointermove", (e) => show(nearest(e.clientX)));
    hit.addEventListener("pointerdown", (e) => show(nearest(e.clientX)));
    svg.addEventListener("pointerleave", hide);
    svg.addEventListener("focus", () => show(active));
    svg.addEventListener("blur", hide);
    svg.addEventListener("keydown", (e) => {
      const jump = e.shiftKey ? 30 : 1;
      if (e.key === "ArrowLeft") { e.preventDefault(); show(active - jump); }
      if (e.key === "ArrowRight") { e.preventDefault(); show(active + jump); }
      if (e.key === "Home") { e.preventDefault(); show(0); }
      if (e.key === "End") { e.preventDefault(); show(pts.length - 1); }
    });
  }

  async function renderChart() {
    const name = `${state.model}-${state.tier}`;
    const label = MODELS.find(([m]) => m === state.model)[1];
    const tierLabel = state.tier === "spot" ? "spot" : "on-demand";
    $("chart-title").textContent = `${label}, ${tierLabel}`;
    const s = await series(name);
    current = s ? { points: s.points, label, tier: state.tier } : null;
    const p = latest.series[name];
    if (s && s.points.length && p) {
      const first = s.points[0].date;
      $("chart-sub").textContent = `USD per GPU-hour · ${s.points.length.toLocaleString("en-US")} days of history since ${fmtDate(first)}`;
      $("now-value").textContent = usd(p.value);
      $("now-label").textContent = `${fmtDate(p.date)} · ${sourcesText(p)}`;
    } else {
      $("chart-sub").textContent = "No published values for this series yet.";
      $("now-value").textContent = "—";
      $("now-label").textContent = "";
    }
    drawChart();
  }

  async function update() {
    if (!latest.series[`${state.model}-${state.tier}`]) state.tier = "on-demand";
    writeHash();
    renderControls();
    await renderChart();
  }

  async function init() {
    readHash();
    try {
      latest = await fetch(BASE + "latest.json").then((r) => r.json());
    } catch (e) {
      $("chart-title").textContent = "Couldn't load the index data.";
      return;
    }
    $("head").textContent = latest.ledger_head;
    $("method").textContent = "v" + latest.methodology_version;
    await renderTiles();
    renderTable();
    await update();
    let raf = 0;
    new ResizeObserver(() => { cancelAnimationFrame(raf); raf = requestAnimationFrame(drawChart); }).observe($("chart"));
    window.addEventListener("hashchange", () => { readHash(); update(); });
  }

  init();
})();
