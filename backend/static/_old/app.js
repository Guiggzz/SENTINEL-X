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

  function addLog(tag, message) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="time">${nowLabel()}</span><span class="tag">${tag}</span>${message}`;
    eventLog.prepend(li);
    while (eventLog.children.length > 80) eventLog.removeChild(eventLog.lastChild);
  }

  const chartDefaults = {
    type: "line",
    options: {
      responsive: true,
      animation: false,
      scales: {
        x: {
          ticks: { color: "#8aa0c2", maxTicksLimit: 6 },
          grid: { color: "rgba(30,45,74,0.6)" },
        },
        y: {
          ticks: { color: "#8aa0c2" },
          grid: { color: "rgba(30,45,74,0.6)" },
        },
      },
      plugins: {
        legend: { display: false },
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
          backgroundColor: color + "33",
          tension: 0.4,
          cubicInterpolationMode: "monotone",
          pointRadius: 0,
          borderWidth: 2,
          spanGaps: true,
        }],
      },
    });
  }

  const chartTemp = makeChart("chartTemp", "#3be0c0", { min: 0, max: 30 });
  const chartHum = makeChart("chartHum", "#4f8cff");
  const chartGas = makeChart("chartGas", "#ffb020");

  // Lissage : moyenne glissante pour masquer le bruit des capteurs bruts
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
    const temp = smooth(state.points.map((p) => p.temperature), 5);
    const hum = smooth(state.points.map((p) => p.humidity), 8);
    const gas = smooth(state.points.map((p) => p.gas), 15);
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
    const res = await fetch("/api/v1/telemetry?limit=200");
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

    const st = await fetch("/api/v1/status");
    if (st.ok) {
      const statuses = await st.json();
      const mine = statuses.find((s) => s.device_id === DEVICE_ID) || statuses[0];
      if (mine) applyStatus(mine.status);
    }

    const al = await fetch("/api/v1/alerts?limit=30");
    if (al.ok) {
      const alerts = await al.json();
      alerts.reverse().forEach((a) => {
        addLog("ALERT", `${a.device_id} · ${a.type}/${a.state}`);
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
        addLog("ALERT", `${data.device_id || "?"} · ${data.type}/${data.state}`);
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
          body: JSON.stringify(payload),
        });
        const body = await res.json();
        if (!res.ok) throw new Error(body.detail || res.statusText);
        cmdFeedback.textContent = `OK → ${body.topic}`;
        addLog("CMD", JSON.stringify(payload));
      } catch (err) {
        cmdFeedback.textContent = `Erreur: ${err.message}`;
      }
    });
  });

  function setupCamera() {
    const params = new URLSearchParams(location.search);
    const defaultCam = `http://${location.hostname}:8081/stream.mjpg`;
    const url = params.get("cam") || localStorage.getItem("sentinelCamUrl") || defaultCam;
    const img = el("camFeed");
    const fallback = el("camFallback");
    let retryTimer = null;

    function showFallback(msg) {
      img.style.display = "none";
      fallback.style.display = "grid";
      fallback.textContent = msg || "Flux indisponible";
    }

    function showFeed() {
      img.style.display = "block";
      fallback.style.display = "none";
    }

    function attach(src) {
      img.onload = () => showFeed();
      img.onerror = () => {
        showFallback("Flux indisponible");
        if (retryTimer) clearTimeout(retryTimer);
        retryTimer = setTimeout(() => {
          // cache-bust to force reconnect
          img.src = src + (src.includes("?") ? "&" : "?") + "t=" + Date.now();
        }, 3000);
      };
      img.src = src;
    }

    attach(url);
  }

  loadHistory().catch((err) => {
    addLog("ERR", err.message);
  });
  connectWs();
  setupCamera();
})();
