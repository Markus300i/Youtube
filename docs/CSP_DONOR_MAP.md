# CSP Studio — mapa repozytoriów donorów

Ten dokument zamraża decyzje z audytów zewnętrznych projektów. Jego celem jest uniknięcie ponownego budowania modułów, które już istnieją w dobrych projektach open source, przy jednoczesnym zachowaniu własnej domeny CSP.

## Zasada główna

Nie forkować jednego dużego projektu jako całego CSP. CSP Studio pozostaje własną warstwą domenową i UX. Projekty zewnętrzne są donorami konkretnych mechanizmów.

Przed skopiowaniem kodu z repo zewnętrznego należy ponownie sprawdzić licencję konkretnego pliku/wersji i zachować wymagane informacje o źródle.

## Docelowy podział odpowiedzialności

```text
CSP STUDIO
├── Story / Master Prompt / Universe Memory            [OWN]
├── Scene Model / Shot Director / Visual QA            [OWN]
├── Asset Manager / revisions / approvals              [OWN]
├── Agent One / production orchestration               [ADAPT]
│   └── donor: darkzOGx/youtube-automation-agent
├── Reliable Task Engine                               [ADAPT]
│   └── donor: harry0703/MoneyPrinterTurbo
├── Timeline / manual editing                          [ADAPT / INTEGRATE]
│   └── donor: OpenCut-app/OpenCut + opencut-classic
├── CSP Automation production pipeline                 [OWN]
│   ├── Chatterbox
│   ├── Whisper
│   ├── sound design
│   └── FFmpeg final render
└── Publishing / analytics                             [ADAPT]
    ├── darkzOGx/youtube-automation-agent
    └── mzu-2410z/yt-automation
```

## Skrócona macierz decyzji

| Repozytorium | Decyzja | Główna rola w CSP | Priorytet |
|---|---|---|---|
| `harry0703/MoneyPrinterTurbo` | ADAPT, nie fork | task engine / niezawodność backendu | P1 |
| `OpenCut-app/OpenCut` | INTEGRATE / obserwować API | docelowy editor / timeline | P1 |
| `OpenCut-app/opencut-classic` | ADAPT wzorce, nie fork | sprawdzone mechanizmy timeline | P1 |
| `darkzOGx/youtube-automation-agent` | ADAPT, nie fork | Agent One / orkiestracja / publishing / analytics | P2 |
| `numbpill3d/ffmpeg-ai` (`codemonkei/ffmpeg-ai` był audytowanym forkiem) | TAKE patterns | prosty deterministyczny pipeline / fallback ideas | P3 |
| `aibr442/multi-ai-video-factory` | TAKE concepts | storyboard / provider ideas | P3 |
| `mzu-2410z/yt-automation` | ADAPT selected | YouTube upload / publishing workflow | P2/P3 |
| `RayVentura/ShortGPT` | REJECT as foundation | history/reference only | NONE |

---

# 1. MoneyPrinterTurbo

Repo: `harry0703/MoneyPrinterTurbo`

## Decyzja

**ADAPT — nie forkować i nie zastępować CSP Automation.**

CSP ma już lepiej dopasowany pipeline produkcyjny: sceny CSP, własne continuity, local/manual GPT Image, sekwencjonowanie pod 8 GB VRAM, Chatterbox PL, Whisper, sound design i finalny FFmpeg.

## TAKE / ADAPT

### Task Manager / queue

Przenieść wzorzec kolejki z zasadą jednego ciężkiego zadania GPU naraz.

Docelowy model zadania CSP:

```text
task_id
project_id
scene_id? / stage
state
progress
started_at
finished_at
failed_stage
error
retry_count
```

Zastosowanie:
- generowanie obrazów,
- TTS,
- transkrypcja,
- render preview,
- render final,
- publikacja.

### Checkpoint / resume / recovery

Adaptować możliwość wznowienia od ostatniego poprawnego etapu zamiast uruchamiania całego Shorta ponownie.

Przykład:

```text
images        DONE
voice         DONE
captions      DONE
sound_design  FAILED
render        WAITING
```

Retry zaczyna od `sound_design`.

### FastAPI + Pydantic contracts

Adaptować wzorzec jawnych request/response schemas dla operacji Studio i przyszłego Agent One.

### Atomic file writes

Adaptować zasadę:

```text
write temp
flush
fsync
os.replace
```

Dla:
- JSON,
- manifestów,
- YAML exportów,
- metadanych zadań,
- checkpointów.

### FFmpeg reliability

Adaptować:
- wykrywanie NVENC,
- fallback do `libx264`,
- walidację wyniku przez `ffprobe`,
- czytelny `failed_stage` i log błędu.

### Test coverage

Brać wzorzec małych testów regresyjnych dla etapów pipeline’u.

## REJECT

Nie przenosić jako rdzeń CSP:
- stock-first Pexels/Pixabay/Coverr,
- generic script/scenario generator,
- dużego MPT TTS/voice layer zamiast Chatterbox,
- monolitycznego WebUI,
- pełnego modelu projektu MPT,
- logiki, która zastępowałaby Scene/Asset/Revision w CSP Studio.

---

# 2. OpenCut — current rewrite

Repo: `OpenCut-app/OpenCut`

## Decyzja

**INTEGRATE, nie forkować.**

OpenCut powinien przejąć funkcje klasycznego edytora wideo. CSP nie powinien budować własnego odpowiednika CapCut.

## Docelowo OpenCut odpowiada za

- timeline,
- tracks,
- clips,
- trim / split,
- ripple,
- snapping,
- pozycję klipów,
- duration,
- transitions,
- zoom / pan / keyframes,
- waveform,
- manualne rozmieszczenie audio,
- manualną korektę napisów,
- preview montażu.

## CSP nadal odpowiada za

- story,
- prompt,
- Shot Director,
- Visual Bible,
- continuity,
- asset revisions,
- Approve / Regenerate,
- Visual QA,
- Agent One,
- production readiness.

## Granica

```text
CSP: camera=pan_right, intensity=low
             ↓
OpenCut adapter
             ↓
konkretne keyframes / position / scale na timeline
```

CSP zapisuje **intencję reżyserską**. OpenCut realizuje ją technicznie na timeline.

## REJECT

- własny rozbudowany timeline CSP,
- własny system keyframe’ów,
- własny editor tracków,
- deep fork aktualnego OpenCut.

---

# 3. OpenCut Classic

Repo: `OpenCut-app/opencut-classic`

Repo jest archiwalne, dlatego jest traktowane jako **biblioteka wzorców**, nie zależność runtime.

## ADAPT patterns

- model `track / clip`,
- selection model,
- snapping,
- trim,
- split,
- ripple behavior,
- undo / redo,
- autosave,
- waveform UX,
- timeline keyboard interactions.

## REJECT

- fork całej aplikacji,
- zależność od Classic jako długoterminowej bazy CSP,
- render engine Classic zamiast istniejącego CSP Automation / przyszłej integracji OpenCut.

---

# 4. YouTube Automation Agent / Agent One donor

Repo: `darkzOGx/youtube-automation-agent`

## Decyzja

**ADAPT orchestration ideas — nie forkować monolitu.**

## TAKE / ADAPT

### Channel Operator

Agent poziomu kanału, który widzi nie tylko jedną scenę, ale cały backlog i stan produkcji.

### Idea backlog

Statusy np.:

```text
idea
approved
script_ready
assets_ready
review
render_ready
published
analytics_ready
```

### Checkpoints / production recovery

Agent ma wiedzieć, co już istnieje i nie wykonywać drugi raz kosztownego etapu.

### Readiness gates

Przykład:

```text
8/8 scenes approved
voice ready
captions ready
sound ready
visual QA passed
→ READY TO RENDER
```

### Provider-task persistence

Zapisywać, jaki provider wykonał operację i jaki był wynik.

### Publishing safety

Adaptować:
- przygotowanie uploadu,
- reconciliation po przerwaniu,
- sprawdzanie, czy film nie został już opublikowany,
- bezpieczne retry.

### Analytics loop

Plan docelowy:
- snapshot 24h,
- snapshot 7d,
- retention,
- CTR,
- watch time,
- zapis wyników do projektu/serii,
- uczenie rekomendacji na podstawie zaakceptowanych danych.

## REJECT

- generic slideshow/video generator,
- generic prompts jako scenarzysta CSP,
- captions wyliczane „na oko” zamiast Whisper timings,
- jego własny production core zamiast CSP Automation,
- pełny monolit aplikacji.

---

# 5. ffmpeg-ai

Audytowany fork: `codemonkei/ffmpeg-ai`.
Upstream: `numbpill3d/ffmpeg-ai`.

## Decyzja

**TAKE PATTERNS — nie integrować całego projektu.**

Projekt jest bliski technicznie CSP: deterministyczny CLI, sceny, narracja, FFmpeg i short-form video.

## TAKE

- prosty, jawny etapowy pipeline,
- deterministyczne wejście → render,
- separację scen / narracji / montażu,
- mały CLI jako wzorzec narzędzi diagnostycznych,
- FFmpeg jako niezawodny fallback eksportu.

## Ograniczenie po decyzji OpenCut

Nie kopiować Ken Burns / pan / zoom jako nowego głównego systemu ruchu CSP. Docelowy ruch montażowy powinien przejść do OpenCut.

## REJECT

- zastępowanie CSP rendererem tego repo,
- obcy model scenariusza,
- obce TTS jako replacement,
- pełny end-to-end workflow.

---

# 6. multi-ai-video-factory

Repo: `aibr442/multi-ai-video-factory`

## Decyzja

**TAKE CONCEPTS — nie fundament.**

## TAKE / ADAPT

- storyboard jako jawna struktura danych,
- podział promptów/providerów per etap,
- local-first provider abstraction,
- możliwość zamiany generatora obrazu bez przebudowy reszty pipeline’u.

Te idee są już częściowo obecne w CSP jako:
- `Scene`,
- `ShotPlan`,
- `Asset`,
- `Revision`,
- `render` metadata,
- provider/source assetu.

## REJECT

- Automatic1111 jako wymagany rdzeń,
- Edge TTS jako replacement dla narratora CSP,
- monolityczny factory script,
- kopiowanie jego modelu storyboardu 1:1 zamiast naszego modelu domenowego.

---

# 7. YT Automation

Repo: `mzu-2410z/yt-automation`

## Decyzja

**ADAPT selected publishing pieces.**

Projekt używa Groq/Llama, Kokoro TTS, FFmpeg i YouTube API. Dla CSP najważniejsza jest końcówka procesu, nie generowanie filmu.

## TAKE / ADAPT

- YouTube upload flow,
- auth / token handling patterns,
- metadata handoff,
- publikacja po zakończonym renderze,
- bezpieczny status `published`,
- ewentualne retry uploadu.

## REJECT

- Pexels / stock-footage-first workflow,
- Groq/Llama jako replacement Master Prompt CSP,
- Kokoro jako replacement Chatterbox,
- jego montaż FFmpeg jako replacement CSP/OpenCut.

---

# 8. ShortGPT

Repo: `RayVentura/ShortGPT`

## Decyzja

**REJECT AS FOUNDATION.**

Projekt pozostaje źródłem historycznych pomysłów na automatyzację Shorts, ale nie powinien być zależnością ani bazą CSP.

## Można zachować jako inspirację

- workflow-oriented thinking,
- modularne kroki tworzenia shorta,
- proste eksperymenty z automatyzacją.

## Nie używać jako rdzeń

- architektury jako fundamentu,
- jego integracji providerów jako standardu CSP,
- jego render pipeline,
- jego promptów,
- jego UI.

---

# Co jest OWN — nie szukamy już zamiennika

Następujące elementy są domeną CSP i mają pozostać własne:

1. Master Prompt CSP i zasady fikcyjnego uniwersum.
2. Project / Scene / Asset / Revision jako model domenowy.
3. Shot Director jako warstwa intencji reżyserskiej.
4. Visual Bible / continuity anchors.
5. Asset Manager i wersje `r1/r2/r3`.
6. Approve / Regenerate / review workflow.
7. Manual GPT Image/browser jako first-class provider.
8. Chatterbox Multilingual V3 narrator pipeline.
9. Whisper word-timed captions.
10. CSP sound design i twist timing.
11. Final production state w SQLite.

---

# Co przestajemy budować samodzielnie

Po audytach nie rozwijamy od zera:

- pełnego timeline’u,
- clip/track editor,
- keyframes,
- snapping/trim/ripple,
- rozbudowanej kolejki zadań bez wzorców MPT,
- upload managera YouTube bez wykorzystania donorów,
- analytics loop bez wzorców Agent One,
- kolejnego generycznego script generatora,
- kolejnego systemu TTS.

---

# Kolejność wdrożenia donorów

## P1 — przed dalszą rozbudową montażu

### A. OpenCut Adapter Spike

Cel: ustalić najprostszy kontrakt:

```text
CSP Project
  → scene assets + timings + ShotPlan
  → OpenCut project/timeline
```

Nie wymaga jeszcze pełnej dwukierunkowej synchronizacji.

### B. Task Engine z wzorców MoneyPrinterTurbo

Cel:
- kolejka,
- states,
- progress,
- failed_stage,
- retry,
- checkpoint/resume,
- atomic writes.

## P2 — po działającym adapterze / task engine

### C. Agent One

- readiness gates,
- operator projektu,
- production checkpoints,
- kolejka publikacji.

### D. YouTube Publisher

- upload,
- reconciliation,
- retry,
- published state.

## P3 — później

### E. Analytics loop

- 24h / 7d snapshots,
- retention,
- porównywanie historii,
- rekomendacje dla kolejnych scen/shortów.

---

# Reguła dla każdej nowej funkcji CSP

Przed implementacją nowego dużego modułu odpowiedz kolejno:

1. Czy jest to domena CSP? → buduj w CSP.
2. Czy jest to klasyczny problem edytora wideo? → najpierw sprawdź OpenCut.
3. Czy jest to reliability / task orchestration? → najpierw sprawdź MoneyPrinterTurbo.
4. Czy jest to channel automation / publishing / analytics? → najpierw sprawdź Agent One i YT Automation.
5. Czy donor daje tylko pomysł, ale jest mały/eksperymentalny? → adaptuj wzorzec, nie zależność.
6. Czy integracja wymagałaby wymiany działającego core CSP? → domyślnie odrzuć.

Ta mapa ma być aktualizowana po każdym kolejnym audycie repozytorium.