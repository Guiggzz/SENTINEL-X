(() => {
  const WINDOW_MS = 5 * 60 * 1000;
  const DEVICE_ID = "sentinel-node-01";

  const el = (id) => document.getElementById(id);
  const connBadge = el("connBadge");
  const clock = el("clock");
  const eventLog = el("eventLog");
  const cmdFeedback = el("cmdFeedback");

  const state = {
    points: [],
    nodeOnline: null,
  };

  function nowLabel(d = new Date()) {
    return d.toLocaleTimeString("fr-FR", { hour12: false });
  }

  function setClock() {
    clock.textContent = nowLabel();
  }
  setInterval(setClock, 1000);
  setClock();

  function setText(id, text, cls = "") {
    const node = el(id);
    node.textContent = text;
    node.className = `value ${cls}`.trim();
  }

  function boolLabel(v, on = "ON", off = "OFF") {
    if (v === true) return on;
    if (v === false) return off;
    return "—";
  }

  function trimWindow() {
    const cutoff = Date.now() - WINDOW_MS;
    state.points = state.points.filter((p) => p.t >= cutoff);
  }

  function pushPoint(telemetry, ts) {
    const t = ts ? new Date(ts).getTime() : Date.now();
    state.points.push({
      t,
      temperature: telemetry.temperature ?? null,
      humidity: telemetry.humidity ?? null,
      gas: telemetry.gas ?? null,
    });
    trimWindow();
    updateCharts();
  }

  function applyTelemetry(data, createdAt) {
    setText("stPresence", boolLabel(data.presence, "détectée", "absente"), data.presence ? "warn" : "ok");
    setText("stBuzzer", boolLabel(data.buzzer), data.buzzer ? "danger" : "muted");
    setText("stLedRed", boolLabel(data.led_red), data.led_red ? "danger" : "muted");
    setText("stLedGreen", boolLabel(data.led_green), data.led_green ? "ok" : "muted");
    setText("stRssi", data.rssi != null ? `${data.rssi} dBm` : "—", "accent");
    setText("stUpdated", createdAt ? new Date(createdAt).toLocaleString("fr-FR") : nowLabel(), "muted");
    pushPoint(data, createdAt);
  }

  function applyStatus(status) {
    const online = String(status).toLowerCase() === "online";
    state.nodeOnline = online;
    setText("stNode", online ? "en ligne" : "hors ligne", online ? "ok" : "danger");
  }

  // libellés lisibles pour les événements visage du service vision
  const STATE_NOTE = {
    face_spoof: " · leurre photo/écran", face_unknown: " · visage inconnu", face_known: " · visage connu",
    identify_start: " · identifiez-vous", identified: " · autorisé", intrusion: " · non identifié",
    intrusion_spoof: " · leurre photo/écran", identify_end: " · fin de présence",
  };
  const alertText = (d) => `${d.device_id || "?"} · ${d.type}/${d.state}${STATE_NOTE[d.state] || ""}`;

  function addLog(tag, message) {
    const li = document.createElement("li");
    const tagClass = tag === "ALERT" ? "tag alert" : "tag";
    // textContent (jamais innerHTML) : les messages viennent du MQTT/API => pas d'injection HTML (XSS)
    const mk = (cls, txt) => { const e = document.createElement("span"); e.className = cls; e.textContent = String(txt); return e; };
    li.append(mk("time", nowLabel()), mk(tagClass, tag), mk("msg", message));
    eventLog.prepend(li);
    while (eventLog.children.length > 200) eventLog.removeChild(eventLog.lastChild);
  }

  const GRID = "#e0ded4";
  const TICK = "#6b6a64";
  const INK = "#111111";

  const chartDefaults = {
    type: "line",
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {
        x: {
          ticks: { color: TICK, maxTicksLimit: 5, font: { size: 10, family: "DejaVu Sans Mono, monospace" } },
          grid: { color: GRID, lineWidth: 1 },
          border: { color: GRID },
        },
        y: {
          ticks: { color: TICK, maxTicksLimit: 5, font: { size: 10, family: "DejaVu Sans Mono, monospace" } },
          grid: { color: GRID, lineWidth: 1 },
          border: { color: GRID },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#111",
          titleColor: "#f2f1ec",
          bodyColor: "#f2f1ec",
          borderWidth: 0,
          displayColors: false,
        },
      },
    },
  };

  function makeChart(canvasId, color, yRange) {
    const ctx = el(canvasId).getContext("2d");
    const cfg = JSON.parse(JSON.stringify(chartDefaults));
    if (yRange) Object.assign(cfg.options.scales.y, yRange);
    return new Chart(ctx, {
      ...cfg,
      data: {
        labels: [],
        datasets: [{
          data: [],
          borderColor: color,
          backgroundColor: "transparent",
          fill: false,
          tension: 0.4,
          cubicInterpolationMode: "monotone",
          pointRadius: 0,
          borderWidth: 1.5,
          spanGaps: true,
        }],
      },
    });
  }

  const chartTemp = makeChart("chartTemp", INK, { min: 0, max: 30 });
  const chartHum = makeChart("chartHum", INK);
  const chartGas = makeChart("chartGas", INK);

  // Lissage (fenetres en echantillons, telemetrie a 0,5 s) : moyenne glissante pour masquer le bruit des capteurs bruts
  function smooth(values, win) {
    const out = [];
    for (let i = 0; i < values.length; i++) {
      let sum = 0, n = 0;
      for (let j = Math.max(0, i - win + 1); j <= i; j++) {
        if (values[j] != null) { sum += values[j]; n++; }
      }
      out.push(n ? Math.round((sum / n) * 10) / 10 : null);
    }
    return out;
  }

  // Échelle Y avec une amplitude minimale, pour que ±1 unité ne remplisse pas tout le graphe
  function fitScale(chart, data, minSpan) {
    const v = data.filter((x) => x != null);
    if (!v.length) return;
    const lo = Math.min(...v), hi = Math.max(...v);
    const mid = (lo + hi) / 2;
    const span = Math.max(hi - lo, minSpan) * 1.2;
    chart.options.scales.y.min = Math.floor(mid - span / 2);
    chart.options.scales.y.max = Math.ceil(mid + span / 2);
  }

  function updateCharts() {
    const labels = state.points.map((p) => nowLabel(new Date(p.t)));
    const temp = smooth(state.points.map((p) => p.temperature), 20);
    const hum = smooth(state.points.map((p) => p.humidity), 32);
    const gas = smooth(state.points.map((p) => p.gas), 40);
    chartTemp.data.labels = labels;
    chartTemp.data.datasets[0].data = temp;
    chartHum.data.labels = labels;
    chartHum.data.datasets[0].data = hum;
    fitScale(chartHum, hum, 10);
    chartGas.data.labels = labels;
    chartGas.data.datasets[0].data = gas;
    fitScale(chartGas, gas, 60);
    chartTemp.update();
    chartHum.update();
    chartGas.update();
  }

  async function loadHistory() {
    const res = await fetch("/api/v1/telemetry?limit=700", { credentials: "same-origin" });
    if (res.status === 401) { location.href = "/login"; return; }
    if (!res.ok) throw new Error("telemetry history failed");
    const rows = await res.json();
    const cutoff = Date.now() - WINDOW_MS;
    const recent = rows
      .filter((r) => new Date(r.created_at).getTime() >= cutoff)
      .reverse();
    state.points = [];
    for (const r of recent) {
      state.points.push({
        t: new Date(r.created_at).getTime(),
        temperature: r.temperature,
        humidity: r.humidity,
        gas: r.gas,
      });
    }
    updateCharts();
    if (rows[0]) applyTelemetry(rows[0], rows[0].created_at);

    const st = await fetch("/api/v1/status", { credentials: "same-origin" });
    if (st.ok) {
      const statuses = await st.json();
      const mine = statuses.find((s) => s.device_id === DEVICE_ID) || statuses[0];
      if (mine) applyStatus(mine.status);
    }

    const al = await fetch("/api/v1/alerts?limit=100", { credentials: "same-origin" });
    if (al.ok) {
      const alerts = await al.json();
      alerts.reverse().forEach((a) => {
        addLog("ALERT", alertText(a));
      });
    }
  }

  function connectWs() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onopen = () => {
      connBadge.textContent = "WS connecté";
      connBadge.className = "badge online";
    };
    ws.onclose = () => {
      connBadge.textContent = "WS déconnecté";
      connBadge.className = "badge offline";
      setTimeout(connectWs, 2000);
    };
    ws.onerror = () => ws.close();
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      const channel = msg.channel;
      const data = msg.data;
      if (channel === "telemetry" && data && typeof data === "object") {
        applyTelemetry(data);
        if (state.nodeOnline === null) applyStatus("online");
      } else if (channel === "alerts" && data && typeof data === "object") {
        addLog("ALERT", alertText(data));
      } else if (channel === "status") {
        applyStatus(data);
        addLog("STATUS", `${msg.topic || DEVICE_ID} → ${data}`);
      }
    };
  }

  document.querySelectorAll(".btn-grid button").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const payload = JSON.parse(btn.dataset.cmd);
      payload.device_id = DEVICE_ID;
      cmdFeedback.textContent = "Envoi…";
      try {
        const res = await fetch("/api/v1/commands", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify(payload),
        });
        if (res.status === 401) { location.href = "/login"; return; }
        const body = await res.json();
        if (!res.ok) throw new Error(body.detail || res.statusText);
        cmdFeedback.textContent = `OK → ${body.topic}`;
        addLog("CMD", JSON.stringify(payload));
      } catch (err) {
        cmdFeedback.textContent = `Erreur: ${err.message}`;
      }
    });
  });

  // Caméra : une seule boucle de trames JPEG, qui alimente l'image visible :
  // aperçu de la Supervision (500 ms) ou onglet Caméra (250 ms). En pause sur les autres
  // onglets et quand la page est masquée. La capture d'enrôlement (Visages) passe par
  // /cam/snapshot_raw.jpg côté serveur et ne dépend pas de ces images.
  function setupCamera() {
    const params = new URLSearchParams(location.search);
    try { localStorage.removeItem("sentinelCamUrl"); } catch (_) {}
    const snap = params.get("cam") || (location.origin + "/cam/snapshot.jpg");
    const targets = {
      cam: { img: el("camFeed"), fallback: el("camFallback"), period: 250 },
      ops: { img: el("camPreview"), fallback: el("camPreviewFallback"), period: 500 },
    };
    let current = null;   // cible active (ou null = en pause)
    let timer = null;
    let fails = 0;
    let objectUrl = null;
    let gen = 0;

    function showFallback(t, msg) {
      t.img.style.display = "none";
      t.fallback.style.display = "grid";
      t.fallback.textContent = msg || "Flux indisponible";
    }
    function showFeed(t) {
      t.img.style.display = "block";
      t.fallback.style.display = "none";
    }

    async function tick(g, t) {
      if (g !== gen) return;
      try {
        const r = await fetch(snap + (snap.includes("?") ? "&" : "?") + "t=" + Date.now(), {
          cache: "no-store",
          credentials: "same-origin",
        });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const blob = await r.blob();
        if (g !== gen) return;
        if (!blob || blob.size < 100) throw new Error("image vide");
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        objectUrl = URL.createObjectURL(blob);
        await new Promise((resolve, reject) => {
          t.img.onload = () => resolve();
          t.img.onerror = () => reject(new Error("decode"));
          t.img.src = objectUrl;
        });
        if (g !== gen) return;
        fails = 0;
        showFeed(t);
        timer = setTimeout(() => tick(g, t), t.period);
      } catch (err) {
        if (g !== gen) return;
        fails += 1;
        showFallback(t, "Flux indisponible (" + (err && err.message ? err.message : err) + ")");
        timer = setTimeout(() => tick(g, t), fails >= 3 ? 3000 : 800);
      }
    }

    // name = onglet affiché ; la boucle suit la cible correspondante, s'il y en a une
    function follow(name) {
      const next = document.hidden ? null : (targets[name] || null);
      if (next === current) return;
      gen += 1;
      clearTimeout(timer);
      timer = null;
      if (current) {
        current.img.removeAttribute("src");
        showFallback(current, "Flux en pause");
      }
      if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
      current = next;
      if (current) {
        fails = 0;
        tick(gen, current);
      }
    }

    Object.values(targets).forEach((t) => showFallback(t, "Flux en pause"));
    return { follow };
  }

  // ---- onglets : data-tab="x" -> section #tab-x ; l'onglet courant est gardé dans l'ancre (#cam, #log…)
  const tabButtons = [...document.querySelectorAll(".tab")];
  const tabNames = tabButtons.map((b) => b.dataset.tab);
  function showTab(name, remember) {
    if (!tabNames.includes(name)) name = "ops";
    tabButtons.forEach((b) => {
      const on = b.dataset.tab === name;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    tabNames.forEach((n) => {
      const page = el("tab-" + n);
      if (page) page.hidden = n !== name;
    });
    if (remember) {
      const hash = name === "ops" ? "" : "#" + name;
      history.replaceState(null, "", location.pathname + location.search + hash);
    }
    // les autres modules (faces.js, ai.js) écoutent cet événement
    window.dispatchEvent(new CustomEvent("sentinel:tab", { detail: name }));
    window.dispatchEvent(new Event("resize"));
  }
  tabButtons.forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab, true)));
  window.addEventListener("hashchange", () => showTab(location.hash.slice(1), false));

  const camera = setupCamera();
  let currentTab = "ops";
  document.addEventListener("visibilitychange", () => camera.follow(currentTab));
  const camLink = document.querySelector(".cam-link");
  if (camLink) camLink.addEventListener("click", (ev) => { ev.preventDefault(); showTab("cam", true); });
  window.addEventListener("sentinel:tab", (ev) => {
    currentTab = ev.detail;
    camera.follow(ev.detail);
    // graphiques créés pendant que la page était masquée : recalcul de taille à l'affichage
    if (ev.detail === "ops") [chartTemp, chartHum, chartGas].forEach((c) => c.resize());
  });
  // après le chargement de tous les scripts (faces.js écoute aussi l'événement)
  document.addEventListener("DOMContentLoaded", () => showTab(location.hash.slice(1), false));

  loadHistory().catch((err) => {
    addLog("ERR", err.message);
  });
  connectWs();
})();
