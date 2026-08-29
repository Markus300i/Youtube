const state = { projects: [], projectId: null, scenes: [], selectedId: null };

const $ = (id) => document.getElementById(id);
const projectSelect = $("projectSelect");
const sceneGrid = $("sceneGrid");
const scenePanel = $("scenePanel");
const projectMeta = $("projectMeta");
const toastEl = $("toast");
const historyDialog = $("historyDialog");
const historyContent = $("historyContent");

function toast(message) {
  toastEl.textContent = message;
  toastEl.classList.add("show");
  setTimeout(() => toastEl.classList.remove("show"), 2200);
}

async function api(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try { const data = await res.json(); detail = data.detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  const type = res.headers.get("content-type") || "";
  return type.includes("application/json") ? res.json() : res;
}

function statusLabel(status) {
  const map = {
    draft: "Draft",
    generated: "Generated",
    approved: "Approved",
    needs_regeneration: "Regenerate",
    render_ready: "Render ready",
  };
  return map[status] || status;
}

async function loadProjects() {
  state.projects = await api("/api/projects");
  projectSelect.innerHTML = state.projects.map(p => `<option value="${p.project_id}">${p.project_id} — ${p.title}</option>`).join("");
  if (!state.projects.length) {
    sceneGrid.innerHTML = `<div class="empty-state">Brak projektów w csp-studio.db.</div>`;
    return;
  }
  if (!state.projectId || !state.projects.some(p => p.project_id === state.projectId)) {
    state.projectId = state.projects[0].project_id;
  }
  projectSelect.value = state.projectId;
  await loadScenes();
}

async function loadScenes({ keepSelection = true } = {}) {
  state.scenes = await api(`/api/projects/${state.projectId}/scenes`);
  const project = state.projects.find(p => p.project_id === state.projectId);
  projectMeta.textContent = `${project?.title || state.projectId} · ${state.scenes.length} scen`;

  if (!keepSelection || !state.scenes.some(s => s.scene_id === state.selectedId)) {
    state.selectedId = state.scenes[0]?.scene_id ?? null;
  }
  renderSceneGrid();
  renderSelected();
}

function renderSceneGrid() {
  sceneGrid.innerHTML = state.scenes.map(scene => `
    <article class="scene-card ${scene.scene_id === state.selectedId ? "active" : ""}" data-scene="${scene.scene_id}">
      <img class="scene-thumb" src="${scene.image_url}" alt="Scena ${scene.scene_id}" onerror="this.style.opacity=.15" />
      <div class="scene-card-body">
        <div class="scene-card-top">
          <span class="scene-num">SCENE ${String(scene.scene_id).padStart(2, "0")}</span>
          <span class="badge ${scene.status}">${statusLabel(scene.status)}</span>
        </div>
        <div class="scene-text">${escapeHtml(scene.text)}</div>
      </div>
    </article>
  `).join("");

  document.querySelectorAll(".scene-card").forEach(card => {
    card.addEventListener("click", () => {
      state.selectedId = Number(card.dataset.scene);
      renderSceneGrid();
      renderSelected();
    });
  });
}

function selectedScene() {
  return state.scenes.find(s => s.scene_id === state.selectedId);
}

function renderSelected() {
  const scene = selectedScene();
  if (!scene) {
    scenePanel.className = "scene-panel empty";
    scenePanel.innerHTML = `<div class="empty-state">Wybierz scenę.</div>`;
    return;
  }
  scenePanel.className = "scene-panel";
  const asset = scene.active_asset;
  scenePanel.innerHTML = `
    <img class="panel-preview" src="${scene.image_url}" alt="Scena ${scene.scene_id}" />
    <div class="panel-head">
      <div>
        <div class="eyebrow">SCENE ${String(scene.scene_id).padStart(2, "0")}</div>
        <h2>${escapeHtml(scene.shot?.purpose || "story")}</h2>
      </div>
      <span class="badge ${scene.status}">${statusLabel(scene.status)}</span>
    </div>

    <div class="detail-block">
      <h3>Narracja</h3>
      <p>${escapeHtml(scene.text)}</p>
    </div>

    <div class="detail-block">
      <h3>Shot Director</h3>
      <div class="meta-grid">
        <div class="meta-item"><span>Shot</span>${escapeHtml(scene.shot?.shot_type || "—")}</div>
        <div class="meta-item"><span>Camera</span>${escapeHtml(scene.shot?.camera || scene.motion || "—")}</div>
        <div class="meta-item"><span>Scene rev.</span>r${scene.scene_revision}</div>
        <div class="meta-item"><span>Image rev.</span>${asset ? `r${asset.revision}` : "—"}</div>
      </div>
    </div>

    <div class="detail-block">
      <h3>Aktywny asset</h3>
      <p class="muted">${asset ? `${escapeHtml(asset.source)} · ${escapeHtml(asset.path)}` : "Brak aktywnego obrazu"}</p>
    </div>

    <div class="actions">
      <label class="file-label">Import / Replace Image<input id="replaceFile" type="file" accept="image/png,image/jpeg,image/webp" /></label>
      <div class="action-row">
        <button id="approveBtn" class="success">Approve</button>
        <button id="regenBtn" class="danger">Regenerate</button>
      </div>
      <button id="historyBtn" class="ghost">Historia wersji</button>
    </div>
  `;

  $("replaceFile").addEventListener("change", replaceSelected);
  $("approveBtn").addEventListener("click", () => mutateSelected("approve"));
  $("regenBtn").addEventListener("click", () => mutateSelected("regenerate"));
  $("historyBtn").addEventListener("click", showHistory);
}

async function replaceSelected(event) {
  const file = event.target.files?.[0];
  const scene = selectedScene();
  if (!file || !scene) return;
  const form = new FormData();
  form.append("file", file);
  form.append("source", "gpt-browser-manual");
  form.append("note", "Imported from CSP Studio GUI");
  try {
    toast("Importuję nową wersję sceny…");
    await api(`/api/projects/${state.projectId}/scenes/${scene.scene_id}/replace`, { method: "POST", body: form });
    await loadScenes();
    toast(`Scena ${scene.scene_id}: nowy obraz aktywny`);
  } catch (err) {
    alert(`Import nie powiódł się: ${err.message}`);
  }
}

async function mutateSelected(action) {
  const scene = selectedScene();
  if (!scene) return;
  const form = new FormData();
  form.append("note", action === "approve" ? "Approved in CSP Studio GUI" : "Marked for regeneration in CSP Studio GUI");
  try {
    await api(`/api/projects/${state.projectId}/scenes/${scene.scene_id}/${action}`, { method: "POST", body: form });
    await loadScenes();
    toast(action === "approve" ? "Scena zatwierdzona" : "Scena oznaczona do regeneracji");
  } catch (err) {
    alert(err.message);
  }
}

async function showHistory() {
  const scene = selectedScene();
  if (!scene) return;
  try {
    const data = await api(`/api/projects/${state.projectId}/scenes/${scene.scene_id}/history`);
    $("historyTitle").textContent = `Scena ${String(scene.scene_id).padStart(2, "0")}`;
    const assetHtml = data.assets.length ? data.assets.map(a => `
      <div class="history-entry">
        <strong>Image r${a.revision} ${a.active ? "· ACTIVE" : ""}</strong>
        <div class="muted">${escapeHtml(a.source)} · ${escapeHtml(a.status)}</div>
        <code>${escapeHtml(a.path)}</code>
      </div>`).join("") : `<div class="empty-state">Brak assetów.</div>`;
    const revisionHtml = data.scene_revisions.length ? data.scene_revisions.slice(0, 12).map(r => `
      <div class="history-entry">
        <strong>Scene r${r.revision} · ${escapeHtml(r.action)}</strong>
        <div class="muted">${escapeHtml(r.note || "")}</div>
      </div>`).join("") : `<div class="empty-state">Brak rewizji sceny.</div>`;
    historyContent.innerHTML = `<h3>Assety</h3>${assetHtml}<h3>Zmiany sceny</h3>${revisionHtml}`;
    historyDialog.showModal();
  } catch (err) {
    alert(err.message);
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;","\"":"&quot;"}[c]));
}

projectSelect.addEventListener("change", async () => {
  state.projectId = projectSelect.value;
  state.selectedId = null;
  await loadScenes({ keepSelection: false });
});
$("refreshBtn").addEventListener("click", () => loadProjects().catch(err => alert(err.message)));
$("closeHistory").addEventListener("click", () => historyDialog.close());

loadProjects().catch(err => {
  sceneGrid.innerHTML = `<div class="empty-state">Nie udało się uruchomić Studio: ${escapeHtml(err.message)}</div>`;
});
