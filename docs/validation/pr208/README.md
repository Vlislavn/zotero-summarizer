# PR208 — добавленные функции и ручная приёмка

```text
Функция, добавленная в PR → исходный GitHub issue → ручная проверка
```

**[Актуальный чеклист версии2](manual-checklist.md):18функций из15исходных GitHub issues.**
[Функция → issue → реализация](issue-scope.md) · [исторические машинные receipts](results.json) ·
[8 run IDs / hashes](swarm.json) · [проверенные code bytes](source-manifest.json).

По уточнению пользователя убраны существующие функции/общие регрессии, внутренние
технические проверки и нереализованный backlog. Проверяется добавленная часть
функционала, а не весь acceptance каждого issue. Человеком версия2 не выполнена;
новый рой по ней не запускался.

## Исторический аудит версии1

Рой проверял прежний смешанный33-сценарный чеклист, а не текущую версию2. Draft SHA256 `2c09def0fabcd8e7d3154b50484ae9d268764fefe73c93c8fc6de387f62db4fe`;
workflow `d90f6c13-6425-4295-a46e-db0f908958ed`. Все 8 завершены, fresh contexts,
6 зон + issue-completeness/human-safety. Их работа — **readonly primary/source
review**, не исполненная пользовательская приёмка. Воспроизведение и реальные
локальные проверки выполнял parent в изолированных fixtures без личных
данных/аккаунтов/моделей. Ограничения каждого вида свидетельств сохранены.

## Найденные продуктовые дыры

| Дефект | Причина / минимальное исправление | Исполненный discriminator |
|---|---|---|
| Manual-note Save очищал dirty draft без app ACK | В HTTP boundary требуется literal `saved === true`, до sync/clean baseline | `{}`, false, string, null,204; реальные route guards; legitimate device/app saves сохранены |
| Retrain мог выбрать `logreg` до загрузки/после ошибки config | Убрано угадывание; нет label refresh/training без известного classifier | Pending/error/missing config; nondefault acknowledged model pinned across label refresh; same-job recovery controls |
| Related показывал seed-version/дубликаты через identifier bridge | Переиспользован Search union-find, closure до выбора и после detail; seed/rejected families исключены | Все6перестановок × seed/candidate family, no-ID same-title control, detail-only bridge, rejected-family control |
| Missing Ollama советовал brew на любой ОС | Homebrew только Darwin; официальный platform download URL иначе | Darwin/Linux/Windows/unknown; inventory запрещён sentinel |
| Policy withholding терял конкретную причину | Добавлены missing-target/original-value explanations в существующий disclosure | Обе server flags × compact/standard/full; входные DTO не изменяются |
| Applicability повторялась в двух раскрытиях | Удалена duplicate full-digest row | Одна supplied literal relevance sentence во всех3режимах |
| Legacy supported goal был одновременно unknown | Удалён второй abstention predicate; используется общий `isSupportedGoal` | Оmitted abstention ×3 surfaces; существующие false/abstained controls сохранены |

Первый qualified RED: frontend **85:62 PASS /23 FAIL**, Python **48:35 PASS /13 FAIL**.
Те же сценарии после исправлений: **85/85 и48/48 PASS**. Дополнительные detail /
rejected-family controls и общие post-fix gates записаны в `results.json`.

| Текущая проверка | Фактический результат |
|---|---|
| Frontend, полный набор | **997/997 PASS** |
| Python, все577test files в8последовательных процессах | **10 727 PASS /166 SKIP /0 FAIL**,10 893уникальных записей |
| Свежий build + owned built-browser | **98/98 PASS**; это98точных skipped identities основного Python-набора, не дополнительная независимая выборка |
| Pre-commit +6новых artifacts | **PASS** |
| Frontend lint | **0 errors /13 existing warnings** |
| Независимый bounded follow-up | **PASS-with-bounds**; source-review, не повторная сертификация receipts |

Однопроцессный повтор Python-suite остановлен guard по20%free-RAM floor через527.5s:
**не PASS**. Весь тот же набор577files затем выполнен8последовательными группами
≤80files с неизменными лимитами; уникальность cases и точные166skip IDs сверены.
Защита не ослаблялась, чужие процессы не останавливались. Из166основных skips98
исполнены отдельно браузером; оставшиеся68в этом goal не объявляются проверенными.
Из147созданных PNG parent открыл4полных reading-снимка390/1440; остальные не
объявляются просмотренными человеком.
Ни один skipped case не превращён в PASS; реальные scientific/hardware/native-GUI
или physical-device результаты из этих чисел не выводятся.

**Поправка старому аудиту:** прежний вывод об отсутствии original AUD14-02 нельзя
использовать для текущего goal-label случая. На6e он воспроизведён и исправлен
здесь. Исторический final295 остаётся историческим snapshot, не вечным сертификатом.

## Исправление scope чеклиста по уточнению пользователя

Версия1 была слишком широкой: смешивала добавленные функции, регрессии старого
приложения, технические проверки и backlog. Даже source-supported regression
не становится новой функцией только потому, что файл изменён.

Например, richer delayed triage note (#8) и non-ranking Read Next proposal (#21)
уже существовали в base; их прежние N07/F07 удалены из актуального чеклиста.
Существующие Setup, Pending/editors, Search (#31), retrain/labels, внутренний
audit export, race/ACK/MCP/backup checks также не создают отдельных новых
пользовательских пунктов.

В версии2 каждая строка называет добавленную функцию, **исходный GitHub issue,
по которому её делали**, действия и ожидаемый результат. Компаньон содержит
конкретную реализацию/subPR, а не общий каталог39связанных issues. Результаты
прежнего роя и code tests сохранены как исторические факты, не как приёмка новой версии.

## Границы решения

- Семь подтверждённых продуктовых дефектов исторического аудита исправлены.
  Текущая правка меняет только documentation scope, не runtime-код и не backlog.
- #11 positive Full-local **не реализован** без executing-runtime envelope
  producer; честный UNKNOWN/refusal не заменяет usable-local acceptance.
- #160 — research policy; #161 collection-tail/last-author/personal h-index /
  organisation gaps унаследованы из base и не реализованы этим PR. Рой их не
  «закрыл» проверкой prefilter metadata.
- Live-provider usefulness, private frozen inputs, Telegram credentials, native
  desktop interoperability, физический iOS/HTTPS deployment и human scientific
  studies остаются отдельными prerequisites/результатами. Они включены в checklist
  как conditional/BLOCKED, не скрыты в общем GREEN.

## Проверка и повторение

Использованы существующие source tests и bounded guard:20% available-RAM floor,
4096MiB tree RSS,2048MiB swap growth, именованный wall safety cap900s; checks
сериализованы, source/index/bundle pins проверяются, owned descendants убираются.
Ни флаги environment, ни mocked success не считаются доказательством acceptance.

Базовые команды: `pre-commit run --all-files`, `pytest -q --forked`,
`npm test --prefix frontend -- --no-file-parallelism --maxWorkers=1 --minWorkers=1`,
`npm run lint --prefix frontend`, `npm run build --prefix frontend`.
Browser opt-ins и installed dependencies описаны в [tests README](../../../tests/README.md);
нужен новый isolated build, не чужой/stale root `dist`.
Фактические команды, counts, JUnit/Vitest SHA и guard scope — в `results.json`;
сырые локальные receipts сохранены, но не притворяются публичными вложениями.

Во время текущего whole run обнаружен ранее опубликованный documentation-contract
конфликт: test разрешал только3main docs и отвергал5уже опубликованных validation
artifacts. Исправление сохраняет ровно3main docs и **явный закрытый список** двух
validation bundles; link checker теперь проверяет и их Markdown. Никакого
безусловного исключения каталога или удаления substantive assertions нет.

## Простота и итоговая приёмка

Source: Laws of UX vault — Hick’s Law, Tesler’s Law, Occam’s Razor. В версии2
сначала убраны лишние regression/developer/backlog пункты, затем добавлена прямая
привязка каждой функции к исходному GitHub issue. Исторические code-исправления
вычли duplicate rationale, второй supported-goal predicate и guessed classifier,
добавив только недостающие admission/explanation/recovery. Это UX-эвристики,
не measured comprehension benefit. Reuse: прежние request/query/state owners,
Search `_Union`, existing disclosures и guard; новых моделей/stores/frameworks нет.
Дальнейшее деление этого небольшого исправления не упрощает touched code.

**Разрешён следующий шаг: ограниченный ручной UAT на копии.** Решение человека о
приёмке ещё отсутствует. Не merge/close issues автоматически и не называть этот
отчёт full production/local/hardware/scientific/device certification.
