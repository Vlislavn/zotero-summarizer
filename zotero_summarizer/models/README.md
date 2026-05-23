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
| `config.py` | `goals.yaml` schema: `GoalsConfig` + `LLMConfig`/`CorpusConfig`/`PrestigeConfig`/`ClassifierGateConfig`/… |
| `triage.py` | `SummarizeRequest/Response`, `TriageResult/Dimensions`, `QualityReview`, `PaperDigest`, batch + corpus + calibration models |
| `api.py` | HTTP request/response + write-path models (`*Request/*Response`, `AppState`, Zotero/pending shapes) |
| `__init__.py` | re-exports all of the above (the public `models` surface) |

**Boundaries:** depends only on `pydantic` + `domain`. Nothing here imports
`services/`/`storage/`/`api/`.
