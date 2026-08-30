(() => {
  const style = document.createElement("style");
  style.textContent = `
    .flow-actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }
    .run-state { display:inline-flex; padding:4px 8px; border:1px solid var(--line); border-radius:999px; font-size:10px; letter-spacing:.08em; text-transform:uppercase; }
    .run-state.running,.run-state.waiting_task { color:var(--warn); border-color:#6e5a2b; }
    .run-state.completed { color:var(--good); border-color:#2f6b4e; }
    .run-state.failed,.run-state.blocked { color:var(--bad); border-color:#744545; }
    .visual-bible-card { margin-top:14px; padding-top:14px; border-top:1px solid var(--line); }
    .bible-entities { display:grid; gap:7px; margin:10px 0; }
    .bible-entity { display:flex; gap:8px; align-items:flex-start; padding:8px; border:1px solid var(--line); border-radius:8px; }
    .bible-entity small { color:var(--muted); display:block; }
    .bible-context { padding:9px; border:1px dashed var(--line); border-radius:8px; white-space:pre-wrap; font-size:11px; color:var(--muted); }
    .bible-create { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:10px; }
    .bible-create textarea { grid-column:1 / -1; min-height:68px; }
    .wizard-dialog { width:min(980px,94vw); max-height:90vh; }
    .wizard-body { overflow:auto; max-height:72vh; display:grid; gap:12px; }
    .wizard-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
    .wizard-grid textarea { min-height:90px; }
    .wizard-scenes { display:grid; gap:10px; }
    .wizard-scene { display:grid; grid-template-columns:90px 1fr 1.4fr; gap:8px; align-items:start; padding:9px; border:1px solid var(--line); border-radius:8px; }
    .wizard-scene textarea { min-height:80px; }
    @media (max-width:760px) { .wizard-grid,.bible-create { grid-template-columns:1fr; } .wizard-scene { grid-template-columns:1fr; } }
  `;
  document.head.appendChild(style);

  let productionBusy = false;
  let bibleBusy = false;
  let injectScheduled = false;

  function ensureWizardDialog() {
    if (document.getElementById("newShortDialog")) return;
    const dialog = document.createElement("dialog");
    dialog.id = "newShortDialog";
    dialog.className = "wizard-dialog";
    const scenes = Array.from({ length: 8 }, (_, i) => `
      <div class="wizard-scene">
        <strong>Scena ${i + 1}</strong>
        <textarea data-wizard-text="${i + 1}" placeholder="Tekst narracji sceny"></textarea>
        <textarea data-wizard-prompt="${i + 1}" placeholder="Prompt obrazu"></textarea>
      </div>
    `).join("");
    dialog.innerHTML = `
      <div class="dialog-head">
        <div><div class="eyebrow">NEW SHORT WIZARD</div><h2>Nowy CSP Short</h2></div>
        <button id="closeNewShort" class="ghost">Zamknij</button>
      </div>
      <div class="wizard-body">
        <div class="wizard-grid">
          <label>ID projektu<input id="wizardId" placeholder="002-nazwa" /></label>
          <label>Tytuł<input id="wizardTitle" placeholder="Tytuł Shorta" /></label>
          <label style="grid-column:1/-1">Narracja 70–160 słów<textarea id="wizardNarration" placeholder="Pełna narracja"></textarea></label>
          <label style="grid-column:1/-1">Styl wizualny<textarea id="wizardStyle">realistyczny polski thriller dokumentalny, cinematic, naturalne światło, desaturated colors, subtle film grain, photorealistic, 9:16</textarea></label>
        </div>
        <div class="wizard-scenes">${scenes}</div>
        <div class="flow-actions">
          <button id="createShortBtn" class="primary">Utwórz projekt</button>
          <span class="muted">Wizard zapisze YAML i SQLite dopiero po walidacji.</span>
        </div>
      </div>
    `;
    document.body.appendChild(dialog);
    dialog.querySelector("#closeNewShort").addEventListener("click", () => dialog.close());
    dialog.querySelector("#createShortBtn").addEventListener("click", createShort);
  }

  function injectWizardButton() {
    const actions = document.querySelector(".top-actions");
    if (!actions || document.getElementById("newShortBtn")) return;
    const button = document.createElement("button");
    button.id = "newShortBtn";
    button.className = "ghost";
    button.textContent = "+ Nowy Short";
    button.addEventListener("click", () => {
      ensureWizardDialog();
      document.getElementById("newShortDialog").showModal();
    });
    actions.insertBefore(button, actions.firstChild);
  }

  async function createShort() {
    const id = document.getElementById("wizardId").value.trim();
    const title = document.getElementById("wizardTitle").value.trim();
    const narration = document.getElementById("wizardNarration").value.trim();
    const visualStyle = document.getElementById("wizardStyle").value.trim();
    const scenes = Array.from({ length: 8 }, (_, i) => ({
      id: i + 1,
      text: document.querySelector(`[data-wizard-text="${i + 1}"]`).value.trim(),
      prompt: document.querySelector(`[data-wizard-prompt="${i + 1}"]`).value.trim(),
      motion: "static",
      continuity_refs: [],
      render: { mode: "generate" },
    }));
    try {
      const result = await api("/api/wizard/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, title, series: "Ciemna Strona Polski", fictional: true, status: "draft", narration, visual_style: visualStyle, scenes }),
      });
      document.getElementById("newShortDialog").close();
      toast(`Utworzono projekt ${result.project.project_id}`);
      state.projectId = result.project.project_id;
      await loadProjects();
      projectSelect.value = state.projectId;
      await loadProjectData({ keepSelection: false });
    } catch (err) {
      alert(`New Short Wizard: ${err.message}`);
    }
  }

  function injectProductionRun() {
    const grid = document.querySelector("#opsDashboard .dashboard-grid");
    if (!grid || document.getElementById("productionRunCard")) return;
    const card = document.createElement("section");
    card.id = "productionRunCard";
    card.className = "ops-card full-span";
    card.innerHTML = `<div class="eyebrow">PRODUCTION RUN</div><div class="muted">Ładowanie…</div>`;
    grid.prepend(card);
    refreshProductionRun().catch(() => {});
  }

  async function productionAction(action) {
    if (!state.projectId) return;
    try {
      const result = await api(`/api/projects/${state.projectId}/production-run/${action}`, { method: "POST" });
      toast(`Production Run: ${result.reason || result.run?.state || action}`);
      await loadProjectData();
      await refreshProductionRun();
    } catch (err) {
      alert(`Production Run: ${err.message}`);
    }
  }

  async function refreshProductionRun() {
    const card = document.getElementById("productionRunCard");
    if (!card || !state.projectId || productionBusy) return;
    productionBusy = true;
    try {
      const data = await api(`/api/projects/${state.projectId}/production-run`);
      const run = data.run;
      const agent = data.agent;
      card.innerHTML = `
        <div class="eyebrow">PRODUCTION RUN</div>
        <div style="display:flex;justify-content:space-between;gap:12px;align-items:start">
          <div><h3>Automatyczny przebieg do następnej bramki człowieka</h3><p class="muted">Agent One: ${escapeHtml(agent.next_action)} · ${escapeHtml(agent.next_action_detail)}</p></div>
          <span class="run-state ${escapeHtml(run.state)}">${escapeHtml(run.state)}</span>
        </div>
        ${run.stop_reason ? `<div class="muted">Stop reason: ${escapeHtml(run.stop_reason)}</div>` : ""}
        <div class="flow-actions">
          <button class="primary" data-run-action="start">Start / Continue</button>
          <button class="ghost" data-run-action="advance" ${run.enabled ? "" : "disabled"}>Advance</button>
          <button class="ghost" data-run-action="stop" ${run.enabled ? "" : "disabled"}>Stop</button>
        </div>
        <div class="muted" style="margin-top:8px">Taski wykonuje Studio Worker. Production Run nie omija review ani deterministic gates.</div>
      `;
      card.querySelectorAll("[data-run-action]").forEach(btn => btn.addEventListener("click", () => productionAction(btn.dataset.runAction)));
    } catch (err) {
      card.innerHTML = `<div class="eyebrow">PRODUCTION RUN</div><div class="muted">${escapeHtml(err.message)}</div>`;
    } finally {
      productionBusy = false;
    }
  }

  function injectVisualBible() {
    const panel = document.getElementById("scenePanel");
    const scene = selectedScene();
    if (!panel || !scene || panel.classList.contains("empty") || document.getElementById("visualBibleCard")) return;
    const card = document.createElement("section");
    card.id = "visualBibleCard";
    card.className = "visual-bible-card";
    card.innerHTML = `<div class="eyebrow">VISUAL BIBLE V2</div><div class="muted">Ładowanie continuity…</div>`;
    panel.appendChild(card);
    refreshVisualBible().catch(() => {});
  }

  async function refreshVisualBible() {
    const card = document.getElementById("visualBibleCard");
    const scene = selectedScene();
    if (!card || !scene || !state.projectId || bibleBusy) return;
    bibleBusy = true;
    try {
      const [bible, sceneBible] = await Promise.all([
        api(`/api/projects/${state.projectId}/visual-bible`),
        api(`/api/projects/${state.projectId}/scenes/${scene.scene_id}/visual-bible`),
      ]);
      const assigned = new Set(sceneBible.entities.map(item => item.entity_key));
      const active = bible.entities.filter(item => item.active && !["style", "rule"].includes(item.kind));
      card.innerHTML = `
        <div class="eyebrow">VISUAL BIBLE V2</div>
        <h3>Continuity sceny ${scene.scene_id}</h3>
        <div class="muted">Style i rules są globalne. Pozostałe encje przypisujesz do sceny.</div>
        <div class="bible-entities">
          ${active.length ? active.map(item => `
            <label class="bible-entity">
              <input type="checkbox" data-bible-key="${escapeHtml(item.entity_key)}" ${assigned.has(item.entity_key) ? "checked" : ""}/>
              <span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.kind)} · ${escapeHtml(item.prompt_fragment || item.description)}</small></span>
            </label>
          `).join("") : `<div class="muted">Brak encji scenowych.</div>`}
        </div>
        <div class="flow-actions"><button id="saveBibleRefs" class="ghost">Zapisz przypisania</button></div>
        <div class="bible-context"><strong>Execution context:</strong> ${escapeHtml(sceneBible.prompt_context || "brak")}</div>
        <div class="bible-create">
          <input id="bibleKey" placeholder="entity_key" />
          <select id="bibleKind">${bible.valid_kinds.map(kind => `<option value="${kind}">${kind}</option>`).join("")}</select>
          <input id="bibleName" placeholder="Nazwa encji" />
          <input id="bibleRef" placeholder="Reference asset path (opcjonalnie)" />
          <textarea id="biblePrompt" placeholder="Stabilny prompt fragment / opis wyglądu"></textarea>
          <button id="createBibleEntity" class="ghost">Dodaj / aktualizuj encję</button>
        </div>
      `;
      card.querySelector("#saveBibleRefs").addEventListener("click", saveBibleAssignments);
      card.querySelector("#createBibleEntity").addEventListener("click", createBibleEntity);
    } catch (err) {
      card.innerHTML = `<div class="eyebrow">VISUAL BIBLE V2</div><div class="muted">${escapeHtml(err.message)}</div>`;
    } finally {
      bibleBusy = false;
    }
  }

  async function saveBibleAssignments() {
    const scene = selectedScene();
    if (!scene) return;
    const keys = [...document.querySelectorAll("#visualBibleCard [data-bible-key]:checked")].map(el => el.dataset.bibleKey);
    try {
      const result = await api(`/api/projects/${state.projectId}/scenes/${scene.scene_id}/visual-bible`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entity_keys: keys }),
      });
      toast(`Visual Bible: zapisano ${result.entity_keys.length} encji`);
      await refreshVisualBible();
    } catch (err) { alert(`Visual Bible: ${err.message}`); }
  }

  async function createBibleEntity() {
    const payload = {
      entity_key: document.getElementById("bibleKey").value.trim(),
      kind: document.getElementById("bibleKind").value,
      name: document.getElementById("bibleName").value.trim(),
      prompt_fragment: document.getElementById("biblePrompt").value.trim(),
      reference_asset_path: document.getElementById("bibleRef").value.trim() || null,
      active: true,
    };
    try {
      await api(`/api/projects/${state.projectId}/visual-bible/entities`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      toast(`Visual Bible: ${payload.entity_key}`);
      await refreshVisualBible();
    } catch (err) { alert(`Visual Bible: ${err.message}`); }
  }

  function inject() {
    injectWizardButton();
    injectProductionRun();
    injectVisualBible();
  }

  function scheduleInject() {
    if (injectScheduled) return;
    injectScheduled = true;
    queueMicrotask(() => {
      injectScheduled = false;
      inject();
    });
  }

  const observer = new MutationObserver(scheduleInject);
  observer.observe(document.body, { childList: true, subtree: true });
  inject();

  setInterval(() => {
    if (state.view === "dashboard") refreshProductionRun().catch(() => {});
  }, 5000);
})();
