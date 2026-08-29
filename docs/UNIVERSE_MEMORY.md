# CSP Studio — Universe Memory

Universe Memory is the long-term semantic memory for the fictional CSP universe.

## Principle

```text
canonical memory = SQLite text + metadata
embedding        = disposable derived cache
vector index     = replaceable implementation detail
```

This prevents CSP lore from depending on one embedding model/provider.

## Tables

```text
universe_memory
universe_memory_embeddings
```

Canonical items contain:
- namespace,
- stable memory key,
- kind,
- text,
- optional source project,
- metadata,
- active flag,
- timestamps.

Embedding rows contain:
- memory item id,
- provider,
- model,
- content hash,
- vector,
- dimensions.

If canonical text changes, `content_hash` makes the old vector stale and `embed_pending()` rebuilds only that item.

## Initial memory kinds

The schema intentionally accepts open kinds such as:

```text
story
scene
location
character
object
visual_anchor
continuity_rule
lesson
series_rule
```

## Project ingestion

The first helper can register the current project summary plus its scenes:

```powershell
$py = "C:\CSP\venv\Scripts\python.exe"
$env:CSP_OUTPUT_DIR = "C:\CSP\output"
& $py -m csp_studio.universe_memory ingest-project 001
```

This is not yet an automatic canonization step. Review/curation rules can later decide which memories become durable universe canon.

## NVIDIA NIM embeddings

With the Provider Layer:

```powershell
$env:NVIDIA_API_KEY = "nvapi-..."
& $py -m csp_studio.universe_memory embed
```

Current default embedding model:

```text
nvidia/nv-embedqa-e5-v5
```

The provider receives canonical item text with `input_type=passage`. Search queries use `input_type=query`.

## Semantic search

```powershell
& $py -m csp_studio.universe_memory search "Czy mieliśmy już historię z podobnymi drzwiami?" --top-k 5
```

The first version computes cosine similarity in Python. This is deliberate: memory cardinality is still small and the stable provider/index boundary matters more than premature vector-index complexity.

Later `sqlite-vec` can replace only the vector lookup implementation while preserving:
- memory ids,
- canonical text,
- metadata,
- provider abstraction,
- Agent One integration.

## Security

Embedding rows persist only provider/model/vector metadata. API keys never enter the memory database.

## Tests

```powershell
& $py -m unittest tests.test_csp_universe_memory -v
```

Tests use a fake deterministic embedding provider; no network or NVIDIA key is required.
