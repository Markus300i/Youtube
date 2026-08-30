const state = {
  projects: [],
  projectId: null,
  scenes: [],
  selectedId: null,
  audit: null,
  ops: null,
  view: "dashboard",
  pollTimer: null,
  polling: false,
  taskLogTaskId: null,
};

const $ = (id) => document.getElementById(id);
const projectSelect = $("projectSelect");
const sceneGrid = $("sceneGrid");
const scenePanel = $("scenePanel");
const projectMeta = $("projectMeta");
const toastEl = $("toast");
const historyDialog = $("historyDialog");
const historyContent = $("historyContent");
const opsDashboard = $("opsDashboard");
const taskPanel = $("taskPanel");
const taskLogDialog = $("taskLogDialog");
const taskLogContent = $("taskLogContent");

const SHOT_TYPES = ["wide", "medium", "close_up", "detail", "pov", "over_shoulder", "reveal", "twist"];
const CAMERA_TYPES = ["static", "slow_push", "slow_pull", "push_in", "pan_left", "pan_right", "micro_handheld"];
const PURPOSE_TYPES = ["story", "establish", "evidence", "character", "tension", "reveal", "orientation_reset", "twist"];
const MOTION_TYPES = ["static", "slow_push", "slow_pull", "push_in", "pan_left", "pan_right", "micro_handheld"];
const MOTION_INTENSITIES = ["none", "low", "medium", "high"];

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

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;","\"":"&quot;"}[c]));
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

function selectOptions(values, current) {
  return values.map(value => `<option value="${value}" ${value === current ? "selected" : ""}>${value}</option>`).join("");
}

function switchView(view) {
  state.view = view;
  document.querySelectorAll(".view").forEach(el => el.classList.add("hidden"));
  $(`${view}View`).classList.remove("hidden");
  document.querySelectorAll(".view-tab").forEach(btn => btn.classList.toggle("active", btn.dataset.view === view));
}

async function loadProjects() {
  state.projects = await api("/api/projects");
  projectSelect.innerHTML = state.projects.map(p => `<option value="${p.project_id}">${p.project_id} — ${escapeHtml(p.title)}</option>`).join("");
  if (!state.projects.length) {
    opsDashboard.innerHTML = `<div class="empty-state">Brak projektów w csp-studio.db.</div>`;
    return;
  }
  if (!state.projectId || !state.projects.some(p => p.project_id === state.projectId)) {
    state.projectId = state.projects[0].project_id;
  }
  projectSelect.value = state.projectId;
  await loadProjectData();
}

async function loadProjectData({ keepSelection = true } = {}) {
  const [scenes, audit, ops] = await Promise.all([
    api(`/api/projects/${state.projectId}/scenes`),
    api(`/api/projects/${state.projectId}/shot-audit`),
    api(`/api/projects/${state.projectId}/ops-dashboard`),
  ]);
  state.scenes = scenes;
  state.audit = audit;
  state.ops = ops;
  const project = state.projects.find(p => p.project_id === state.projectId);
  const review = ops.review || { approved: 0, total: scenes.length };
  projectMeta.textContent = `${project?.title || state.projectId} · Review ${review.approved}/${review.total} · Shot QA ${audit.score}/100`;

  if (!keepSelection || !state.scenes.some(s => s.scene_id === state.selectedId)) {
    state.selectedId = state.scenes[0]?.scene_id ?? null;
  }
  renderDashboard();
  renderTasks();
  renderSceneGrid();
  renderSelected();
  syncTaskPolling();
}

function syncTaskPolling() {
  const active = (state.ops?.tasks || []).some(task => task.state === "queued" || task.state === "running");
  if (active && !state.pollTimer) {
    state.pollTimer = setInterval(async () => {
      if (state.polling) return;
      state.polling = true;
      try { await loadProjectData(); } catch (_) {}
      finally { state.polling = false; }
    }, 2000);
  } else if (!active && state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

function readinessClass(check) {
  if (check.ok) return "ok";
  return check.blocking ? "block" : "warn";
}

function renderDashboard() {
  const ops = state.ops;
  if (!ops) return;
  const agent = ops.agent;
  const review = ops.review;
  const vqa = ops.visual_qa || {};
  const memory = ops.memory || {};
  const pipeline = ops.pipeline || [];
  const checks = agent.checks || [];
  const blocking = (agent.blockers || []).length;
  const nextButtonLabel = agent.next_action === "review_scenes" ? "Przejdź do review" : "Uruchom następny krok";
  const visualScore = vqa.available ? `${vqa.score}/100` : "—";
  const memoryText = memory.comparison_available
    ? `${memory.previous_project_ids.length} wcześniejszych projektów w pamięci`
    : "Brak wcześniejszych projektów do porównania";

  opsDashboard.innerHTML = `
    <div class="dashboard-head">
      <div>
        <div class="eyebrow">AGENT ONE</div>
        <h2>${escapeHtml(agent.title)}</h2>
        <p class="muted">Stage: <strong>${escapeHtml(agent.stage)}</strong> · Blockers: ${blocking}</p>
      </div>
      <button id="runNextBtn" class="primary">${nextButtonLabel}</button>
    </div>

    <div class="kpi-grid">
      <div class="kpi-card"><span>Review scen</span><strong>${review.approved}/${review.total}</strong><small>${review.pending_ids.length ? `Pending: ${review.pending_ids.join(", ")}` : "Gotowe"}</small></div>
      <div class="kpi-card"><span>Visual QA</span><strong>${visualScore}</strong><small>${escapeHtml(vqa.aggregate_status || "not run")}</small></div>
      <div class="kpi-card"><span>Shot QA</span><strong>${state.audit?.score ?? 0}/100</strong><small>${state.audit?.warnings?.length || 0} ostrzeżeń</small></div>
      <div class="kpi-card"><span>Tasks</span><strong>${ops.tasks.length}</strong><small>${ops.tasks.filter(t => t.state === "queued" || t.state === "running").length} aktywnych</small></div>
    </div>

    <div class="dashboard-grid">
      <section class="ops-card next-card">
        <div class="eyebrow">NEXT ACTION</div>
        <h3>${escapeHtml(agent.next_action)}</h3>
        <p>${escapeHtml(agent.next_action_detail)}</p>
      </section>

      <section class="ops-card">
        <div class="eyebrow">UNIVERSE MEMORY</div>
        <h3>${memory.total_items || 0} wpisów</h3>
        <p>${escapeHtml(memoryText)}</p>
        <div class="muted">Bieżący projekt: ${memory.current_project_items || 0} · Poprzednie: ${memory.previous_project_items || 0}</div>
      </section>

      <section class="ops-card full-span">
        <div class="eyebrow">READINESS</div>
        <div class="readiness-list">
          ${checks.map(check => `
            <div class="readiness-row ${readinessClass(check)}">
              <span class="readiness-dot"></span>
              <div><strong>${escapeHtml(check.label)}</strong><div class="muted">${escapeHtml(check.detail)}</div></div>
              <span>${check.ok ? "OK" : (check.blocking ? "BLOCK" : "WARN")}</span>
            </div>
          `).join("")}
        </div>
      </section>

      <section class="ops-card full-span">
        <div class="eyebrow">PIPELINE FRESHNESS</div>
        <div class="pipeline-list">
          ${pipeline.map(item => `
            <div class="pipeline-row">
              <div>
                <strong>${escapeHtml(item.label)}</strong>
                <div class="muted">${escapeHtml(item.check_detail || item.detail)}</div>
                ${item.freshness_reason ? `<div class="pipeline-reason">${escapeHtml(item.freshness_reason)}</div>` : ""}
              </div>
              <div class="pipeline-meta">
                <span class="pipeline-state ${escapeHtml(item.state)}">${escapeHtml(String(item.state).toUpperCase())}</span>
                <small>${item.checkpoint_updated_at ? escapeHtml(item.checkpoint_updated_at) : "brak checkpointu"}</small>
              </div>
            </div>
          `).join("")}
        </div>
      </section>

      <section class="ops-card full-span">
        <div class="eyebrow">VISUAL QA</div>
        ${vqa.available ? `
          <div class="visual-qa-summary">
            <strong>${vqa.score}/100</strong>
            <p>${escapeHtml(vqa.summary || "Raport Visual QA zapisany.")}</p>
          </div>
          ${(vqa.warnings || []).length ? `<ul class="compact-list">${vqa.warnings.map(w => `<li>${escapeHtml(w)}</li>`).join("")}</ul>` : ""}
        ` : `<p class="muted">Brak zapisanego raportu Visual QA.</p>`}
      </section>
    </div>
  `;
  $("runNextBtn").addEventListener("click", runNextAction);
}

async function scheduleTask(taskId) {
  const result = await api(`/api/tasks/${encodeURIComponent(taskId)}/run`, { method: "POST" });
  toast(`Uruchomiono ${result.task.stage}`);
  await loadProjectData();
  switchView("tasks");
}

async function runNextAction() {
  const agent = state.ops?.agent;
  if (!agent) return;
  if (agent.next_action === "review_scenes") {
    const pending = state.ops.review?.pending_ids || [];
    if (pending.length) state.selectedId = pending[0];
    switchView("scenes");
    renderSceneGrid();
    renderSelected();
    toast("Review scen jest aktualnym krokiem Agent One");
    return;
  }
  try {
    const result = await api(`/api/projects/${state.projectId}/agent/enqueue-next`, { method: "POST" });
    if (result.queued) {
      toast(`Task ${result.task.stage} dodany do kolejki`);
      await scheduleTask(result.task.task_id);
    } else if (result.reason === "already_queued" && result.task?.state === "queued") {
      await scheduleTask(result.task.task_id);
    } else {
      toast(`Nie uruchomiono taska: ${result.reason}`);
      await loadProjectData();
      switchView("tasks");
    }
  } catch (err) {
    alert(`Nie udało się uruchomić następnego kroku: ${err.message}`);
  }
}

function taskButtons(task) {
  if (task.state === "queued") {
    return `<button class="ghost task-action" data-task="${escapeHtml(task.task_id)}" data-action="run">Run</button><button class="danger task-action" data-task="${escapeHtml(task.task_id)}" data-action="cancel">Cancel</button>`;
  }
  if (task.state === "running") {
    return `<button class="danger task-action" data-task="${escapeHtml(task.task_id)}" data-action="cancel">Cancel</button>`;
  }
  if (task.state === "failed") {
    return `<button class="primary task-action" data-task="${escapeHtml(task.task_id)}" data-action="retry">Retry</button>`;
  }
  return "";
}

function renderTasks() {
  const tasks = state.ops?.tasks || [];
  if (!tasks.length) {
    taskPanel.innerHTML = `<div class="empty-state">Brak tasków dla tego projektu.</div>`;
    return;
  }
  taskPanel.innerHTML = tasks.map(task => `
    <article class="task-row">
      <div class="task-main">
        <div><strong>${escapeHtml(task.stage)}</strong> <span class="task-state ${escapeHtml(task.state)}">${escapeHtml(task.state)}</span></div>
        <div class="muted">${escapeHtml(task.task_id)} · ${escapeHtml(task.resource)}${task.scene_id ? ` · scene ${task.scene_id}` : ""}</div>
        ${task.failed_stage ? `<div class="task-step">${task.state === "failed" ? "Błąd na etapie" : "Bieżący krok"}: ${escapeHtml(task.failed_stage)}</div>` : ""}
        <div class="muted task-timing">Utworzono: ${escapeHtml(task.created_at)}${task.started_at ? ` · Start: ${escapeHtml(task.started_at)}` : ""}</div>
        ${task.error ? `<div class="task-error">${escapeHtml(task.error)}</div>` : ""}
        ${task.result?.log_path ? `<div class="muted">Log: ${escapeHtml(task.result.log_path)}</div>` : ""}
        <div class="task-actions">
          ${taskButtons(task)}
          <button class="ghost task-log-button" data-task="${escapeHtml(task.task_id)}">Pokaż log</button>
        </div>
      </div>
      <div class="task-progress"><strong>${task.progress}%</strong><div class="progress-track"><span style="width:${Math.max(0, Math.min(100, task.progress))}%"></span></div></div>
    </article>
  `).join("");

  document.querySelectorAll(".task-action").forEach(button => {
    button.addEventListener("click", () => taskAction(button.dataset.task, button.dataset.action));
  });
  document.querySelectorAll(".task-log-button").forEach(button => {
    button.addEventListener("click", () => openTaskLog(button.dataset.task));
  });
}

async function refreshTaskLog() {
  const taskId = state.taskLogTaskId;
  if (!taskId) return;
  taskLogContent.innerHTML = `<div class="empty-state">Ładowanie logu…</div>`;
  try {
    const data = await api(`/api/tasks/${encodeURIComponent(taskId)}/log`);
    $("taskLogTitle").textContent = `${data.stage} · ${data.state}`;
    if (!data.available) {
      taskLogContent.innerHTML = `<div class="empty-state">Log nie jest jeszcze dostępny.</div>`;
      return;
    }
    const truncation = data.truncated
      ? `<div class="task-log-note">Pokazano ostatnie ${Math.round(data.max_bytes / 1024)} KiB z ${data.size_bytes} bajtów.</div>`
      : `<div class="task-log-note">${data.size_bytes} bajtów · sekrety są maskowane</div>`;
    taskLogContent.innerHTML = `${truncation}<pre class="task-log-output">${escapeHtml(data.content || "Log jest pusty.")}</pre>`;
  } catch (err) {
    taskLogContent.innerHTML = `<div class="task-error">${escapeHtml(err.message)}</div>`;
  }
}

async function openTaskLog(taskId) {
  state.taskLogTaskId = taskId;
  $("taskLogTitle").textContent = taskId;
  taskLogDialog.showModal();
  await refreshTaskLog();
}

async function taskAction(taskId, action) {
  try {
    if (action === "run") {
      await scheduleTask(taskId);
      return;
    }
    const result = await api(`/api/tasks/${encodeURIComponent(taskId)}/${action}`, { method: "POST" });
    toast(action === "retry" ? `Ponowiono ${result.task.stage}` : `Anulowano ${result.task.stage}`);
    await loadProjectData();
    switchView("tasks");
  } catch (err) {
    alert(`Task ${action} nie powiódł się: ${err.message}`);
  }
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

function visualNoteFor(sceneId) {
  const notes = state.ops?.visual_qa?.scene_notes || [];
  return notes.find(note => Number(note.scene_id) === Number(sceneId)) || null;
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
  const audit = state.audit || { score: 0, warnings: [] };
  const warnings = audit.warnings || [];
  const visualNote = visualNoteFor(scene.scene_id);
  scenePanel.innerHTML = `
    <img class="panel-preview" src="${scene.image_url}" alt="Scena ${scene.scene_id}" />
    <div class="panel-head">
      <div>
        <div class="eyebrow">SCENE ${String(scene.scene_id).padStart(2, "0")}</div>
        <h2>${escapeHtml(scene.shot?.purpose || "story")}</h2>
      </div>
      <span class="badge ${scene.status}">${statusLabel(scene.status)}</span>
    </div>

    ${visualNote ? `
      <div class="detail-block visual-note ${escapeHtml(visualNote.severity || "info")}">
        <h3>Visual QA · ${escapeHtml(visualNote.severity || "info")}</h3>
        <p>${escapeHtml(visualNote.issue || "")}</p>
        ${visualNote.recommendation ? `<p class="muted visual-recommendation">${escapeHtml(visualNote.recommendation)}</p>` : ""}
      </div>
    ` : ""}

    <div class="detail-block">
      <h3>Narracja</h3>
      <p>${escapeHtml(scene.text)}</p>
    </div>

    <div class="detail-block editor-block">
      <div class="editor-heading">
        <h3>Prompt sceny</h3>
        <span class="revision-pill">Scene r${scene.scene_revision}</span>
      </div>
      <textarea id="promptInput" class="prompt-input" rows="9">${escapeHtml(scene.prompt || "")}</textarea>
    </div>

    <div class="detail-block">
      <h3>Shot Director</h3>
      <div class="editor-grid">
        <label>Shot type<select id="shotTypeInput">${selectOptions(SHOT_TYPES, scene.shot?.shot_type || "medium")}</select></label>
        <label>Camera<select id="cameraInput">${selectOptions(CAMERA_TYPES, scene.shot?.camera || "static")}</select></label>
        <label>Purpose<select id="purposeInput">${selectOptions(PURPOSE_TYPES, scene.shot?.purpose || "story")}</select></label>
        <label>Motion<select id="motionInput">${selectOptions(MOTION_TYPES, scene.motion || "static")}</select></label>
        <label>Motion intensity<select id="motionIntensityInput">${selectOptions(MOTION_INTENSITIES, scene.shot?.motion_intensity || "low")}</select></label>
        <label>Visual anchor<input id="visualAnchorInput" type="text" value="${escapeHtml(scene.shot?.visual_anchor || scene.continuity_refs?.[0] || "")}" /></label>
      </div>
      <button id="saveSceneBtn" class="primary save-scene">Zapisz scenę</button>
    </div>

    <div class="detail-block qa-block ${audit.ok ? "qa-ok" : "qa-warn"}">
      <div class="qa-head"><h3>Shot QA</h3><strong>${audit.score}/100</strong></div>
      ${warnings.length ? `<details><summary>${warnings.length} ostrzeżeń</summary><ul>${warnings.map(w => `<li>${escapeHtml(w)}</li>`).join("")}</ul></details>` : `<p class="muted">Brak ostrzeżeń Shot Directora.</p>`}
    </div>

    <div class="detail-block">
      <h3>Aktywny asset</h3>
      <div class="meta-grid">
        <div class="meta-item"><span>Image rev.</span>${asset ? `r${asset.revision}` : "—"}</div>
        <div class="meta-item"><span>Source</span>${asset ? escapeHtml(asset.source) : "—"}</div>
      </div>
      <p class="muted asset-path">${asset ? escapeHtml(asset.path) : "Brak aktywnego obrazu"}</p>
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

  $("saveSceneBtn").addEventListener("click", saveSelectedScene);
  $("replaceFile").addEventListener("change", replaceSelected);
  $("approveBtn").addEventListener("click", () => mutateSelected("approve"));
  $("regenBtn").addEventListener("click", regenerateSelected);
  $("historyBtn").addEventListener("click", showHistory);
}

async function saveSelectedScene() {
  const scene = selectedScene();
  if (!scene) return;
  const payload = {
    prompt: $("promptInput").value,
    motion: $("motionInput").value,
    shot: {
      shot_type: $("shotTypeInput").value,
      camera: $("cameraInput").value,
      purpose: $("purposeInput").value,
      visual_anchor: $("visualAnchorInput").value || null,
      motion_intensity: $("motionIntensityInput").value,
    },
    note: "Scene plan edited in CSP Studio GUI",
  };
  try {
    const result = await api(`/api/projects/${state.projectId}/scenes/${scene.scene_id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await loadProjectData();
    toast(result.changed ? `Scena ${scene.scene_id}: zapisano nową rewizję` : "Brak zmian do zapisania");
  } catch (err) {
    alert(`Nie udało się zapisać sceny: ${err.message}`);
  }
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
    await loadProjectData();
    toast(`Scena ${scene.scene_id}: nowy obraz aktywny`);
  } catch (err) {
    alert(`Import nie powiódł się: ${err.message}`);
  }
}

async function mutateSelected(action) {
  const scene = selectedScene();
  if (!scene) return;
  const form = new FormData();
  form.append("note", "Approved in CSP Studio GUI");
  try {
    await api(`/api/projects/${state.projectId}/scenes/${scene.scene_id}/${action}`, { method: "POST", body: form });
    await loadProjectData();
    toast("Scena zatwierdzona");
  } catch (err) {
    alert(err.message);
  }
}

async function regenerateSelected() {
  const scene = selectedScene();
  if (!scene) return;
  const form = new FormData();
  form.append("note", "Regenerate requested in CSP Studio GUI");
  try {
    const result = await api(`/api/projects/${state.projectId}/scenes/${scene.scene_id}/regenerate`, { method: "POST", body: form });
    await loadProjectData();
    if (result.task.state === "queued") {
      await scheduleTask(result.task.task_id);
    } else {
      toast(`Regenerate już trwa dla sceny ${scene.scene_id}`);
      switchView("tasks");
    }
  } catch (err) {
    alert(`Regenerate nie powiódł się: ${err.message}`);
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
    const revisionHtml = data.scene_revisions.length ? data.scene_revisions.slice(0, 16).map(r => `
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

projectSelect.addEventListener("change", async () => {
  state.projectId = projectSelect.value;
  state.selectedId = null;
  await loadProjectData({ keepSelection: false });
});
$("refreshBtn").addEventListener("click", () => loadProjects().catch(err => alert(err.message)));
$("closeHistory").addEventListener("click", () => historyDialog.close());
$("refreshTaskLog").addEventListener("click", refreshTaskLog);
$("closeTaskLog").addEventListener("click", () => {
  taskLogDialog.close();
  state.taskLogTaskId = null;
});
document.querySelectorAll(".view-tab").forEach(btn => btn.addEventListener("click", () => switchView(btn.dataset.view)));

loadProjects().catch(err => {
  opsDashboard.innerHTML = `<div class="empty-state">Nie udało się uruchomić Studio: ${escapeHtml(err.message)}</div>`;
});
