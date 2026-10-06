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
      setCamLive(name === "faces");
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
    setPresent(s);
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
    const names = $("facePeopleNames");
    if (names) {
      names.replaceChildren(...people.map((person) => {
        const opt = document.createElement("option");
        opt.value = person.name || person.id;
        opt.label = `${person.samples || 0} photo(s)`;
        return opt;
      }));
    }
    if (!people.length) {
      list.appendChild(emptyItem("Aucune personne enrôlée."));
      return;
    }
    for (const person of people) list.appendChild(buildBadge(person));
    applyPresence();
  }

  // ---- badges d'accès : code stable dérivé de l'identifiant (FNV-1a 32 bits)
  function badgeCode(key) {
    let h = 0x811c9dc5;
    for (const ch of String(key)) {
      h ^= ch.codePointAt(0);
      h = Math.imul(h, 0x01000193) >>> 0;
    }
    return "SX-" + (h % 0x10000).toString(16).toUpperCase().padStart(4, "0");
  }

  function formatDate(value) {
    if (!value) return "";
    const d = new Date(typeof value === "number" && value < 1e12 ? value * 1000 : value);
    if (Number.isNaN(d.getTime())) return "";
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()}`;
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function buildBadge(person) {
    const name = person.name || person.id;
    const samples = person.samples || 0;
    const li = el("li", "idcard");
    li.dataset.name = name.toLowerCase();

    const band = el("div", "idcard-band");
    band.append(el("span", null, "SENTINEL-X"), el("span", null, "Accès autorisé"));

    const photo = el("div", "idcard-photo");
    const img = document.createElement("img");
    img.alt = `Photo d'enrôlement de ${name}`;
    img.loading = "lazy";
    img.src = `/api/v1/faces/${encodeURIComponent(person.id)}/thumb`;
    photo.appendChild(img);

    const info = el("div", "idcard-info");
    info.appendChild(el("p", "idcard-name", name));
    const fields = el("dl", "idcard-fields");
    const rows = [
      ["ID", badgeCode(person.id || name)],
      ["Niveau", "Opérateur"],
      ["Photos", String(samples).padStart(2, "0")],
    ];
    const enrolled = formatDate(person.created_at);
    if (enrolled) rows.push(["Enrôlé", enrolled]);
    for (const [k, v] of rows) fields.append(el("dt", null, k), el("dd", null, v));
    info.appendChild(fields);

    const body = el("div", "idcard-body");
    body.append(photo, info);

    const add = el("button", "idcard-act", "+ Photos");
    add.type = "button";
    add.title = "Ajouter des photos à cette personne";
    add.addEventListener("click", () => {
      $("faceName").value = name;
      $("faceCaptureMsg").textContent = `${name} · ${samples} photo(s). Capturer pour en ajouter.`;
      $("faceCaptureBtn").focus();
    });
    const del = el("button", "idcard-act danger", "Retirer");
    del.type = "button";
    del.addEventListener("click", async () => {
      if (!window.confirm(`Retirer ${name} de la galerie ?`)) return;
      del.disabled = true;
      const r = await fetch(`/api/v1/faces/${encodeURIComponent(person.id)}`, {
        method: "DELETE",
        credentials: "same-origin",
      });
      if (r.status === 401) { location.href = "/login"; return; }
      await loadPeople();
    });
    const actions = el("div", "idcard-actions");
    actions.append(add, del);
    const code = el("span", "idcard-code");
    code.setAttribute("aria-hidden", "true");
    const foot = el("div", "idcard-foot");
    foot.append(code, actions);

    const tag = el("span", "idcard-present", "Présent");
    li.append(band, body, foot, tag);
    return li;
  }

  // nom(s) reconnu(s) en direct -> badge surligné
  let presentNames = new Set();
  function applyPresence() {
    document.querySelectorAll("#faceList .idcard").forEach((card) => {
      card.classList.toggle("is-present", presentNames.has(card.dataset.name));
    });
  }
  function setPresent(s) {
    const next = new Set();
    if (s && s.status === "known" && s.name) {
      for (const n of String(s.name).split(",")) if (n.trim()) next.add(n.trim().toLowerCase());
    }
    const same = next.size === presentNames.size && [...next].every((n) => presentNames.has(n));
    if (same) return;
    presentNames = next;
    applyPresence();
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

  // ---- capture caméra : trame brute -> enrôlement (une photo par clic)
  let camTimer = null;
  let camUrl = null;
  async function fetchFrame(raw) {
    const paths = raw ? ["/cam/snapshot_raw.jpg", "/cam/snapshot.jpg"] : ["/cam/snapshot.jpg"];
    for (const path of paths) {
      const r = await fetch(path, { credentials: "same-origin", cache: "no-store" });
      if (r.status === 401) { location.href = "/login"; return null; }
      if (r.ok) return r.blob();
      if (r.status !== 404) break;
    }
    return null;
  }
  async function refreshCam() {
    try {
      const blob = await fetchFrame(false);
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      $("faceCamLive").src = url;
      if (camUrl) URL.revokeObjectURL(camUrl);
      camUrl = url;
    } catch { /* caméra injoignable */ }
  }
  function setCamLive(on) {
    if (camTimer) { clearInterval(camTimer); camTimer = null; }
    if (on && !$("tab-faces").hidden) {
      refreshCam();
      camTimer = setInterval(refreshCam, 700);
    }
  }

  function addCaptureThumb(blob) {
    const li = document.createElement("li");
    const img = document.createElement("img");
    img.alt = "capture";
    img.src = URL.createObjectURL(blob);
    const tag = document.createElement("span");
    tag.textContent = "…";
    li.append(img, tag);
    const strip = $("faceCaptures");
    strip.prepend(li);
    while (strip.children.length > 10) {
      const last = strip.lastElementChild;
      const lastImg = last.querySelector("img");
      if (lastImg) URL.revokeObjectURL(lastImg.src);
      last.remove();
    }
    return { li, tag };
  }

  let lastCaptureName = "";
  $("faceCaptureBtn").addEventListener("click", async () => {
    const btn = $("faceCaptureBtn");
    const msg = $("faceCaptureMsg");
    const name = $("faceName").value.trim();
    if (!name) {
      msg.textContent = "Saisir un nom (ou choisir une personne) avant de capturer.";
      $("faceName").focus();
      return;
    }
    if (name.toLowerCase() !== lastCaptureName.toLowerCase()) {
      $("faceCaptures").replaceChildren();
      lastCaptureName = name;
    }
    btn.disabled = true;
    msg.textContent = "Capture…";
    try {
      const blob = await fetchFrame(true);
      if (!blob) { msg.textContent = "Caméra indisponible."; return; }
      const thumb = addCaptureThumb(blob);
      const body = new FormData();
      body.append("name", name);
      body.append("single", "1");
      body.append("photo", blob, "capture.jpg");
      const r = await fetch("/api/v1/faces", { method: "POST", credentials: "same-origin", body });
      if (r.status === 401) { location.href = "/login"; return; }
      let payload = {};
      try { payload = await r.json(); } catch { /* réponse vide */ }
      if (!r.ok) {
        thumb.li.classList.add("is-bad");
        thumb.tag.textContent = payload.faces > 1 ? `${payload.faces} visages` : "refusée";
        msg.textContent = payload.error || "Capture refusée.";
        return;
      }
      thumb.li.classList.add("is-ok");
      thumb.tag.textContent = `#${payload.samples}`;
      msg.textContent = `${payload.name} · photo ajoutée · ${payload.samples} photo(s) au total.`;
      await loadPeople();
    } catch {
      msg.textContent = "Échec de la capture.";
    } finally {
      btn.disabled = false;
    }
  });

  loadFaceAlarm();
  loadPeople();
  pollStatus();
  setInterval(pollStatus, 1000);
})();
