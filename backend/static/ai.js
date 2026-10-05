// SENTINEL-X — Analyse IA (maintenance prédictive), alarme présence, vision, MCO.
// Fichier séparé d'app.js : sa propre connexion WebSocket (canaux risk / settings).
(() => {
  const NODE = "sentinel-node-01";
  const STALE_MS = 15000;
  const SEVERITY = { gaz_fumee: 5, thermique: 4, anomalie: 3, prediction: 2, normal: 1, warmup: 0 };
  const STATE_LABEL = {
    normal: "normal", prediction: "prédiction", gaz_fumee: "gaz / fumée",
    thermique: "thermique", anomalie: "anomalie", warmup: "chauffe",
  };
  const $ = (id) => document.getElementById(id);
  const ai = {};          // device -> {payload, rx}
  const hist = {};        // device -> [[t, risk]]
  let shown = NODE;

  // ---------------------------------------------------------------- sparkline risque
  const spark = new Chart($("aiSpark").getContext("2d"), {
    type: "line",
    data: { labels: [], datasets: [
      { data: [], borderColor: "#111111", borderWidth: 1.25, pointRadius: 0, tension: 0.25, fill: false, spanGaps: true },
      { data: [], borderColor: "#b8b6ab", borderWidth: 1, borderDash: [3, 3], pointRadius: 0, fill: false },
    ] },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: { display: false },
        y: { min: 0, max: 100, ticks: { color: "#6b6a64", stepSize: 50, font: { size: 9, family: "DejaVu Sans Mono, monospace" } },
             grid: { color: "#e0ded4" }, border: { color: "#e0ded4" } },
      },
    },
  });

  function pickDevice() {
    const now = Date.now();
    let best = NODE, bestSev = -1;
    for (const [dev, v] of Object.entries(ai)) {
      if (now - v.rx > STALE_MS) continue;
      const sev = SEVERITY[v.payload.state] ?? 0;
      if (sev > bestSev || (sev === bestSev && dev === NODE)) { best = dev; bestSev = sev; }
    }
    return best;
  }

  function render() {
    shown = pickDevice();
    const v = ai[shown];
    const fresh = v && Date.now() - v.rx < STALE_MS;
    const p = fresh ? v.payload : null;
    const st = p ? p.state : null;
    const risk = p && p.risk != null ? Math.round(p.risk) : null;
    $("aiRisk").textContent = risk == null ? "—" : String(risk);
    $("aiRisk").className = "ai-risk" + (st && SEVERITY[st] >= 3 ? " alarm" : st === "prediction" ? " pred" : "");
    $("aiState").textContent = st ? STATE_LABEL[st] || st : "pas de données";
    $("aiState").className = "ai-state" + (st && SEVERITY[st] >= 2 ? " alarm" : "");
    $("aiDevice").textContent = shown + (shown !== NODE ? " (test)" : "");
    if (p && p.state === "warmup") $("aiState").textContent = `chauffe ${p.warmup_pct ?? 0} %`;
    const top = p && p.top_features && SEVERITY[st] >= 2 ? p.top_features.slice(0, 2).map((f) => `${f.feature} z=${f.z}`).join(" · ") : "—";
    $("aiTop").textContent = top;
    if (p && p.model) {
      const m = p.model;
      $("aiModel").textContent = `${m.type} · ${m.n_samples} fenêtres (${m.n_real} réelles) · ${m.n_features} features · contamination ${m.contamination}`;
    }
    $("aiInfer").textContent = p && p.infer_ms != null ? `${p.infer_ms} ms` : "—";
    // sparkline
    const h = (hist[shown] || []).slice(-300);
    spark.data.labels = h.map((x) => x[0]);
    spark.data.datasets[0].data = h.map((x) => x[1]);
    spark.data.datasets[1].data = h.map(() => 50);
    spark.update();
    // bandeau d'alarme (tous équipements confondus)
    const now = Date.now();
    const active = Object.values(ai).filter((x) => now - x.rx < STALE_MS).map((x) => x.payload);
    const gas = active.find((x) => x.state === "gaz_fumee");
    const therm = active.find((x) => x.state === "thermique");
    const banner = $("alarmBanner");
    if (gas) {
      banner.hidden = false;
      $("alarmTitle").textContent = "ALERTE GAZ / FUMÉE";
      $("alarmDetail").textContent = `${gas.device_id} · risque ${Math.round(gas.risk)} · ${gas.alarm ? "sirène active" : gas.silenced ? "sirène coupée (réarmement 60 s)" : "sirène en attente"}`;
    } else if (therm) {
      banner.hidden = false;
      $("alarmTitle").textContent = "DÉRIVE THERMIQUE — INCIDENT PRÉDIT";
      $("alarmDetail").textContent = `${therm.device_id} · risque ${Math.round(therm.risk)}`;
    } else {
      banner.hidden = true;
    }
  }

  function onRisk(payload) {
    const dev = payload.device_id || NODE;
    ai[dev] = { payload, rx: Date.now() };
    if (payload.risk != null) {
      (hist[dev] = hist[dev] || []).push([Date.now(), payload.risk]);
      if (hist[dev].length > 450) hist[dev].shift();
    }
    render();
  }

  async function loadAi() {
    const r = await fetch("/api/v1/ai", { credentials: "same-origin" });
    if (!r.ok) return;
    const body = await r.json();
    const now = Date.now();
    for (const [dev, p] of Object.entries(body.devices || {})) {
      ai[dev] = { payload: p, rx: now - (p.age_s || 0) * 1000 };
    }
    for (const [dev, h] of Object.entries(body.history || {})) {
      hist[dev] = h.map(([t, r]) => [t * 1000, r]);
    }
    render();
  }

  // ---------------------------------------------------------------- alarme présence
  const toggle = $("personToggle");
  function applyPerson(enabled) {
    toggle.setAttribute("aria-pressed", enabled ? "true" : "false");
    toggle.classList.toggle("armed", !!enabled);
    $("personState").textContent = enabled ? "ARMÉE" : "DÉSARMÉE";
    $("personState").className = "toggle-state" + (enabled ? " armed" : "");
  }
  async function loadPerson() {
    const r = await fetch("/api/v1/settings/person-alarm", { credentials: "same-origin" });
    if (r.ok) applyPerson((await r.json()).enabled);
  }
  toggle.addEventListener("click", async () => {
    const next = toggle.getAttribute("aria-pressed") !== "true";
    toggle.disabled = true;
    try {
      const r = await fetch("/api/v1/settings/person-alarm", {
        method: "PUT", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: next }),
      });
      if (r.status === 401) { location.href = "/login"; return; }
      if (r.ok) applyPerson((await r.json()).enabled);
    } finally { toggle.disabled = false; }
  });

  // ---------------------------------------------------------------- vision (temps d'inférence)
  function camBase() {
    localStorage.removeItem("sentinelCamUrl");
    return `${location.origin}/cam`;
  }
  async function pollVision() {
    try {
      const r = await fetch(`${camBase()}/health`, { cache: "no-store" });
      const h = await r.json();
      const ms = h.infer_ms;
      $("visInfer").textContent = ms != null ? `${ms.toFixed(0)} ms` : "—";
      $("visInfer").className = "mco-v big" + (ms != null && ms >= 100 ? " alarm" : "");
      $("visDetail").textContent = `max ${h.infer_ms_max ?? "—"} ms · ${h.infer_fps ?? "—"} inf/s · flux ${h.fps ?? "—"} FPS`;
      $("visFrame").textContent = h.infer_frame ? `${h.infer_frame[0]}×${h.infer_frame[1]} · ${h.model}` : h.model || "—";
      $("visPersons").textContent = h.persons ?? "—";
    } catch {
      $("visInfer").textContent = "—";
      $("visDetail").textContent = "service vision injoignable";
    }
  }

  // ---------------------------------------------------------------- MCO
  function pct(id, v) {
    $(id).textContent = v == null ? "—" : `${Math.round(v)} %`;
    $(id).className = "mco-v" + (v != null && v >= 90 ? " alarm" : "");
    const bar = $(id + "Bar");
    if (bar) bar.style.width = `${Math.min(100, v || 0)}%`;
  }
  async function pollSystem() {
    try {
      const r = await fetch("/api/v1/system", { credentials: "same-origin" });
      if (!r.ok) return;
      const s = await r.json();
      pct("mcoCpu", s.cpu_pct);
      pct("mcoRam", s.mem.used_pct);
      pct("mcoDisk", s.disk.used_pct);
      $("mcoMsgs").textContent = `${s.mqtt.msgs_per_min} msg/min`;
      const up = s.uptime_s; const d = Math.floor(up / 86400), hh = Math.floor((up % 86400) / 3600);
      $("mcoLoad").textContent = `charge ${s.load[0]} · ${s.cpu_count} cœurs · RAM ${s.mem.total_gb} Go · hôte up ${d} j ${hh} h`;
      const list = $("mcoServices");
      list.innerHTML = "";
      for (const [name, v] of Object.entries(s.services)) {
        const li = document.createElement("li");
        const extra = v.latency_ms != null ? `${v.latency_ms} ms` : v.status || "";
        li.innerHTML = `<span class="dot ${v.ok ? "ok" : "ko"}"></span><span>${name}</span><span class="muted">${v.ok ? "ok" : "KO"} ${extra}</span>`;
        list.appendChild(li);
      }
    } catch { /* ignore */ }
  }


  // ---------------------------------------------------------------- sources d'alarme
  const srcBox = $("alarmSources");
  function applySources(src) {
    if (!src || !srcBox) return;
    srcBox.querySelectorAll("input[data-src]").forEach((inp) => {
      const k = inp.getAttribute("data-src");
      if (k in src) inp.checked = !!src[k];
    });
  }
  async function loadSources() {
    try {
      const r = await fetch("/api/v1/settings/sources", { credentials: "same-origin" });
      if (!r.ok) return;
      const d = await r.json();
      applySources(d.sources || d);
    } catch (_) {}
  }
  if (srcBox) {
    srcBox.addEventListener("change", async (ev) => {
      const inp = ev.target;
      if (!(inp instanceof HTMLInputElement) || !inp.dataset.src) return;
      const body = {};
      body[inp.dataset.src] = inp.checked;
      inp.disabled = true;
      try {
        const r = await fetch("/api/v1/settings/sources", {
          method: "PUT", credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (r.ok) applySources((await r.json()).sources);
      } catch (_) {
        inp.checked = !inp.checked;
      } finally { inp.disabled = false; }
    });
  }
  loadSources();

  // ---------------------------------------------------------------- WebSocket dédié
  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onmessage = (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.channel === "risk" && msg.data && typeof msg.data === "object") onRisk(msg.data);
      else if (msg.channel === "settings" && msg.data) {
        if ("person_alarm_enabled" in msg.data) applyPerson(msg.data.person_alarm_enabled);
        if (msg.data.sources) applySources(msg.data.sources);
      }
    };
    ws.onclose = () => setTimeout(connect, 2000);
    ws.onerror = () => ws.close();
  }

  loadAi(); loadPerson(); pollVision(); pollSystem(); connect();
  setInterval(render, 2000);
  setInterval(pollVision, 3000);
  setInterval(pollSystem, 5000);
})();
