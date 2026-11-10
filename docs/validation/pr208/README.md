# PR208 — аудит 50 агентов и добавленные функции

Этот пакет сохраняет результаты исходного аудита50агентов, а не текущую
live-приёмку. Последующие локальные проверки и ограничения находятся в
`data/pr208-functional-swarms/run-01/FINAL-HANDOFF.md`; сырые receipts локальны,
не являются публичными вложениями и не отмечают человеческий чеклист.

Текущий порядок завершения: [локальная ревизия от 2026-11-03](../../../data/pr208-functional-swarms/run-01/remaining-audit-v1/README.md).
Она отделяет технические остатки, физическую/содержательную приёмку и условные
эксперименты; данные и обслуживание работающего сервиса при ревизии не менялись.

```text
18 функций / 15 issues
        ↓
50 новых logical agents: 49 isolated writers + 1 readonly scout
        ↓
14 групп исправлений → parent RED/GREEN → main working tree
        ↓
10 764 Python PASS · 1 000 frontend PASS · 98 unique browser PASS
        └─ не человеческая, live-provider или scientific приёмка
```

**[Чеклист версии 3.3](manual-checklist.md)** сохраняет все18пунктов/15исходных issues.
Текущее действие повторения называется **Retry**. Для RSS подтвердить фактический
импорт: HTTP200/восстановленные entries недостаточны. `RssNamespaceWarning:` в
**Last source issue** означает ограниченное чтение после полной проверки XML,
а не failed refresh; прочие XML-ошибки по-прежнему отклоняются. Пустой/отклонённый
вход блокирует зависимый triage. Успешный импорт не доказывает его полезности.
[Функция → issue → добавленная часть](issue-scope.md) ·
[проверки и ограничения](results.json) · [50 run/session/report bindings](swarm.json) ·
[1360 runtime/test/config fingerprints](source-manifest.json) ·
[CHANGELOG](../../../CHANGELOG.md).

Изменения перенесены в основной checkout **без stage/commit/push**. Проверялся
изолированный build; существующий root `frontend/dist` не заменялся, deployment
не выполнялся. **Все18ручных чекбоксов остаются неотмеченными.**

## Что именно обследовано

Первичный workflow `27ab1399-c0d6-4f41-bc22-fd3d936b900b`:50/50spawns,
fresh contexts,49раздельных writer-worktrees. Первичная очередь ограничена6агентами;
тяжёлые проверки выполнялись по одной под общим kernel lock. Retained recovery
имеют собственный lifecycle, поэтому6не выдаётся за измеренный общий peak.

Из первоначальных запусков **40завершились,10failed**:9native timeouts и1acceptance
mismatch. Все10затем завершили retained recovery. Отдельные продолжения агента13
(один rejected, затем completed) и readonly агента50 **не являются новыми агентами**.
Исходные failures остаются NOT_PASS; наличие50отчётов не превращает их в50успешных
первичных запусков. Native/managed outputs сверены штатным acceptance-stripper:
6byte-identical,28различаются terminal newline,16—machine acceptance block.

Readonly follow-up того же агента50 проверил15изменённых production owners и
происхождение18функций по base `bd5e267`; дополнительных конкретных нарушений не
нашёл. Он **не запускал тесты**, не является новым независимым аудитором и не
сертифицировал receipts. Исправления принимал parent после реального воспроизведения.

## Исправленные контракты

| Функция / lanes | Подтверждённая проблема и минимальная коррекция |
|---|---|
| Reading policy,07/08 | Verifier больше не принимает `False` как index0 и `1` как supported; SKIM-причины видны до раскрытия, weak-evidence берётся из server flags |
| Related,13/15 | Версии ранжируются до identity closure, retrieval-window не исчерпывается дубликатами; submitted comments отделены от cited records |
| Question evidence,20 | Повреждённые persisted scope/span данные становятся stale/unknown, а не crash или основанием для promotion |
| Weekly,23 | JSON validation и ответ используют те же captured bytes; выбранный Markdown также захватывается до streaming |
| Offline controls,26 | Старый receipt не понижает continuation revision; любое несовпадение current/base, включая future base, даёт conflict |
| RSS cards,29/31 | Assessed-empty inputs не названы unassessed; malformed present provenance недоступна, но unqualified source observation не отвергается как card authority |
| Telegram CLI,34 | Игнорировавшиеся RSS-only flags явно запрещены до Settings; положительный saved-input workflow сохранён |
| Weekly feedback,36 | Доказанный prelaunch failure допускает retry; неопределённые child/publication evidence остаются held |
| Priority,41 | Development observations после объявленного frozen split не допускаются; равные и timezone-equivalent instants сохранены |
| Collections,46 | Empty retrieval считается по исходным suggestions, не по результату удаления workflow collections |
| Interpretation,49 | Без независимых human scores результат INCONCLUSIVE, даже если assigned executions завершились ошибками |

Не вводились новые scorer/store/runner/model или произвольная более строгая политика.
Например, равенство taxonomy/split timestamps и различие raw/effective config не
объявлены багами без соответствующего контракта. Дублирование manifest provenance
во всех Markdown/stdout полях отклонено. Candidate-схема31дала **2PASS/2FAIL** на
собственном producer/readback control; принята меньшая граница observation/card.

## Исполненные проверки

| Проверка | Receipt |
|---|---|
| Полный Python:577files,8последовательных partitions | **10 764PASS /166SKIP /0FAIL /0ERROR**,10 930уникальных записей |
| Полный frontend | **1000/1000PASS**,144suites |
| Свежий isolated build | **PASS**, bundle/source pins сверены |
| Built-browser | **98уникальных PASS**:94initial passes +12перепроверок,8overlap; это98точных skipped identities Python-набора, не дополнительная независимая выборка |
| Frontend lint | **0errors /13existing warnings** |
| Static gates | Все9pre-commit hooks passed; новый untracked frontend test проверен явно; final documentation closure отражена в `results.json` |

Первый whole cohort выявил некорректный Collection reseal fixture; следующий —
Telegram positive fixture с теперь запрещёнными RSS date flags. Начальный browser
run: **94PASS/4FAIL**, потому что старый DOM assert запрещал новый видимый SKIM label.
Исправлены только fixtures/assertions: valid DTO до reseal, реальные replay refusals,
admitted CLI options и WCAG≥4.5для **каждого** visible collapsed label. Все keyboard,
expanded-label, overflow и immutable-replay controls сохранены. Исходные failed
attempts не скрыты и не суммируются с итоговыми cohorts.

Лимиты не ослаблялись:20%available RAM,4096MiBowned-tree RSS,2048MiBswap growth,
900sguard safety cap,1spoll,2stermination grace. Чужие процессы не останавливались.
Parent открыл6новых PNG:4SKIM surfaces390/1440 и2isolated Related captures;
последние не являются full-styled-application acceptance. Остальные PNG не
объявляются просмотренными. Сырые receipts локальны; пути/hashes не притворяются
публичными вложениями.

## Scope и границы

- В base уже были offline queue/conflicts и practitioner routing. Добавленная
  часть FEATURE-10 — cold context ранее не открывавшейся synced paper; старые
  workflows остаются контролями. FEATURE-01 отделяет presets, Refresh и triage.
- Human UAT, live-model usefulness, Telegram account/SDK pagination, native desktop,
  deployed owner-authenticated HTTPS, physical device и scientific studies здесь
  **не выполнены**. Отсутствующий prerequisite означает BLOCKED, не PASS.
- Из166основных skips98исполнены отдельно браузером;68остаются unexecuted.
  Optional Telethon adapter не устанавливался ради зелёной строки.
- Source/scripted fixtures доказывают bounded machine contracts, не научную
  истинность, полезность рекомендаций, победу модели или полноту issues.

Исторические8агентов проверяли смешанный33-сценарный чеклист версии1; они не
засчитываются в этот рой. Их receipts сохранены в `historical_v1` обоих JSON;
исторический source manifest имеет pinned Git URL. Текущий manifest пересобран
из fresh snapshot, включая CSS/HTML/config и без старого absent-file tombstone.

Source: **Laws of UX vault — Hick’s Law, Tesler’s Law, Occam’s Razor**. Сначала убраны
устаревшие scope/«новый рой не запускался» assertions и duplicate authority predicates;
затем уточнены источник, видимая причина и recovery в существующих owners.
Дальнейшее дробление этих небольших границ не упрощает touched code. Это эвристики,
не measured comprehension benefit. Автоматически merge/close issues нельзя.
