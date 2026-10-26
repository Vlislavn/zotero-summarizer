# PR208 — какие ишьюсы покрывает чеклист

```text
issue requirement ≠ merged-PR reference ≠ executed machine test ≠ human acceptance
```

[Ручной чеклист](manual-checklist.md) · [результаты роя](README.md).
Сверены 39 primary issue bodies, 206 branch commits, name/status diff и scope
включённых PR. Начальный аудит привязан к `6e909596593b1ab0a89f3d54e0ee22ad9a5639c7`,
base — `bd5e26741dd70f3a359b984f759d68b32ac1eab9`; исправления — по текущему
[source manifest](source-manifest.json). В PR208 нет automatic closing-issue refs.

**Часть** — реализован ограниченный slice, а не весь acceptance ишьюса.
**Регрессия** — существующее поведение у затронутых callers/surfaces.
**Условно** — нужны отдельные реальные inputs/provider/native/device/human outcomes.
Ни одна строка не означает, что человек уже поставил PASS.

| Ишьюс | Что относится к PR / исходный subPR | Чеклист и что не заявляется |
|---|---|---|
| [#7](https://github.com/Vlislavn/zotero-summarizer/issues/7) | Часть: typed native edits/Pending, PR173–175; capability routing и Q&A checkpoints — отдельные contracts | N04/N05, D01/X03. Не вся broad pattern/evaluation/ADR acceptance; без нового metadata editor/history UI. |
| [#8](https://github.com/Vlislavn/zotero-summarizer/issues/8) | Full triage artifact, delayed materialization, richer distinct note types | N07 добавлен после роя. Restart/preservation/native readback отдельно от 10-paper usefulness study. |
| [#9](https://github.com/Vlislavn/zotero-summarizer/issues/9) | Часть: saved reading-plan presentation, PR134 | R01. Не полный conservative-generation/labelled-paper evaluation. |
| [#10](https://github.com/Vlislavn/zotero-summarizer/issues/10) | Часть: original artifact-URL admission, PR140 | R03a/b. Six-field production `method_and_code` ≠ deep-review CodeLink banner; live six-case/human acceptance отдельно. |
| [#11](https://github.com/Vlislavn/zotero-summarizer/issues/11) | Часть: setup save/recovery/verification, PR145; PR150 не inference-readiness | S01–04/X02a. **Positive Full local не реализован:** runtime envelope producer отсутствует; UNKNOWN — правильный отказ, не исполнение positive acceptance. |
| [#12](https://github.com/Vlislavn/zotero-summarizer/issues/12) | Часть: source-qualified CLI cards и read-only Weekly, PR136/139; frozen rank replay PR180 | F04/F05. Native dry-run не no-model/no-local-write; real cohort/usefulness metrics остаются отдельными. |
| [#13](https://github.com/Vlislavn/zotero-summarizer/issues/13) | Регрессия current labels/trajectory через изменённые writers и offline paths | N03/N06. Нет crash-atomic exactly-once JSONL/history UI. |
| [#14](https://github.com/Vlislavn/zotero-summarizer/issues/14) | Консолидирован в #13; native reconciliation — регрессия | N03. Не второй history subsystem и не Ops restore surface. |
| [#15](https://github.com/Vlislavn/zotero-summarizer/issues/15) | Часть: independent evidence/idea/writing policy, PR144 | R01/R02. Исправлены explanation gaps; научные quality/READ precision и idea-rescue study не измерены. |
| [#16](https://github.com/Vlislavn/zotero-summarizer/issues/16) | Часть: practitioner subscriptions/refresh, PR137 | F01. Useful-versus-hype discrimination/positive web-article generation условны. |
| [#17](https://github.com/Vlislavn/zotero-summarizer/issues/17) | Регрессия hosted/no-LLM settings/credential UX | S01/S04. Не новая whole-feature attribution по одному touched file. |
| [#18](https://github.com/Vlislavn/zotero-summarizer/issues/18) | Регрессия removed sidecars/structured QA через изменённые renderer/context owners | R04. Старые sidecars не удаляются; PDF/native validity не доказывает UI mock. |
| [#19](https://github.com/Vlislavn/zotero-summarizer/issues/19) | Часть: disconnected working-set review PR157; authenticated transport PR150 отдельно | N02/N06/X02c. Physical iOS, storage lifetime, trusted HTTPS/revocation не приняты машинными fixtures. |
| [#20](https://github.com/Vlislavn/zotero-summarizer/issues/20) | Регрессия shared OA/cached attachment/materialization callers | F03. Parent item creation ≠ attachment success; не новая acquisition engine. |
| [#21](https://github.com/Vlislavn/zotero-summarizer/issues/21) | Регрессия принятого non-ranking «Review suggests» варианта Read Next | F07 добавлен после роя. Не measured reranker и не Today selection evidence. |
| [#22](https://github.com/Vlislavn/zotero-summarizer/issues/22) | Изменённая architecture documentation | X03: contributor review, не новый пользовательский экран. |
| [#23](https://github.com/Vlislavn/zotero-summarizer/issues/23) | Историческое RepoDoc research/reject решение; соответствующего нового эксперимента в PR не установлено | X03. Не adoption KG/graph UI и не обязательный новый эксперимент. |
| [#25](https://github.com/Vlislavn/zotero-summarizer/issues/25) | Исторический medium audit ledger | X01 и конкретные touched-function regressions. Не сертификация всех 42 записей одним smoke. |
| [#26](https://github.com/Vlislavn/zotero-summarizer/issues/26) | Исторический low audit ledger; warranted Ops mutation regression | X01. Исправлен unknown-config Retrain; не whole-five-items/history acceptance. |
| [#27](https://github.com/Vlislavn/zotero-summarizer/issues/27) | Часть CLI Telegram, PR170→171→177, несмотря на пустую explicit-ref карту | E01. ML shadow, не calibrated hard gate. Реальная разрешённая collection и RU/EN holdout study условны. |
| [#28](https://github.com/Vlislavn/zotero-summarizer/issues/28) | Регрессия usable-review-before-feed-Add на API/approval/materialization/UI | F03/N05. Trash независим, review completion не auto-Add. |
| [#29](https://github.com/Vlislavn/zotero-summarizer/issues/29) | Регрессия reducing repeated conclusions при сохранении разных findings/quotes | R02. Убрана duplicate applicability; exact text identity не semantic comprehension evaluation. |
| [#30](https://github.com/Vlislavn/zotero-summarizer/issues/30) | Experimental partial original-source question evidence, PR147 | Q04/Q05. Все три linked appendices относятся к contract/eval scope; negative/null result может отвечать. Held-out answerability/false-promotion/latency и default-enable не установлены. |
| [#31](https://github.com/Vlislavn/zotero-summarizer/issues/31) | Регрессия Search UI/federation/session; core intent/query files не вводились этим diff | Q01. Нельзя приписывать PR208 новую whole-#31 implementation. |
| [#32](https://github.com/Vlislavn/zotero-summarizer/issues/32) | Часть source diagnostics и preservation через изменённый review path | R03a. Не полная historical incident RCA/representative live completion study. |
| [#33](https://github.com/Vlislavn/zotero-summarizer/issues/33) | Часть Today evidence/recovery observability, PR138 | F02. 30 consecutive + rejected sample / untouched later-window usefulness diagnosis не выполнены этим аудитом. |
| [#34](https://github.com/Vlislavn/zotero-summarizer/issues/34) | Retired overlapping umbrella; remaining obligations #32/#33 | F02/R03a. Closed/duplicate metadata не доказывают устранение причин исходного incident. |
| [#36](https://github.com/Vlislavn/zotero-summarizer/issues/36) | Historical source-plumbing repair PR35; regression через текущие owners | R03a. Не новая whole repair attribution к PR140. |
| [#37](https://github.com/Vlislavn/zotero-summarizer/issues/37) | Developer-only Support comparator PR152 | E03. Frozen plumbing ≠ endpoint/resource qualification или model winner. |
| [#38](https://github.com/Vlislavn/zotero-summarizer/issues/38) | Developer-only Priority comparator PR154 | E03. Production ranking adoption и real held-out labels не заявлены. |
| [#40](https://github.com/Vlislavn/zotero-summarizer/issues/40) | Часть retained evidence, PR149 | R05/R04. Export internal-only; historical curl не текущий endpoint. Five-digest/expert-effort study отдельно. |
| [#41](https://github.com/Vlislavn/zotero-summarizer/issues/41) | Регрессия full-reading hierarchy/accessibility/one-home rationale | R02/Q05a/X02b. Setup labels не whole-review acceptance; counterbalanced human comprehension остаётся человеку. |
| [#42](https://github.com/Vlislavn/zotero-summarizer/issues/42) | Developer-only Collection comparator PR155; PR167 diagnostic relation отдельно | E03. Membership mutation, practitioner diagnosis и measured winner не следуют из protocol PASS. |
| [#46](https://github.com/Vlislavn/zotero-summarizer/issues/46) | Часть Related PR148 + saved-seed Discovery PR153 | Q02/Q03. Исправлена transitive identity closure. Comment-only retrieval recall и 10–20 owner-labelled-seed usefulness study остаются открыты. |
| [#47](https://github.com/Vlislavn/zotero-summarizer/issues/47) | Operator report/capture/manual/weekly/training ledger PR162/163/164/166/168 | E02. Не dashboard; live first-run descriptive findings, historical missing evidence и unbiased-quality claims раздельны. |
| [#48](https://github.com/Vlislavn/zotero-summarizer/issues/48) | Часть durable question persistence/API/UI PR169/178/179; native dependencies PR173–175 | N01–05. App/device/answer/native outcomes раздельны; human Today/Library/native acceptance ещё не выполнена. |
| [#49](https://github.com/Vlislavn/zotero-summarizer/issues/49) | Opt-in Interpretation PR158, dependencies PR149/205 | E04. Defaults unchanged; no measured scientific benefit; все assigned failures в denominators. |
| [#160](https://github.com/Vlislavn/zotero-summarizer/issues/160) | Research-policy proposal; не реализован автоматический code/group filter | F06. PR183/198 prefilter metadata — другой scope, не данный feature. |
| [#161](https://github.com/Vlislavn/zotero-summarizer/issues/161) | **Не реализован** declared-arXiv collection/backfill + last-author/personal h-index/organisation contract | F06: известный унаследованный дефект. В base уже те же collection slice, author slice и присвоение max-h первому имени; текущий PR их не исправляет. Не менять ingestion/ranking под видом checklist work и не отмечать их PASS. |

## Добавления после проверки полноты

- N07: full triage artifact → restart → delayed native materialization, а не просто editor/Pending.
- F07: отдельный Read Next non-ranking regression.
- R03a/b: original-source diagnostics отдельно от production six-field method/code.
- X02a/b/c: setup accessibility, reading comprehension и device/gateway отдельно.
- D01–03: Q&A checkpoint/capability, raw RSS maintenance, selected-root CLI; эти
  функции не выдаются за браузерные controls.

## Provenance, которую нельзя упрощать

Merged body reference может быть зависимостью. PR150 не доказывает #11 inference;
PR173–175 не доказывают все требования #7/#48; PR167 не whole-#42. Интегрированные
байты PR135/142 не являются утверждением, что эти исходные PR имеют GitHub state
Merged. Пустые explicit links не исключают фактический Telegram scope. При
следующем изменении требований пересматривать эту матрицу, а не закрывать ишьюсы
по совпадению номера или зелёному агрегату тестов.
