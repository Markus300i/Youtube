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
    .wizard-ai-state { border:1px solid var(--line); border-radius:8px; padding:9px 10px; font-size:12px; line-height:1.45; }
    .wizard-ai-state.idle { color:var(--muted); }
    .wizard-ai-state.busy { color:var(--warn); border-color:#6e5a2b; }
    .wizard-ai-state.ready { color:var(--good); border-color:#2f6b4e; }
    .wizard-ai-state.warn { color:var(--warn); border-color:#6e5a2b; }
    .wizard-ai-state.error { color:var(--bad); border-color:#744545; }
    .nim-ready { color:var(--good); }
    .nim-missing { color:var(--bad); }
  `;
  document.head.appendChild(style);

  let aiEnvelope = null;
  let workerBusy = false;
  let injectScheduled = false;

  function setWizardAiState(kind, message) {
    const target = document.getElementById("wizardAiState");
    if (!target) return;
    target.className = `wizard-ai-state ${kind}`;
    target.textContent = message;
  }

  function clearAiEnvelope(message = null) {
    aiEnvelope = null;
    document.getElementById("createAiShortBtn")?.setAttribute("disabled", "disabled");
    if (message) setWizardAiState("warn", message);
  }

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

  async function refreshNimStatus() {
    const target = document.getElementById("wizardNimStatus");
    if (!target) return null;
    try {
      const status = await api("/api/providers/nvidia-nim/status");
      if (status.configured) {
        target.className = "wizard-ai-meta nim-ready";
        target.textContent = `NVIDIA NIM gotowy · ${status.chat_model} · źródło klucza: ${status.api_key_source}`;
      } else {
        target.className = "wizard-ai-meta nim-missing";
        target.textContent = "NVIDIA NIM: brak klucza. Uruchom setup\\configure-nim.ps1; klucz nie jest zapisywany w SQLite ani logach.";
      }
      return status;
    } catch (err) {
      target.className = "wizard-ai-meta nim-missing";
      target.textContent = `NVIDIA NIM status: ${err.message}`;
      return null;
    }
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
      const storyRepairs = envelope.provider?.story_repairs ?? 0;
      const visualRepairs = envelope.provider?.visual_repairs ?? 0;
      meta.textContent = `Shot QA ${envelope.shot_audit?.score ?? "—"}/100 · Visual Bible ${entities} encji · repairs story/visual ${storyRepairs}/${visualRepairs} · ${envelope.provider?.name || "provider"}/${envelope.provider?.model || "model"}`;
    }
    setWizardAiState("ready", "Draft AI przeszedł deterministic gates. Sprawdź narrację, 8 scen i prompty; projekt zostanie zapisany dopiero po kliknięciu „Utwórz po review”.");
    document.getElementById("createAiShortBtn")?.removeAttribute("disabled");
  }

  async function requestAiDraft(payload) {
    const response = await fetch("/api/wizard/v2/draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const data = await response.json();
        detail = data.detail || detail;
      } catch (_) {}
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  async function generateAiDraft() {
    const topic = document.getElementById("wizardAiTopic").value.trim();
    if (!topic) {
      setWizardAiState("warn", "Wpisz pomysł na Shorta albo skorzystaj z ręcznego formularza poniżej.");
      return;
    }
    const nimStatus = await refreshNimStatus();
    if (!nimStatus?.configured) {
      setWizardAiState("error", "NVIDIA NIM nie jest skonfigurowany. Możesz skonfigurować provider albo od razu utworzyć projekt ręcznie poniżej.");
      return;
    }

    clearAiEnvelope();
    const button = document.getElementById("generateAiDraftBtn");
    button.disabled = true;
    const oldText = button.textContent;
    button.textContent = "Generuję…";
    setWizardAiState("busy", "Generuję draft. Nic nie jest jeszcze zapisywane do SQLite ani YAML.");
    try {
      aiEnvelope = await requestAiDraft({
        topic,
        project_id: document.getElementById("wizardId").value.trim() || null,
        title: document.getElementById("wizardTitle").value.trim() || null,
      });
      populateWizard(aiEnvelope);
      button.textContent = "Generuj ponownie";
      toast("Wizard V2: draft AI gotowy do review");
    } catch (err) {
      clearAiEnvelope();
      if (err.status === 422) {
        setWizardAiState("warn", `Model nie przeszedł deterministic gate: ${err.message}. Nic nie zapisano. Spróbuj ponownie albo dokończ formularz ręcznie.`);
        toast("Wizard V2: draft odrzucony przez gate — możesz ponowić");
      } else {
        setWizardAiState("error", `Nie udało się pobrać draftu AI: ${err.message}. Ręczny Wizard pozostaje dostępny poniżej.`);
      }
      button.textContent = "Spróbuj ponownie";
    } finally {
      button.disabled = false;
      if (button.textContent === "Generuję…") button.textContent = oldText;
    }
  }

  async function createAiShort() {
    if (!aiEnvelope) {
      setWizardAiState("warn", "Brak aktualnego, zaakceptowanego envelope AI. Wygeneruj draft ponownie albo użyj ręcznego przycisku „Utwórz projekt”.");
      return;
    }
    const reviewed = {
      ...aiEnvelope,
      draft: currentWizardDraft(),
    };
    const button = document.getElementById("createAiShortBtn");
    button.disabled = true;
    setWizardAiState("busy", "Waliduję reviewed draft i zapisuję projekt. Production Run nie zostanie uruchomiony automatycznie.");
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
      clearAiEnvelope();
    } catch (err) {
      setWizardAiState("error", `Reviewed create został odrzucony: ${err.message}. Projekt nie powinien być uruchamiany dalej, dopóki błąd nie zostanie poprawiony.`);
      button.disabled = false;
    }
  }

  function useManualWizard() {
    clearAiEnvelope();
    setWizardAiState("idle", "Tryb ręczny: uzupełnij pola i 8 scen poniżej, a następnie użyj przycisku „Utwórz projekt”. AI i Visual Bible nie są wymagane do utworzenia ręcznego draftu.");
    const target = document.querySelector("#newShortDialog .wizard-grid");
    target?.scrollIntoView({ behavior: "smooth", block: "start" });
    document.getElementById("wizardId")?.focus();
  }

  function enhanceWizard() {
    const body = document.querySelector("#newShortDialog .wizard-body");
    if (!body || document.getElementById("wizardAiBox")) return;
    const box = document.createElement("section");
    box.id = "wizardAiBox";
    box.className = "wizard-ai-box";
    box.innerHTML = `
      <div><div class="eyebrow">WIZARD V2 · AI DRAFT</div><strong>Pomysł → scenariusz → 8 scen → Visual Bible</strong></div>
      <div id="wizardNimStatus" class="wizard-ai-meta">Sprawdzam NVIDIA NIM…</div>
      <textarea id="wizardAiTopic" placeholder="Np. nocny strażnik na małej stacji zauważa peron, którego nie ma w żadnym rozkładzie…"></textarea>
      <div class="flow-actions">
        <button id="generateAiDraftBtn" class="ghost">Generuj draft AI</button>
        <button id="createAiShortBtn" class="primary" disabled>Utwórz po review</button>
        <button id="manualWizardBtn" class="ghost">Przejdź do ręcznego formularza</button>
      </div>
      <div id="wizardAiState" class="wizard-ai-state idle">AI jest opcjonalne. Nieudany draft nie zapisuje projektu i nie blokuje ręcznego workflow.</div>
      <div id="wizardAiMeta" class="wizard-ai-meta">Draft AI nie zapisuje niczego do SQLite. Najpierw zostanie wypełniony formularz do review.</div>
    `;
    body.prepend(box);
    box.querySelector("#generateAiDraftBtn").addEventListener("click", generateAiDraft);
    box.querySelector("#createAiShortBtn").addEventListener("click", createAiShort);
    box.querySelector("#manualWizardBtn").addEventListener("click", useManualWizard);
    box.querySelector("#wizardAiTopic").addEventListener("input", () => {
      if (aiEnvelope) {
        clearAiEnvelope("Pomysł został zmieniony po wygenerowaniu draftu. Wygeneruj AI ponownie albo przejdź do ręcznego formularza; poprzedni envelope został unieważniony.");
      }
    });
    refreshNimStatus().catch(() => {});
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
