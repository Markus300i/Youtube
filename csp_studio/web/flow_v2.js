(() => {
  const style = document.createElement("style");
  style.textContent = `
    .worker-line { display:flex; justify-content:space-between; gap:12px; align-items:center; }
    .worker-pill { display:inline-flex; padding:4px 8px; border:1px solid var(--line); border-radius:999px; font-size:10px; letter-spacing:.08em; text-transform:uppercase; }
    .worker-pill.online { color:var(--good); border-color:#2f6b4e; }
    .worker-pill.offline { color:var(--bad); border-color:#744545; }
    .wizard-ai-box { border:1px solid var(--line); border-radius:10px; padding:12px; display:grid; gap:9px; }
    .wizard-ai-box textarea { min-height:90px; }
    .wizard-ai-meta { font-size:11px; color:var(--muted); white-space:pre-wrap; }
  `;
  document.head.appendChild(style);

  let aiEnvelope = null;
  let workerBusy = false;
  let injectScheduled = false;

  async function refreshWorkerCard() {
    const card = document.getElementById("workerStatusCard");
    if (!card || workerBusy) return;
    workerBusy = true;
    try {
      const data = await api("/api/workers");
      const online = (data.workers || []).filter(item => item.online);
      const primary = online[0] || (data.workers || [])[0];
      card.innerHTML = `
        <div class="eyebrow">STUDIO WORKER</div>
        <div class="worker-line">
          <div>
            <h3>${primary ? escapeHtml(primary.worker_id) : "Brak zarejestrowanego workera"}</h3>
            <div class="muted">${primary ? `${escapeHtml(primary.state)}${primary.current_task_id ? ` · ${escapeHtml(primary.current_task_id)}` : ""}` : "Execution plane nie wysłał jeszcze heartbeat."}</div>
          </div>
          <span class="worker-pill ${online.length ? "online" : "offline"}">${online.length ? "ONLINE" : "OFFLINE"}</span>
        </div>
        ${primary ? `<div class="muted" style="margin-top:8px">Host: ${escapeHtml(primary.hostname)} · PID ${primary.pid} · heartbeat ${escapeHtml(primary.heartbeat_at)}</div>` : ""}
      `;
    } catch (err) {
      card.innerHTML = `<div class="eyebrow">STUDIO WORKER</div><div class="muted">${escapeHtml(err.message)}</div>`;
    } finally {
      workerBusy = false;
    }
  }

  function injectWorkerCard() {
    const grid = document.querySelector("#opsDashboard .dashboard-grid");
    if (!grid || document.getElementById("workerStatusCard")) return;
    const card = document.createElement("section");
    card.id = "workerStatusCard";
    card.className = "ops-card full-span";
    card.innerHTML = `<div class="eyebrow">STUDIO WORKER</div><div class="muted">Ładowanie heartbeat…</div>`;
    grid.prepend(card);
    refreshWorkerCard().catch(() => {});
  }

  function currentWizardDraft() {
    return {
      id: document.getElementById("wizardId").value.trim(),
      title: document.getElementById("wizardTitle").value.trim(),
      series: "Ciemna Strona Polski",
      fictional: true,
      status: "draft",
      narration: document.getElementById("wizardNarration").value.trim(),
      visual_style: document.getElementById("wizardStyle").value.trim(),
      scenes: Array.from({ length: 8 }, (_, i) => ({
        id: i + 1,
        text: document.querySelector(`[data-wizard-text="${i + 1}"]`).value.trim(),
        prompt: document.querySelector(`[data-wizard-prompt="${i + 1}"]`).value.trim(),
        motion: "static",
        continuity_refs: aiEnvelope?.draft?.scenes?.[i]?.continuity_refs || [],
        render: { mode: "generate" },
      })),
    };
  }

  function populateWizard(envelope) {
    const draft = envelope.draft;
    document.getElementById("wizardId").value = draft.id || "";
    document.getElementById("wizardTitle").value = draft.title || "";
    document.getElementById("wizardNarration").value = draft.narration || "";
    document.getElementById("wizardStyle").value = draft.visual_style || "";
    (draft.scenes || []).forEach(scene => {
      const text = document.querySelector(`[data-wizard-text="${scene.id}"]`);
      const prompt = document.querySelector(`[data-wizard-prompt="${scene.id}"]`);
      if (text) text.value = scene.text || "";
      if (prompt) prompt.value = scene.prompt || "";
    });
    const meta = document.getElementById("wizardAiMeta");
    if (meta) {
      const entities = envelope.visual_bible?.entities?.length || 0;
      meta.textContent = `Shot QA ${envelope.shot_audit?.score ?? "—"}/100 · Visual Bible ${entities} encji · ${envelope.provider?.name || "provider"}/${envelope.provider?.model || "model"}`;
    }
    document.getElementById("createAiShortBtn")?.removeAttribute("disabled");
  }

  async function generateAiDraft() {
    const topic = document.getElementById("wizardAiTopic").value.trim();
    if (!topic) {
      alert("Wizard V2: wpisz pomysł na Shorta.");
      return;
    }
    const button = document.getElementById("generateAiDraftBtn");
    button.disabled = true;
    const oldText = button.textContent;
    button.textContent = "Generuję…";
    try {
      aiEnvelope = await api("/api/wizard/v2/draft", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          topic,
          project_id: document.getElementById("wizardId").value.trim() || null,
          title: document.getElementById("wizardTitle").value.trim() || null,
        }),
      });
      populateWizard(aiEnvelope);
      toast("Wizard V2: draft AI gotowy do review");
    } catch (err) {
      alert(`Wizard V2 draft: ${err.message}`);
    } finally {
      button.disabled = false;
      button.textContent = oldText;
    }
  }

  async function createAiShort() {
    if (!aiEnvelope) return;
    const reviewed = {
      ...aiEnvelope,
      draft: currentWizardDraft(),
    };
    const button = document.getElementById("createAiShortBtn");
    button.disabled = true;
    try {
      const result = await api("/api/wizard/v2/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(reviewed),
      });
      document.getElementById("newShortDialog").close();
      toast(`Wizard V2: utworzono ${result.project.project_id}`);
      state.projectId = result.project.project_id;
      await loadProjects();
      projectSelect.value = state.projectId;
      await loadProjectData({ keepSelection: false });
      aiEnvelope = null;
    } catch (err) {
      alert(`Wizard V2 create: ${err.message}`);
    } finally {
      button.disabled = false;
    }
  }

  function enhanceWizard() {
    const body = document.querySelector("#newShortDialog .wizard-body");
    if (!body || document.getElementById("wizardAiBox")) return;
    const box = document.createElement("section");
    box.id = "wizardAiBox";
    box.className = "wizard-ai-box";
    box.innerHTML = `
      <div><div class="eyebrow">WIZARD V2 · AI DRAFT</div><strong>Pomysł → scenariusz → 8 scen → Visual Bible</strong></div>
      <textarea id="wizardAiTopic" placeholder="Np. nocny strażnik na małej stacji zauważa peron, którego nie ma w żadnym rozkładzie…"></textarea>
      <div class="flow-actions">
        <button id="generateAiDraftBtn" class="ghost">Generuj draft AI</button>
        <button id="createAiShortBtn" class="primary" disabled>Utwórz z Visual Bible</button>
      </div>
      <div id="wizardAiMeta" class="wizard-ai-meta">Draft AI nie zapisuje niczego do SQLite. Najpierw zostanie wypełniony formularz do review.</div>
    `;
    body.prepend(box);
    box.querySelector("#generateAiDraftBtn").addEventListener("click", generateAiDraft);
    box.querySelector("#createAiShortBtn").addEventListener("click", createAiShort);
  }

  function inject() {
    injectWorkerCard();
    enhanceWizard();
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
    if (state.view === "dashboard") refreshWorkerCard().catch(() => {});
  }, 5000);
})();
