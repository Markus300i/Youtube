(() => {
  const ACTIONS = [
    ["tts", "TTS", "Chatterbox narration"],
    ["captions", "Captions", "Whisper subtitles"],
    ["sound_design", "Sound", "Final audio mix"],
    ["visual_qa", "Visual QA", "NVIDIA visual review"],
    ["opencut_export", "OpenCut", "Export interchange"],
    ["render_final", "Render Final", "FFmpeg final MP4"],
  ];

  const style = document.createElement("style");
  style.textContent = `
    .manual-action-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin-top:14px; }
    .manual-action-btn { text-align:left; min-height:72px; display:grid; gap:5px; }
    .manual-action-btn strong { color:var(--text); }
    .manual-action-btn span { color:var(--muted); font-size:11px; }
    #quickRegenBtn { border-color:#40567c; color:#a9c7ff; }
    @media (max-width:780px) { .manual-action-grid { grid-template-columns:1fr 1fr; } }
    @media (max-width:520px) { .manual-action-grid { grid-template-columns:1fr; } }
  `;
  document.head.appendChild(style);

  let pollBusy = false;

  async function quickRegenerate() {
    const scene = selectedScene();
    if (!scene) return;
    try {
      toast(`Scena ${scene.scene_id}: uruchamiam Quick Regenerate…`);
      const result = await api(
        `/api/projects/${state.projectId}/scenes/${scene.scene_id}/quick-regenerate`,
        { method: "POST" },
      );
      toast(`Quick Regenerate dodany: ${result.task.task_id}`);
      await loadProjectData();
      switchView("tasks");
    } catch (err) {
      alert(`Quick Regenerate nie powiódł się: ${err.message}`);
    }
  }

  async function runManualAction(action, label) {
    try {
      toast(`${label}: dodaję task…`);
      const result = await api(`/api/projects/${state.projectId}/actions/${action}`, { method: "POST" });
      toast(`${label}: ${result.task.task_id}`);
      await loadProjectData();
      switchView("tasks");
    } catch (err) {
      alert(`${label}: ${err.message}`);
    }
  }

  function injectSceneActions() {
    const quality = document.getElementById("regenBtn");
    if (!quality) return;
    quality.textContent = "Quality Regenerate";
    if (document.getElementById("quickRegenBtn")) return;

    const quick = document.createElement("button");
    quick.id = "quickRegenBtn";
    quick.className = "ghost quick-regenerate";
    quick.textContent = "Quick Regenerate";
    quick.title = "Szybki draft przez Z-Image Turbo; pomija FLUX edit/crop.";
    quick.addEventListener("click", quickRegenerate);
    quality.parentElement?.insertBefore(quick, quality);
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
      <p class="muted">Akcje tworzą normalne taski i nie zmieniają deterministycznego next_action.</p>
      <div class="manual-action-grid">
        ${ACTIONS.map(([action, label, detail]) => `
          <button class="ghost manual-action-btn" data-manual-action="${action}" data-label="${label}">
            <strong>${label}</strong><span>${detail}</span>
          </button>
        `).join("")}
      </div>
    `;
    grid.appendChild(card);
    card.querySelectorAll("[data-manual-action]").forEach(button => {
      button.addEventListener("click", () => runManualAction(button.dataset.manualAction, button.dataset.label));
    });
  }

  function inject() {
    injectSceneActions();
    injectManualActions();
  }

  const observer = new MutationObserver(() => inject());
  observer.observe(document.body, { childList: true, subtree: true });
  inject();

  setInterval(async () => {
    if (pollBusy || state.view !== "tasks" || !state.projectId) return;
    const active = (state.ops?.tasks || []).some(task => task.state === "queued" || task.state === "running");
    if (!active) return;
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
