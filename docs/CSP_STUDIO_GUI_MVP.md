# CSP Studio GUI MVP

Pierwszy lokalny interfejs CSP Studio nad istniejącym `csp-studio.db` i CSP Automation V1.

## Funkcje

- lista projektów zapisanych w SQLite,
- siatka scen z aktywnymi obrazami i statusami,
- podgląd jednej sceny,
- informacje Shot Directora,
- Import / Replace Image z pliku pobranego np. z GPT Image w przeglądarce,
- automatyczne wersjonowanie obrazów w `images/revisions/`,
- zachowanie kompatybilnego `images/scene-XX.png` dla obecnego renderera,
- Approve,
- Regenerate,
- historia assetów i rewizji sceny.

## Instalacja zależności webowych

```powershell
$py = "C:\CSP\venv\Scripts\python.exe"
& $py -m pip install fastapi==0.116.1 uvicorn==0.35.0 python-multipart==0.0.20 httpx==0.28.1
```

## Uruchomienie

```powershell
cd C:\Users\pat30\Youtube
$py = "C:\CSP\venv\Scripts\python.exe"
$env:CSP_OUTPUT_DIR = "C:\CSP\output"
& $py -m csp_studio.web_app
```

Następnie otwórz:

`http://127.0.0.1:8765`

## Test

```powershell
& $py -m unittest tests.test_csp_studio_web -v
```

## Architektura

GUI komunikuje się z lokalnym FastAPI. API korzysta z tych samych `StudioStore`, `AssetManager` i `SceneOperations`, co CLI. Frontend jest obecnie lekkim SPA bez procesu build, aby szybko zweryfikować workflow Scene Editora. Docelowy timeline może zostać przeniesiony do React/Vite bez zmiany kontraktu API i logiki domenowej.
