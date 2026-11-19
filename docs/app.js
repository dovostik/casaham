"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};

const FIELDS = {
  ret1d: "% Hari ini", ret5d: "% 5 hari", ret20d: "% 20 hari", ret60d: "% 60 hari",
  close: "Harga", volume: "Volume (lembar)", value: "Value (Rp)", avgValue20: "Rata2 value 20H",
  volRatio: "Volume / rata2 20H", mcap: "Market cap (Rp)", rsi14: "RSI 14", stochK: "Stoch %K 14",
  macdHist: "MACD histogram", aboveMA20: "Di atas MA20 (1/0)", aboveMA50: "Di atas MA50 (1/0)",
  gc2050: "Golden cross 20/50 (1/0)", fromHigh52: "% dari high 52M", araPct: "% menuju ARA",
  per: "PER", pbv: "PBV", roe: "ROE %", divYield: "Dividend yield %",
  issi: "Syariah ISSI (1/0)", nearHighBBS: "Dekat high ≤3% / ≤2 tick (1/0)", fromHighD: "% dari high hari ini", highRet1d: "% high hari ini vs kemarin",
};
const OPS = { ">": (a, b) => a > b, ">=": (a, b) => a >= b, "<": (a, b) => a < b, "<=": (a, b) => a <= b, "==": (a, b) => a == b, "!=": (a, b) => a != b };

let DATA = { meta: {}, rows: [] }, BY = {}, PRESETS = [], current = null, rules = [];

// ---------- format
const fmtN = (x, d = 0) => x == null ? "-" : Number(x).toLocaleString("id-ID", { maximumFractionDigits: d, minimumFractionDigits: d });
const fmtB = x => { if (x == null) return "-"; const a = Math.abs(x);
  if (a >= 1e12) return fmtN(x / 1e12, 2) + " T"; if (a >= 1e9) return fmtN(x / 1e9, 1) + " M";
  if (a >= 1e6) return fmtN(x / 1e6, 1) + " jt"; return fmtN(x); };
const pct = x => x == null ? "-" : (x > 0 ? "+" : "") + fmtN(x, 2) + "%";
const cls = x => x > 0 ? "up" : x < 0 ? "dn" : "";

// ---------- tabs
function showTab(t) {
  $$(".tab").forEach(s => s.classList.toggle("active", s.id === "tab-" + t));
  $$("nav.bottom button").forEach(b => b.classList.toggle("on", b.dataset.tab === t));
  if (t === "watch") renderWatch();
  if (t === "jurnal") renderJurnal();
  if (t === "chart" && chart) setTimeout(resizeCharts, 0);
  window.scrollTo(0, 0);
}
$$("nav.bottom button").forEach(b => b.onclick = () => showTab(b.dataset.tab));

// ---------- screener
function allPresets() { return [...PRESETS, ...store.get("myScreens", [])]; }
function renderPresets() {
  const box = $("#presets"); box.innerHTML = "";
  allPresets().forEach(p => {
    const b = document.createElement("button");
    b.textContent = (p.mine ? "★ " : "") + p.name;
    b.className = current && current.id === p.id ? "on" : "";
    b.onclick = () => selectPreset(p.id);
    box.appendChild(b);
  });
}
function selectPreset(id) {
  current = allPresets().find(p => p.id === id) || allPresets()[0];
  rules = current.rules.map(r => [...r]);
  $("#presetDesc").textContent = current.desc || "";
  renderPresets(); renderRules(); runScreen();
}
function renderRules() {
  const box = $("#rules"); box.innerHTML = "";
  rules.forEach((r, i) => {
    const d = document.createElement("div"); d.className = "rule";
    d.innerHTML = `<select>${Object.entries(FIELDS).map(([k, v]) => `<option value="${k}" ${k === r[0] ? "selected" : ""}>${v}</option>`).join("")}</select>
      <select>${Object.keys(OPS).map(o => `<option ${o === r[1] ? "selected" : ""}>${o}</option>`).join("")}</select>
      <input type="number" inputmode="decimal" step="any" value="${r[2]}"><button class="x" title="hapus">×</button>`;
    const [f, o, v] = $$("select,input", d);
    f.onchange = () => { r[0] = f.value; runScreen(); };
    o.onchange = () => { r[1] = o.value; runScreen(); };
    v.oninput = () => { r[2] = parseFloat(v.value); runScreen(); };
    $(".x", d).onclick = () => { rules.splice(i, 1); renderRules(); runScreen(); };
    box.appendChild(d);
  });
  $("#ruleCount").textContent = `(${rules.length})`;
}
$("#addRule").onclick = () => { rules.push(["ret1d", ">", 0]); renderRules(); runScreen(); };
$("#saveScreen").onclick = () => {
  const name = prompt("Nama screener:", current?.mine ? current.name : "");
  if (!name) return;
  const mine = store.get("myScreens", []);
  const id = "my_" + name.toLowerCase().replace(/\W+/g, "_");
  const i = mine.findIndex(p => p.id === id);
  const p = { id, name, mine: true, desc: "Screener buatan sendiri (tersimpan di HP ini)", rules: rules.map(r => [...r]) };
  if (i >= 0) mine[i] = p; else mine.push(p);
  store.set("myScreens", mine); current = p; renderPresets();
};
$("#sortSel").onchange = runScreen;

function runScreen() {
  const key = $("#sortSel").value;
  const hits = DATA.rows.filter(r => rules.every(([f, o, v]) => r[f] != null && !Number.isNaN(v) && OPS[o](r[f], v)));
  hits.sort((a, b) => key === "code" ? a.code.localeCompare(b.code) : (b[key] ?? -1e99) - (a[key] ?? -1e99));
  $("#hitCount").textContent = `${hits.length} saham`;
  renderList($("#results"), hits.slice(0, 300));
  location.hash = "screener=" + (current?.id || "");
}
function renderList(box, rows) {
  box.innerHTML = rows.map(r => `<div class="item" data-c="${r.code}">
    <div><span class="c">${r.code}</span>${r.araPct >= 95 ? '<span class="badge">ARA</span>' : ""}<div class="n">${r.name}</div></div>
    <div class="p ${cls(r.ret1d)}">${fmtN(r.close)}<div class="s"><span class="${cls(r.ret1d)}">${pct(r.ret1d)}</span> · ${fmtB(r.value)}</div></div>
  </div>`).join("") || `<p class="muted">Tidak ada saham yang lolos.</p>`;
  $$(".item", box).forEach(el => el.onclick = () => openChart(el.dataset.c));
}

// ---------- chart
let chart, rsiChart, candle, vol, ma20s, ma50s, rsiS, curCode, curHist, range = 130;
function initCharts() {
  if (chart || !window.LightweightCharts) return;
  const opt = { layout: { background: { color: "transparent" }, textColor: "#8a97a5" },
    grid: { vertLines: { color: "#1e262e" }, horzLines: { color: "#1e262e" } },
    rightPriceScale: { borderColor: "#26303a" }, timeScale: { borderColor: "#26303a" },
    crosshair: { mode: 0 }, handleScale: true };
  chart = LightweightCharts.createChart($("#chartMain"), opt);
  candle = chart.addCandlestickSeries({ upColor: "#16c784", downColor: "#ea3943", borderVisible: false, wickUpColor: "#16c784", wickDownColor: "#ea3943", priceFormat: { type: "price", precision: 0, minMove: 1 } });
  vol = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "v", lastValueVisible: false, priceLineVisible: false });
  chart.priceScale("v").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
  candle.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.22 } });
  ma20s = chart.addLineSeries({ color: "#f5a524", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  ma50s = chart.addLineSeries({ color: "#3b9cff", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  rsiChart = LightweightCharts.createChart($("#chartRsi"), { ...opt, timeScale: { visible: false } });
  rsiS = rsiChart.addLineSeries({ color: "#b48cff", lineWidth: 1, priceLineVisible: false });
  [30, 70].forEach(p => rsiS.createPriceLine({ price: p, color: "#4a5560", lineStyle: 2, lineWidth: 1, axisLabelVisible: false }));
  chart.timeScale().subscribeVisibleLogicalRangeChange(r => r && rsiChart.timeScale().setVisibleLogicalRange(r));
  new ResizeObserver(resizeCharts).observe($("#chartMain"));
}
function resizeCharts() {
  if (!chart) return;
  chart.applyOptions({ width: $("#chartMain").clientWidth });
  rsiChart.applyOptions({ width: $("#chartRsi").clientWidth });
}
const sma = (a, n) => a.map((_, i) => i < n - 1 ? null : a.slice(i - n + 1, i + 1).reduce((s, x) => s + x, 0) / n);
function rsiCalc(c, n = 14) {
  const out = Array(c.length).fill(null); let up = 0, dn = 0;
  for (let i = 1; i < c.length; i++) {
    const d = c[i] - c[i - 1], u = Math.max(d, 0), w = Math.max(-d, 0);
    up = i === 1 ? u : (up * (n - 1) + u) / n; dn = i === 1 ? w : (dn * (n - 1) + w) / n;
    if (i >= n) out[i] = dn === 0 ? 100 : 100 - 100 / (1 + up / dn);
  }
  return out;
}
async function openChart(code) {
  code = (code || "").toUpperCase().trim().replace(".JK", "");
  if (!BY[code]) { if (code) alert("Kode " + code + " tidak ditemukan"); return; }
  showTab("chart"); initCharts();
  curCode = code; $("#codeInput").value = code; location.hash = "chart=" + code;
  const r = BY[code];
  $("#quoteHead").innerHTML = `<div class="nm">${r.name}</div>
    <span class="big">${fmtN(r.close)}</span> <span class="${cls(r.ret1d)}">${fmtN(r.close - r.prev)} (${pct(r.ret1d)})</span>`;
  updStar();
  const stats = [["Open", fmtN(r.open)], ["High", fmtN(r.high)], ["Low", fmtN(r.low)],
    ["Volume", fmtB(r.volume)], ["Value", fmtB(r.value)], ["Vol/rata2", fmtN(r.volRatio, 2) + "x"],
    ["RSI 14", fmtN(r.rsi14, 1)], ["Stoch %K", fmtN(r.stochK, 1)], ["MACD hist", fmtN(r.macdHist, 1)],
    ["MA20", fmtN(r.ma20)], ["MA50", fmtN(r.ma50)], ["MA200", fmtN(r.ma200)],
    ["High 52M", fmtN(r.high52)], ["Low 52M", fmtN(r.low52)], ["Batas ARA", r.araLimit + "%"],
    ["Mkt cap", fmtB(r.mcap)], ["PER", fmtN(r.per, 1)], ["PBV", fmtN(r.pbv, 2)],
    ["ROE", r.roe == null ? "-" : fmtN(r.roe, 1) + "%"], ["EPS", fmtN(r.eps, 1)], ["Div yield", r.divYield ? fmtN(r.divYield, 2) + "%" : "-"],
    ["% 5H", pct(r.ret5d)], ["% 20H", pct(r.ret20d)], ["% 60H", pct(r.ret60d)]];
  $("#stats").innerHTML = stats.map(([k, v]) => `<div><small>${k}</small>${v}</div>`).join("");
  try {
    curHist = await (await fetch(`data/hist/${code}.json?v=${DATA.meta.updated}`)).json();
  } catch { curHist = []; }
  drawChart();
}
function drawChart() {
  if (!chart || !curHist) return;
  const h = curHist, c = h.map(x => x[4]);
  const m20 = sma(c, 20), m50 = sma(c, 50), rs = rsiCalc(c);
  candle.setData(h.map(x => ({ time: x[0], open: x[1], high: x[2], low: x[3], close: x[4] })));
  vol.setData(h.map(x => ({ time: x[0], value: x[5], color: x[4] >= x[1] ? "rgba(22,199,132,.35)" : "rgba(234,57,67,.35)" })));
  ma20s.setData(h.map((x, i) => m20[i] == null ? { time: x[0] } : { time: x[0], value: m20[i] }));
  ma50s.setData(h.map((x, i) => m50[i] == null ? { time: x[0] } : { time: x[0], value: m50[i] }));
  rsiS.setData(h.map((x, i) => rs[i] == null ? { time: x[0] } : { time: x[0], value: rs[i] }));
  const n = h.length;
  chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - range), to: n + 2 });
  resizeCharts();
}
$$("#rangeChips button").forEach(b => b.onclick = () => {
  $$("#rangeChips button").forEach(x => x.classList.toggle("on", x === b));
  range = +b.dataset.r; drawChart();
});
$("#codeInput").addEventListener("change", e => openChart(e.target.value));
$("#codeInput").addEventListener("keydown", e => { if (e.key === "Enter") openChart(e.target.value); });

// ---------- watchlist
const wl = () => store.get("watch", []);
function updStar() { $("#starBtn").textContent = wl().includes(curCode) ? "★" : "☆"; }
$("#starBtn").onclick = () => {
  if (!curCode) return;
  let w = wl(); w = w.includes(curCode) ? w.filter(x => x !== curCode) : [...w, curCode];
  store.set("watch", w); updStar();
};
function renderWatch() {
  const rows = wl().map(c => BY[c]).filter(Boolean);
  $("#watchEmpty").hidden = rows.length > 0;
  renderList($("#watchList"), rows);
}

// ---------- alert
async function renderAlerts() {
  const repo = DATA.meta.repo;
  $("#aEdit").href = repo ? `https://github.com/${repo}/edit/main/alerts.yml` : "#";
  let cfg = {}, log = [];
  try { cfg = await (await fetch("data/alerts_config.json?v=" + Date.now())).json(); } catch {}
  try { log = await (await fetch("data/alerts_log.json?v=" + Date.now())).json(); } catch {}
  const names = Object.fromEntries(allPresets().map(p => [p.id, p.name]));
  const lab = { above: "harga >", below: "harga <", rsi_above: "RSI >", rsi_below: "RSI <", change_above: "naik >", change_below: "turun <" };
  const parts = [];
  (cfg.screeners || []).forEach(id => parts.push(`📊 Screener: <b>${names[id] || id}</b>`));
  (cfg.saham || []).forEach(s => Object.entries(s).forEach(([k, v]) => {
    if (k !== "kode") { const r = BY[String(s.kode).toUpperCase()];
      parts.push(`🔔 <b>${s.kode}</b> ${lab[k] || k} ${v}${k.startsWith("change") ? "%" : ""} <span class="muted small">(kini ${r ? (k.startsWith("rsi") ? fmtN(r.rsi14, 1) : k.startsWith("change") ? pct(r.ret1d) : fmtN(r.close)) : "-"})</span>`); }
  }));
  $("#alertActive").innerHTML = parts.join("<br>") || '<span class="muted">Belum ada alert.</span>';
  $("#alertLog").innerHTML = log.length ? `<div class="loglist">${log.slice(0, 20).map(e => `<div class="card"><small class="muted">${e.time}</small><br>${e.items.map(i =>
    i.type === "screener" ? `📊 ${i.name}: ${i.codes.join(", ")}` : `🔔 ${i.code} ${lab[i.cond] || i.cond} ${i.target} (harga ${fmtN(i.close)})`).join("<br>")}</div>`).join("")}</div>`
    : '<p class="muted">Belum ada alert terkirim.</p>';
}
$("#aMake").onclick = async () => {
  const c = $("#aCode").value.toUpperCase().trim(), v = $("#aVal").value;
  if (!c || v === "") return alert("Isi kode dan nilai");
  const txt = `  - kode: ${c}\n    ${$("#aCond").value}: ${v}`;
  $("#aOut").hidden = false; $("#aOut").textContent = txt;
  try { await navigator.clipboard.writeText(txt); $("#aMake").textContent = "Tersalin ✓"; setTimeout(() => $("#aMake").textContent = "Buat & salin", 1500); } catch {}
};

// ---------- jurnal
let JR = null, jSetup = "ALL";
const STAGE = { WATCH: "⏳ watchlist", BOUNCE: "🟢 memantul", FAILED: "🔴 gagal", OUT: "⚠️ bandar keluar",
  SKIP: "⏭️ dilewati broksum", DONE: "⚪ selesai", SIGNAL: "📊 sinyal" };
function jStats(rows, key) {
  const g = {};
  rows.filter(r => r.result != null).forEach(r => { const k = key ? (r[key] ?? "-") : "Total"; (g[k] = g[k] || []).push(r.result); });
  return Object.entries(g).sort().map(([k, v]) => [k, v.length, v.filter(x => x > 0).length / v.length * 100, v.reduce((a, b) => a + b, 0) / v.length]);
}
function jTable(title, rows) {
  if (!rows.length) return "";
  return `<table class="jt"><tr><th>${title}</th><th>Sinyal</th><th>Menang</th><th>Rata-rata</th></tr>${rows.map(([k, n, w, a]) =>
    `<tr><td>${k}</td><td>${n}</td><td>${fmtN(w)}%</td><td class="${cls(a)}">${pct(a)}</td></tr>`).join("")}</table>`;
}
async function renderJurnal() {
  if (!JR) { try { JR = await (await fetch("data/jurnal.json?v=" + Date.now())).json(); } catch { JR = []; } }
  const repo = DATA.meta.repo;
  $("#jCsv").href = repo ? `https://github.com/${repo}/blob/main/jurnal/jurnal.csv` : "#";
  const rows = JR.filter(r => jSetup === "ALL" || r.setup === jSetup);
  const done = rows.filter(r => r.result != null);
  const party = rows.filter(r => r.setup === "PARTY"), bbs = rows.filter(r => r.setup === "BBS");
  const funnel = party.length ? `<div class="card small" style="margin:8px 0">PARTY: ${party.length} masuk watchlist · ${party.filter(r => r.t_bounce).length} memantul ·
    ${party.filter(r => r.t_out).length} bandar keluar · ${party.filter(r => r.broksum_ok === false).length} dilewati broksum · ${party.filter(r => r.t_fail).length} gagal</div>` : "";
  const lk = bbs.filter(r => r.locked != null);
  const bbsInfo = lk.length ? `<div class="card small" style="margin:8px 0">BBS: ${bbs.length} sinyal · terkunci ARA saat tutup ${fmtN(lk.filter(r => r.locked).length / lk.length * 100)}%</div>` : "";
  $("#jSummary").innerHTML = (rows.length ? "" : '<p class="muted">Jurnal masih kosong — terisi mulai sesi bursa berikutnya.</p>') + funnel + bbsInfo +
    (done.length ? jTable("Total", jStats(done)) + jTable("Setup", jStats(done, "setup")) + jTable("Band harga", jStats(done, "band")) +
      jTable("Sesi", jStats(done, "session")) + jTable("Broksum", jStats(done.filter(r => r.setup === "PARTY").map(r => ({ ...r, bk: r.broksum_ok === true ? "lolos" : r.broksum_ok === false ? "tidak" : "tanpa data" })), "bk"))
      : (rows.length ? '<p class="muted">Belum ada catatan yang hasilnya lengkap (BBS terkunci menunggu open besok).</p>' : "")) +
    '<p class="muted small">Pembanding uji: BBS 59 hari +1,38% (menang 51%) · PARTY 1m ketat 19 hari −0,26% (menang 49%).</p>';
  $("#jList").innerHTML = rows.slice().reverse().slice(0, 60).map(r => `<div class="jrow" data-c="${r.code}">
    <b>${r.code}</b> <span class="muted">${r.setup} · ${r.date} ${r.time_in} · ${r.band} · ${r.session}</span><br>
    masuk ${fmtN(r.price_in)} (${pct(r.ret_in)}) · ${STAGE[r.stage] || r.stage}${r.t_bounce ? ` ${r.t_bounce} @${fmtN(r.bounce_price)}` : ""}
    ${r.close != null ? ` · tutup ${fmtN(r.close)}${r.locked ? " (ARA)" : ""}` : ""}${r.n_open != null ? ` · open besok ${fmtN(r.n_open)}` : ""}
    ${r.result != null ? ` · <b class="${cls(r.result)}">${pct(r.result)}</b>` : ""}</div>`).join("");
  $$(".jrow", $("#jList")).forEach(el => el.onclick = () => openChart(el.dataset.c));
}
$$("#jSetup button").forEach(b => b.onclick = () => {
  $$("#jSetup button").forEach(x => x.classList.toggle("on", x === b)); jSetup = b.dataset.s; renderJurnal();
});

// ---------- boot
async function boot() {
  try {
    const [s, p] = await Promise.all([fetch("data/summary.json?v=" + Date.now()).then(r => r.json()), fetch("screeners.json").then(r => r.json())]);
    DATA = s; PRESETS = p.presets;
  } catch (e) { $("#upd").textContent = "Gagal memuat data"; return; }
  BY = Object.fromEntries(DATA.rows.map(r => [r.code, r]));
  $("#upd").innerHTML = `Data: ${DATA.meta.updated}<br>${DATA.meta.count} saham · tertunda ±15 mnt`;
  $("#codeList").innerHTML = DATA.rows.map(r => `<option value="${r.code}">${r.name}</option>`).join("");
  const h = location.hash.slice(1), [k, v] = h.split("=");
  selectPreset(k === "screener" && v ? v : "bbs");
  renderAlerts();
  if (k === "chart" && v) openChart(v);
  else if (k === "alert") showTab("alert");
  else if (k === "jurnal") showTab("jurnal");
}
boot();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
