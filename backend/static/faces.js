// SENTINEL-X — galerie de visages et alarme visage inconnu.
(() => {
  const $ = (id) => document.getElementById(id);

  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      const name = btn.getAttribute("data-tab");
      document.querySelectorAll(".tab").forEach((other) => {
        const on = other === btn;
        other.classList.toggle("is-on", on);
        other.setAttribute("aria-selected", on ? "true" : "false");
      });
      $("tab-ops").hidden = name !== "ops";
      $("tab-faces").hidden = name !== "faces";
      if (name === "faces") loadPeople();
      window.dispatchEvent(new Event("resize"));
    });
  });

  const toggle = $("faceAlarmToggle");
  function applyFaceAlarm(enabled) {
    toggle.setAttribute("aria-pressed", enabled ? "true" : "false");
    toggle.classList.toggle("armed", !!enabled);
    $("faceAlarmState").textContent = enabled ? "ARMÉE" : "DÉSARMÉE";
    $("faceAlarmState").className = "toggle-state" + (enabled ? " armed" : "");
  }
  window.applyFaceAlarm = applyFaceAlarm;

  async function loadFaceAlarm() {
    const r = await fetch("/api/v1/settings/face-alarm", { credentials: "same-origin" });
    if (r.status === 401) { location.href = "/login"; return; }
    if (r.ok) applyFaceAlarm((await r.json()).enabled);
  }
  toggle.addEventListener("click", async () => {
    const next = toggle.getAttribute("aria-pressed") !== "true";
    toggle.disabled = true;
    try {
      const r = await fetch("/api/v1/settings/face-alarm", {
        method: "PUT",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      });
      if (r.status === 401) { location.href = "/login"; return; }
      if (r.ok) applyFaceAlarm((await r.json()).enabled);
    } finally {
      toggle.disabled = false;
    }
  });

  function renderStatus(s) {
    const ready = s && s.face_ready !== false && s.status !== "unavailable";
    let text = "Reconnaissance indisponible";
    let kind = "off";
    if (ready && s.status === "known") {
      text = `Connu · ${s.name || "?"}`;
      kind = "known";
    } else if (ready && s.status === "unknown") {
      text = "Inconnu";
      kind = "unknown";
    } else if (ready) {
      text = "Aucun visage";
      kind = "none";
    }
    const live = $("faceLive");
    live.textContent = text;
    live.className = "face-status" + (kind === "unknown" ? " unknown" : "");
    const line = $("faceStatusLine");
    if (line) {
      line.textContent = `Visage · ${text}`;
      line.className = "mco-sub" + (kind === "unknown" ? " alarm" : "");
    }
    const score = $("faceScore");
    if (!ready) {
      score.textContent = s && s.model ? s.model : "modèles absents sur le service vision";
    } else if (s.score != null && s.threshold != null) {
      score.textContent = `similarité ${Number(s.score).toFixed(2)} · seuil ${Number(s.threshold).toFixed(2)} · galerie ${s.gallery ?? 0}`;
    } else {
      score.textContent = `seuil ${s.threshold != null ? Number(s.threshold).toFixed(2) : "—"} · galerie ${s.gallery ?? 0}`;
    }
  }

  async function pollStatus() {
    try {
      const r = await fetch("/api/v1/faces/status", { credentials: "same-origin", cache: "no-store" });
      if (r.status === 401) return;
      if (!r.ok) {
        renderStatus({ face_ready: false, status: "unavailable", model: "service vision injoignable" });
        return;
      }
      renderStatus(await r.json());
    } catch {
      renderStatus({ face_ready: false, status: "unavailable" });
    }
  }

  function emptyItem(text) {
    const li = document.createElement("li");
    li.className = "face-empty";
    li.textContent = text;
    return li;
  }

  async function loadPeople() {
    const list = $("faceList");
    list.replaceChildren();
    let people = [];
    try {
      const r = await fetch("/api/v1/faces", { credentials: "same-origin", cache: "no-store" });
      if (r.status === 401) { location.href = "/login"; return; }
      if (!r.ok) {
        list.appendChild(emptyItem("Galerie injoignable."));
        return;
      }
      const body = await r.json();
      people = body.people || [];
    } catch {
      list.appendChild(emptyItem("Galerie injoignable."));
      return;
    }
    if (!people.length) {
      list.appendChild(emptyItem("Aucune personne enrôlée."));
      return;
    }
    for (const person of people) {
      const li = document.createElement("li");
      const img = document.createElement("img");
      img.alt = "";
      img.src = `/api/v1/faces/${encodeURIComponent(person.id)}/thumb`;
      const text = document.createElement("div");
      const who = document.createElement("span");
      who.className = "who";
      who.textContent = person.name || person.id;
      const meta = document.createElement("span");
      meta.className = "meta";
      meta.textContent = `${person.samples || 1} photo(s)`;
      text.append(who, meta);
      const del = document.createElement("button");
      del.type = "button";
      del.textContent = "Retirer";
      del.addEventListener("click", async () => {
        if (!window.confirm(`Retirer ${person.name} de la galerie ?`)) return;
        del.disabled = true;
        const r = await fetch(`/api/v1/faces/${encodeURIComponent(person.id)}`, {
          method: "DELETE",
          credentials: "same-origin",
        });
        if (r.status === 401) { location.href = "/login"; return; }
        await loadPeople();
      });
      li.append(img, text, del);
      list.appendChild(li);
    }
  }

  $("faceForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const msg = $("faceFormMsg");
    const name = $("faceName").value.trim();
    const files = $("facePhotos").files;
    if (!name || !files || !files.length) {
      msg.textContent = "Nom et au moins une photo.";
      return;
    }
    const body = new FormData();
    body.append("name", name);
    for (const file of files) body.append("photo", file);
    msg.textContent = "Enrôlement…";
    const r = await fetch("/api/v1/faces", { method: "POST", credentials: "same-origin", body });
    if (r.status === 401) { location.href = "/login"; return; }
    let payload = {};
    try { payload = await r.json(); } catch { /* réponse vide */ }
    if (!r.ok) {
      msg.textContent = payload.error || "Échec de l'enrôlement.";
      return;
    }
    msg.textContent = `${payload.name} enrôlé (${payload.samples} photo(s)).`;
    $("faceForm").reset();
    await loadPeople();
  });

  loadFaceAlarm();
  loadPeople();
  pollStatus();
  setInterval(pollStatus, 1000);
})();
