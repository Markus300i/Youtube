(() => {
  const style = document.createElement("style");
  style.textContent = `
    .manual-action-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin-top:14px; }
    .manual-action-btn { text-align:left; min-height:92px; display:grid; grid-template-columns:1fr auto; gap:5px 8px; align-items:start; }
    .manual-action-btn strong { color:var(--text); }
    .manual-action-btn .action-detail { color:var(--muted); font-size:11px; grid-column:1 / -1; }
    .manual-action-btn .action-activity { color:#a9c7ff; font-size:11px; grid-column:1 / -1; }
    .manual-action-btn .action-blocker { color:var(--warn); font-size:11px; grid-column:1 / -1; }
    .manual-action-btn:disabled { cursor:not-allowed; opacity:.58; }
    .action-state { font-size:9px; letter-spacing:.08em; padding:3px 6px; border-radius:999px; border:1px solid var(--line); color:var(--muted); }
    .action-state.done { color:var(--good); border-color:#2f6b4e; }
    .action-state.ready { color:#a9c7ff; border-color:#40567c; }
    .action-state.blocked { color:var(--warn); border-color:#6e5a2b; }
    .action-state.stale { color:var(--warn); border-color:#6e5a2b; }
    .action-state.failed { color:var(--bad); border-color:#744545; }
    .action-state.running, .action-state.queued { color:var(--warn); border-color:#6e5a2b; }
    #quickRegenBtn { border-color:#40567c; color:#a9c7ff; }
    .bulk-image-box { display:flex; justify-content:space-between; gap:14px; align-items:center; padding:12px; margin-top:12px; border:1px solid var(--line); border-radius:10px; }
    .bulk-image-box strong { display:block; margin-bottom:3px; }
    .bulk-image-box.complete { border-color:#2f6b4e; }
    #bulkQuickDraftBtn { border-color:#40567c; color:#a9c7ff; white-space:nowrap; }
    @media (max-width:780px) { .manual-action-grid { grid-template-columns:1fr 1fr; } }
    @media (max-width:620px) { .bulk-image-box { align-items:stretch; flex-direction:column; } }
    @media (max-width:520px) { .manual-action-grid { grid-template-columns:1fr; } }
  `;
  document.head.appendChild(style);

  let pollBusy = false;
  let injectScheduled = false;
  let actionsFetchBusy = false;
  let bulkImagesBusy = false;

  async function quickRegenerate() {
    const scene = selectedScene();
    if (!scene) return;
    try {
      toast(`Scena ${scene.scene_id}: uruchamiam Quick Regenerate…`);
      const result = await api(
        `/api/projects/${state.projectId}/scenes/${scene.scene_id}/quick-regenerate`,
        { method: "POST" },
      );
      if (result.scheduled === false) {
        toast(`Quick Regenerate już działa: ${result.task.task_id}`);
      } else {
        toast(`Quick Regenerate dodany: ${result.task.task_id}`);
      }
      await loadProjectData();
      switchView("tasks");
    } catch (err) {
      alert(`Quick Regenerate nie powiódł się: ${err.message}`);
    }
  }

  function missingImageScenes() {
    return (state.scenes || []).filter(scene => !scene.active_asset);
  }

  async function bulkQuickDraft() {
    if (bulkImagesBusy || !state.projectId) return;
    const missing = missingImageScenes();
    if (!missing.length) {
      toast("Wszystkie sceny mają już aktywny obraz");
      return;
    }

    bulkImagesBusy = true;
    const button = document.getElementById("bulkQuickDraftBtn");
    if (button) {
      button.disabled = true;
      button.textContent = "Planuję…";
    }
    let scheduled = 0;
    let alreadyActive = 0;
    const failures = [];

    try {
      for (const scene of missing) {
        try {
          const result = await api(
            `/api/projects/${state.projectId}/scenes/${scene.scene_id}/quick-regenerate`,
            { method: "POST" },
          );
          if (result.scheduled === false) alreadyActive += 1;
          else scheduled += 1;
        } catch (err) {
          failures.push(`scena ${scene.scene_id}: ${err.message}`);
        }
      }
      toast(`Quick Draft: ${scheduled} nowych tasków${alreadyActive ? ` · ${alreadyActive} już aktywnych` : ""}`);
      await loadProjectData();
      switchView("tasks");
      if (failures.length) {
        alert(`Nie udało się zaplanować części scen:\n${failures.join("\n")}`);
      }
    } finally {
      bulkImagesBusy = false;
      const current = document.getElementById("bulkQuickDraftBtn");
      if (current) {
        current.disabled = false;
        current.textContent = "Quick Draft brakujących scen";
      }
    }
  }

  async function runManualAction(action, label) {
    try {
      toast(`${label}: dodaję task…`);
      const result = await api(`/api/projects/${state.projectId}/actions/${action}`, { method: "POST" });
      if (result.scheduled === false) {
        toast(`${label}: task już jest aktywny`);
      } else {
        toast(`${label}: ${result.task.task_id}`);
      }
      await loadProjectData();
      switchView("tasks");
    } catch (err) {
      alert(`${label}: ${err.message}`);
      refreshManualActions().catch(() => {});
    }
  }

  function injectSceneActions() {
    const quality = document.getElementById("regenBtn");
    if (!quality) return;

    if (quality.textContent !== "Quality Regenerate") {
      quality.textContent = "Quality Regenerate";
    }
    if (document.getElementById("quickRegenBtn")) return;

    const quick = document.createElement("button");
    quick.id = "quickRegenBtn";
    quick.className = "ghost quick-regenerate";
    quick.textContent = "Quick Regenerate";
    quick.title = "Szybki draft przez Z-Image Turbo; pomija FLUX edit/crop.";
    quick.addEventListener("click", quickRegenerate);
    quality.parentElement?.insertBefore(quick, quality);
  }

  function actionButtonHtml(item) {
    const blocked = (item.missing_labels || []).join(", ");
    const disabled = !item.can_run;
    const progress = item.active_progress;
    const stateLabel = item.state === "running" && progress !== null
      ? `RUNNING ${progress}%`
      : String(item.state || "ready").toUpperCase();
    const activity = item.current_step
      ? `Bieżący krok: ${item.current_step}`
      : (item.waiting_reason || "");
    const freshness = item.freshness_reason && item.state === "stale"
      ? `Nieaktualne: ${item.freshness_reason}`
      : "";
    return `
      <button class="ghost manual-action-btn" data-manual-action="${item.action}" data-label="${item.label}" ${disabled ? "disabled" : ""}>
        <strong>${item.label}</strong>
        <span class="action-state ${item.state}">${stateLabel}</span>
        <span class="action-detail">${item.detail} · ${item.resource}</span>
        ${activity ? `<span class="action-activity">${escapeHtml(activity)}</span>` : ""}
        ${freshness ? `<span class="action-blocker">${escapeHtml(freshness)}</span>` : ""}
        ${blocked ? `<span class="action-blocker">Wymaga: ${blocked}</span>` : ""}
      </button>
    `;
  }

  function bindManualButtons(card) {
    card.querySelectorAll("[data-manual-action]").forEach(button => {
      button.addEventListener("click", () => runManualAction(button.dataset.manualAction, button.dataset.label));
    });
  }

  function renderBulkImageGate(card) {
    const target = card.querySelector("#bulkImageGate");
    if (!target) return;
    const missing = missingImageScenes();
    if (!missing.length) {
      target.innerHTML = `
        <div class="bulk-image-box complete">
          <div><strong>Obrazy 8/8</strong><span class="muted">Wszystkie sceny mają aktywny obraz. Możesz przejść do review / Visual QA.</span></div>
        </div>
      `;
      return;
    }
    const ids = missing.map(scene => scene.scene_id).join(", ");
    target.innerHTML = `
      <div class="bulk-image-box">
        <div>
          <strong>Brakuje obrazów: ${missing.length}/8</strong>
          <span class="muted">Sceny ${escapeHtml(ids)}. Quick Draft użyje Z-Image Turbo i istniejącej kolejki GPU; nie zatwierdzi scen automatycznie.</span>
        </div>
        <button id="bulkQuickDraftBtn" class="ghost" ${bulkImagesBusy ? "disabled" : ""}>${bulkImagesBusy ? "Planuję…" : "Quick Draft brakujących scen"}</button>
      </div>
    `;
    target.querySelector("#bulkQuickDraftBtn")?.addEventListener("click", bulkQuickDraft);
  }

  async function refreshManualActions() {
    const card = document.getElementById("manualActionsCard");
    if (!card || !state.projectId || actionsFetchBusy) return;
    actionsFetchBusy = true;
    try {
      const data = await api(`/api/projects/${state.projectId}/actions`);
      const grid = card.querySelector(".manual-action-grid");
      if (!grid) return;
      grid.innerHTML = data.actions.map(actionButtonHtml).join("");
      bindManualButtons(card);
      renderBulkImageGate(card);
      card.dataset.projectId = state.projectId;
    } catch (err) {
      const grid = card.querySelector(".manual-action-grid");
      if (grid) grid.innerHTML = `<div class="muted">Nie udało się pobrać statusów akcji: ${escapeHtml(err.message)}</div>`;
      renderBulkImageGate(card);
    } finally {
      actionsFetchBusy = false;
    }
  }

  function injectManualActions() {
    const grid = document.querySelector("#opsDashboard .dashboard-grid");
    if (!grid || document.getElementById("manualActionsCard")) return;

    const card = document.createElement("section");
    card.id = "manualActionsCard";
    card.className = "ops-card full-span manual-actions-card";
    card.innerHTML = `
      <div class="eyebrow">MANUAL ACTIONS</div>
      <h3>Uruchom etap niezależnie od Agent One</h3>
      <p class="muted">Ręczne akcje nie zmieniają next_action, ale respektują wymagane artefakty i review.</p>
      <div id="bulkImageGate"></div>
      <div class="manual-action-grid"><div class="muted">Ładowanie statusów…</div></div>
    `;
    grid.appendChild(card);
    renderBulkImageGate(card);
    refreshManualActions().catch(() => {});
  }

  function inject() {
    injectSceneActions();
    injectManualActions();
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

  setInterval(async () => {
    if (pollBusy || !["tasks", "dashboard"].includes(state.view) || !state.projectId) return;
    const active = (state.ops?.tasks || []).some(task => task.state === "queued" || task.state === "running");
    if (!active) {
      if (state.view === "dashboard") refreshManualActions().catch(() => {});
      return;
    }
    pollBusy = true;
    try {
      await loadProjectData();
    } catch (_) {
      // Normal refresh button remains available if polling temporarily fails.
    } finally {
      pollBusy = false;
    }
  }, 3000);
})();