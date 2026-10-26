# PR208 — добавленная функция → исходный GitHub issue

```text
Diff от main@bd5e267 + scope реализации → новая функция → её GitHub issue
```

[Ручной чеклист версии2](manual-checklist.md) · [PR208](https://github.com/Vlislavn/zotero-summarizer/pull/208).
Здесь **не все связанные/упомянутые issues**: только исходные GitHub issues
для18добавленных функциональных возможностей. Номер bugfix, зависимость или
исторический audit ledger сами по себе не делают существующую функцию новой.
Указанный issue может иметь больший scope, чем добавленная в PR часть.

| ID | Добавленный функционал | Исходный GitHub issue | Реализация в PR208 |
|---|---|---|---|
| FEATURE-01 | HackerNoon presets в обычных RSS-подписках | [#16](https://github.com/Vlislavn/zotero-summarizer/issues/16) | [PR137](https://github.com/Vlislavn/zotero-summarizer/pull/137); RssFeedsSection и practitioner feed routing. |
| FEATURE-02 | Why selected: причины выбора Today и отдельный evidence signal | [#33](https://github.com/Vlislavn/zotero-summarizer/issues/33) | [PR138](https://github.com/Vlislavn/zotero-summarizer/pull/138); SelectionEvidence и daily-selection projection. Не measured ranking improvement. |
| FEATURE-03 | Сохранённый план чтения рядом с действием, без Details | [#9](https://github.com/Vlislavn/zotero-summarizer/issues/9) | [PR134](https://github.com/Vlislavn/zotero-summarizer/pull/134); ReadingPlan/PaperReview. Не новая генерация всего review. |
| FEATURE-04 | Раздельные idea/evidence/writing и объяснимое ограничение глубины чтения | [#15](https://github.com/Vlislavn/zotero-summarizer/issues/15) | [PR144](https://github.com/Vlislavn/zotero-summarizer/pull/144), [PR181](https://github.com/Vlislavn/zotero-summarizer/pull/181); reading-policy projection/OrthogonalSignals. |
| FEATURE-05 | Автосохранение каждого отправленного review-вопроса и composite My notes | [#48](https://github.com/Vlislavn/zotero-summarizer/issues/48) | [PR169](https://github.com/Vlislavn/zotero-summarizer/pull/169), [PR178](https://github.com/Vlislavn/zotero-summarizer/pull/178), [PR179](https://github.com/Vlislavn/zotero-summarizer/pull/179); question storage/API/AskPaperBox. |
| FEATURE-06 | Related saved papers, отдельно consent-bound comments assessment | [#46](https://github.com/Vlislavn/zotero-summarizer/issues/46) | [PR148](https://github.com/Vlislavn/zotero-summarizer/pull/148); RelatedPapers и `library/related.py`. Comment-only candidate recall не реализован. |
| FEATURE-07 | Find new relevant из saved seed с retained Search session | [#46](https://github.com/Vlislavn/zotero-summarizer/issues/46) | [PR153](https://github.com/Vlislavn/zotero-summarizer/pull/153); `search/discovery.py` и Search discovery view. |
| FEATURE-08 | Opt-in original-source question evidence перед review selection | [#30](https://github.com/Vlislavn/zotero-summarizer/issues/30) | [PR147](https://github.com/Vlislavn/zotero-summarizer/pull/147); `_question_evidence`, `_question_policy`, QuestionEvidence. |
| FEATURE-09 | Weekly UI: сохранённые отчёты, bookmark и original downloads | [#12](https://github.com/Vlislavn/zotero-summarizer/issues/12) | [PR139](https://github.com/Vlislavn/zotero-summarizer/pull/139); Weekly/WeeklyDigest и saved-report API. |
| FEATURE-10 | Disconnected working-set review, durable edits и conflict resolution | [#19](https://github.com/Vlislavn/zotero-summarizer/issues/19) | [PR157](https://github.com/Vlislavn/zotero-summarizer/pull/157); offlineStore/syncClient и `services/sync/`. |
| FEATURE-11 | Explicit owner-authenticated remote HTTPS/private-Unix mode | [#19](https://github.com/Vlislavn/zotero-summarizer/issues/19) | [PR150](https://github.com/Vlislavn/zotero-summarizer/pull/150); `serve-mobile` и gateway admission. Не #11 inference readiness. |
| FEATURE-12 | Source-qualified project engineering cards из RSS | [#12](https://github.com/Vlislavn/zotero-summarizer/issues/12) | [PR136](https://github.com/Vlislavn/zotero-summarizer/pull/136); research-feed producer/card/proposal qualification. |
| FEATURE-13 | Telegram auth/collection/saved-input news processing | [#27](https://github.com/Vlislavn/zotero-summarizer/issues/27) | [PR170](https://github.com/Vlislavn/zotero-summarizer/pull/170), [PR171](https://github.com/Vlislavn/zotero-summarizer/pull/171), [PR177](https://github.com/Vlislavn/zotero-summarizer/pull/177); Telegram transport/runtime/public CLI. |
| FEATURE-14 | Retained feedback analysis, manual report и weekly scheduling | [#47](https://github.com/Vlislavn/zotero-summarizer/issues/47) | [PR162](https://github.com/Vlislavn/zotero-summarizer/pull/162), [PR163](https://github.com/Vlislavn/zotero-summarizer/pull/163), [PR164](https://github.com/Vlislavn/zotero-summarizer/pull/164), [PR166](https://github.com/Vlislavn/zotero-summarizer/pull/166), [PR168](https://github.com/Vlislavn/zotero-summarizer/pull/168); feedback_analysis/report/scheduler. |
| FEATURE-15 | Frozen criterion–evidence support comparator | [#37](https://github.com/Vlislavn/zotero-summarizer/issues/37) | [PR152](https://github.com/Vlislavn/zotero-summarizer/pull/152); `tools.method_validation support`. |
| FEATURE-16 | Frozen reading-priority comparator | [#38](https://github.com/Vlislavn/zotero-summarizer/issues/38) | [PR154](https://github.com/Vlislavn/zotero-summarizer/pull/154); `tools.method_validation priority`. |
| FEATURE-17 | Existing-collection membership comparator | [#42](https://github.com/Vlislavn/zotero-summarizer/issues/42) | [PR155](https://github.com/Vlislavn/zotero-summarizer/pull/155); `tools.method_validation collections`. PR167 diagnostic packaging — не исходная реализация этой функции. |
| FEATURE-18 | Opt-in Interpretation A/B/C и Finding / Scope / Unproven | [#49](https://github.com/Vlislavn/zotero-summarizer/issues/49) | [PR158](https://github.com/Vlislavn/zotero-summarizer/pull/158), source dependencies PR149/205; `faithbench interpretation`. #40 dependency не подменяет исходный issue#49. |

## Что удалено из чеклиста версии1

- Общие проверки существующих Setup, labels, retraining, Search constraints,
  Pending/editors, Reader и attachments; исправления race/ACK, native approval,
  backup internals, MCP и архитектурные проверки. Новые внутренние модули не
  означают новую пользовательскую функцию, если сам workflow уже был в base.
- #8: полный triage artifact и richer triage note **уже были в base**. Diff здесь
  меняет native mirroring, а не добавляет эту функцию. Старый N07 не является новой функцией PR208.
- #21: non-ranking Read Next proposal **уже был в base**. Изменения callers/offline
  и защиты черновика не означают добавление этого предложения заново.
- #31: core intent/query Search существовал ранее; новые Related/Discovery/question
  evidence проверяются под собственными исходными #46/#30.
- Внутренний audit export #40 не имеет публичного UI/CLI и не создаёт отдельный
  ручной пользовательский сценарий. Method/code admission fixes #10 также не
  выдаются за впервые добавленный method/code UI.
- Нереализованные #160/#161 — не пункты приёмки добавленного функционала.

## Статус проверки

Исторический рой8агентов и его33scenario IDs относятся к **версии1 со смешанным
scope**. Текущая версия2 составлена заново по уточнению пользователя; прежний рой
не считается её проверкой, новый рой здесь не запускался. Машинные результаты
приложения в [results.json](results.json) сохранены как отдельные свидетельства,
не как отметки PASS у18ручных функций.
