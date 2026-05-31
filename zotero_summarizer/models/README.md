# models — Pydantic contract (config + API shapes)

Every request/response body and the `goals.yaml` schema. Split by group; the
package `__init__` re-exports everything, so `from zotero_summarizer.models
import X` works unchanged.

```
config.py ──GoalsConfig──> api.py (AppState)
triage.py ──(domain)        all three ──*──> __init__.py  ──>  from ...models import X
```

| file | holds |
|---|---|
| `config.py` | `goals.yaml` schema: `GoalsConfig` + `LLMConfig`/`CorpusConfig`/`PrestigeConfig`/`ClassifierGateConfig`/… Migrates a legacy flat `llm:` block into `llm_routing` when the latter is absent. |
| `providers.py` | per-stage LLM routing: `ProviderConfig`/`ProviderType`, `StageModelConfig`, `LLMRoutingConfig`, `resolve_stage()` (stage → effective provider+model, inheriting `default`). `ProviderConfig.is_local` flags loopback endpoints (→ serial triage concurrency). Pure data + lookups — no env reads, no client building. |
| `triage.py` | `SummarizeRequest/Response`, `TriageResult/Dimensions`, `QualityReview`, `PaperDigest`, batch + corpus + calibration models |
| `api.py` | HTTP request/response + write-path models (`*Request/*Response`, `AppState`, Zotero/pending shapes) |
| `__init__.py` | re-exports all of the above (the public `models` surface) |

**Boundaries:** depends only on `pydantic` + `domain`. Nothing here imports
`services/`/`storage/`/`api/`.
