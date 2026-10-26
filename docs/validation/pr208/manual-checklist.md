# PR #208 — ручная приёмка человеком

```text
Копия данных → основной UI → выбранные интеграции → решение человека
                 A–D            E–G
Разработчик: fault/race/replay проверки — отдельно, не ручной маршрут
```

**Чеклист финализирован после роя и исправлений, но человеком ещё не выполнен.**
[PR #208](https://github.com/Vlislavn/zotero-summarizer/pull/208) ·
[что проверил рой и что исправлено](README.md) ·
[границы всех 39 ишьюсов и ссылки на исходные PR](issue-scope.md).
Ишьюс в строке означает связь/регрессию, **не обещание закрытия всего ишьюса**.
Идентификаторы S/F/R/N/Q/E/X сохранены из чеклиста, переданного агентам.

## Перед началом

- Записать: `git rev-parse HEAD`, ОС, браузер/версию, origin, режим AI и размер окна.
- Использовать отдельный project root и **отдельно выбранную копию** Zotero/PDF.
  Сам по себе другой project root не копирует библиотеку и не делает записи безопасными.
- Для первого прохода отключить AI, фоновые задания и native writers. Для разделов
  с записью включать только явно разрешённую интеграцию с копией; подготовить backup.
- Сохранённые обзоры/отчёты/сессии и Pending не появляются в чистом проекте.
  До сеанса записать доступные ключи в таблицу ниже. Отсутствующие данные → BLOCKED,
  не SQL-редактирование или изготовление «успешных» receipts человеком.
- Просмотр UI не всегда пассивен: обычный `/paper/KEY` может начать review;
  для просмотра использовать `?autoRun=false`. Переход Related может открыть обычный
  URL. Reader может перестроить stale artifact; Build/Ask/Search/Refresh/Save/Add/
  Trash/Retrain имеют самостоятельные эффекты. Для чисто визуального сеанса их
  отключает подготовивший окружение разработчик, а не только query-параметр.
- Не очищать browser storage с несинхронизированными заметками. Не повторять
  неопределённую native-запись ради получения зелёного результата.

### Данные сеанса — заполнить до действий

| Нужный пример | Ключ/URL/ID в копии |
|---|---|
| Paper с полным review; READ / capped SKIM / SKIP | ___ / ___ / ___ |
| Unknown/missing assessment; нет PDF; длинный/legacy review | ___ / ___ / ___ |
| Saved seed, уже прочитанная related paper, версии DOI/arXiv | ___ / ___ / ___ |
| Today: без review, usable review, rejected; Read Next | ___ / ___ / ___ |
| Saved Search session; Weekly run, включая пустой/pending | ___ / ___ |
| Approvable Pending proposals; backup копии | ___ / ___ |
| Синхронизированный working set; второй клиент для конфликта | ___ / ___ |

Для каждого подпункта: **PASS / FAIL / BLOCKED / N/A** + фактический результат.
N/A — функция намеренно не входит в выбранный режим; нет prerequisite — BLOCKED.
Чекбокс означает «проверено и записан результат», а не автоматически PASS.

## A. Setup и настройки

| Проверено | ID / ишьюсы | Действие → наблюдаемый PASS |
|---|---|---|
| ☐ | S01 — #11, #17 | В свежем проекте пройти `/setup` в No LLM, сохранить и reload. Режим/цели сохранены; нет обязательного LLM key; «сохранено» не названо operational Ready. |
| ☐ | S02 — #11 | Full local → Existing: ввести endpoint/model **до** списка моделей. Поля остаются редактируемыми; несовместимый preset имеет причину отказа; listing не выдаётся за загрузку/проверенную ёмкость. Без runtime evidence — UNKNOWN, не Ready. **Положительная готовность Full local сейчас не реализована.** |
| ☐ | S03 — #11 | Явно Verify в допустимом окружении; раскрыть результаты. Видны реальные выполненные/непроверенные проверки. Stop — только на действительно запущенном собственном run; после Stop нет ложного Ready. Retry не означает прекращение удалённой работы. Реальная inference — раздел E. |
| ☐ | S04 — #11, #17 | Открыть `/settings#ai-models`, `#rss-feeds`, `#university-access` с холодной страницы и внутри неё. Нужный блок открыт и поле в фокусе; переход не запускает Save/Verify/Refresh. Изменённый login URL недоступен до подтверждённого Save; посторонний черновик не блокирует прежний сохранённый URL. |
| ☐ | X02a — #11 | Пройти wizard клавиатурой при 320/390/768px и desktop. Видны подписи и текущий шаг, фокус предсказуем, ошибки не только цветом; длинные endpoint/model не расширяют страницу. Browser emulation не считается iPhone-тестом. |

## B. Источники, Today, Read Next, Weekly

| Проверено | ID / ишьюсы | Действие → наблюдаемый PASS |
|---|---|---|
| ☐ | F01 — #16 | Settings → Sources: набрать custom draft, добавить HackerNoon presets, отключить один, reload. Draft не теряется при preset-save; повторное добавление не включает отключённый feed. Refresh выполнять отдельно: пусто, ошибка и offline различимы. Live triage — раздел E. |
| ☐ | F02 — #33, #34 | Today → Why selected у доступных контрастных примеров. Relevance отделена от evidence; unknown/legacy не названы плохой наукой; exploration обозначена; объяснение относится к выбранной статье. Это **не** доказательство качества отбора. |
| ☐ | F07 — #21 | Library → Read Next: открыть предложение, не подтверждать. «Review suggests» не становится сохранённым вердиктом/новым ранжировщиком; явно сохранённый пользовательский приоритет остаётся отдельным. |
| ☐ | F03a — #28, #20 | У Today-строки без usable full-text review Add недоступен с причиной; Trash доступен. Генерация review сама не добавляет статью. Проверка реального Add/attachment — раздел F. **Trash меняет feedback.** |
| ☐ | F05 — #12 | `/weekly`: выбрать сохранённый run, bookmark/reload, раскрыть cards/pending, скачать JSON/Markdown. Остаётся тот же run; пустой отчёт не ошибка; pending review не назван отсутствующим PDF; unverified observations не названы доказанным применением. Просмотр/скачивание не генерируют новый отчёт. |

## C. Чтение, объяснения и вопросы

| Проверено | ID / ишьюсы | Действие → наблюдаемый PASS |
|---|---|---|
| ☐ | R01 — #9, #15 | Открыть доступные READ/capped SKIM/SKIP/unknown на полной странице, compact и HTML brief. Один упорядоченный план рядом с действием; targets/skip hints буквальны; missing time ≠ 0; capped time — сохранённая оценка. Unknown не оживляет старые инструкции. |
| ☐ | R02 — #15, #29, #41 | Раскрыть Idea/Evidence/Writing и Full digest. Идея, scientific evidence и writing не смешаны; applicability и writing reasons не повторены; отсутствующие targets/original value объяснены; legacy supported goal не назван unknown. Все разные caveats/цитаты доступны. |
| ☐ | R03a — #10, #32, #34, #36 | На сохранённом результате сравнить claim/число/URL/quote с **исходной** статьёй. Репозиторий «найден» не значит «принадлежность проверена»; generated/map notes не заменяют original evidence. Source/parser/verifier failure не научный отрицательный вывод. Production method/code — раздел E. |
| ☐ | R04 — #18, #40 | На подготовленном artifact открыть Figures & full brief. Есть только текущие audited outputs; при показанной stale/error-проблеме понятен rebuild, старые ссылки не предлагаются как текущие. Build не обещает Markdown. Изменение файлов/удержание rebuild выполняет разработчик, не человек. |
| ☐ | N01 — #48 | В Today и Library открыть paper **без PDF/успешного review**, отправить typed и preset question. При недоступном ответе вопрос остаётся в My notes после reload, отдельно от manual note. Повторная осознанная отправка — новый вопрос; Retry answer — та же отправка. Онлайн Ask может вызвать QA и mirror; нет отдельной «Save question only» кнопки. |
| ☐ | Q05a / X02b — #30, #46, #41 | Раскрыть длинные evidence/IDs/locators: текст целиком читается и копируется, в том числе при 390/1440px. Для полного review дополнительно 320px, 200% text resize, spacing override, keyboard и light/dark: нет потери caveats/перекрытий. Проверить понимание, а не только геометрию. |

## D. Заметки и решения

| Проверено | ID / ишьюсы | Действие → наблюдаемый PASS |
|---|---|---|
| ☐ | N02 — #48, #19 | Сохранить manual note, изменить; уйти по ссылке, history и доступным Prev/Next. Cancel оставляет черновик; Leave уходит без скрытого Save; reload/close спрашивает нативно. После подтверждённого Save лишнего prompt нет. Очистить и Save — существующая операция, не отдельный Delete API. |
| ☐ | N03 — #13, #14 | Save приоритет X с rationale → Update Y → retract → reload. Видно текущее решение; изменение приоритета не стирает комментарий, отмена черновика ничего не пишет. Наличие transition-history UI и crash-exactly-once JSONL **не обещается**. |
| ☐ | X01a — #25, #26 | Ops → System: виден текущий loaded gate, не «новейший файл». Пока classifier config неизвестен/ошибочен, Retrain недоступен, `logreg` не угадан. Сам Retrain — отдельное согласие в разделе E. |

## E. Условные AI / сеть / операторские действия

Нужны разрешённые источники, выбранные routes и существующий corpus/assets.
**Не запрашивать новые API keys ради чеклиста**; нет уже доступного окружения — BLOCKED.
Не загружать локальные веса без отдельного ресурсного допуска. No LLM не может
положительно принять Generate/Ask/Add нового feed item без прежнего usable review.

| Проверено | ID / ишьюсы | Действие → наблюдаемый PASS |
|---|---|---|
| ☐ | S01/S03 live — #11, #17 | В уже разрешённом hosted окружении выбрать preset/model, Save/reload, явно Verify. Реальная inference и no-write pipeline наблюдаются отдельно от listing. Local UNKNOWN не засчитывать как положительную Full-local готовность. |
| ☐ | F01 live — #16 | Разрешённые concrete practitioner и hype-примеры проходят реальный triage; вручную проверить смысл результата и web-article review. Scripted regression не доказывает полезность классификации. |
| ☐ | R03b — #10 | На abstract/direct-PDF/long-map примерах проверить шесть `method_and_code` полей или честные null, источники, persistence/reload и итоговую note. CodeLink banner недостаточен; неподтверждённые команды/файлы — FAIL. |
| ☐ | Q01 — #31 (регрессия) | Targeted Search: narrow comparison, Query plan, proposed против явно подтверждённых phrase/type constraints; сменить sort и reload session. Сохраняются candidate identity/полный retained pool; предложения модели не стали молчаливыми фильтрами. Retrieval не обещает полноту литературы. |
| ☐ | Q02 — #46 | Show related saved papers: включены already-read, ≤3 distinct families, нет собственной DOI/arXiv-версии. Cancel «Try with comments» не вызывает assessment; Confirm явно называет обработчик/private context. Изменить/очистить manual note и refresh: старое assessment не остаётся текущим. Local display comments не равен отправке провайдеру. |
| ☐ | Q03 — #46 | Saved seed → Find new relevant без private consent, затем отдельно с разрешённым consent. Новыми рекомендованы только confirmed misses; unknown membership явно обозначен. Review/Add отдельны; stale result не разрешает новый эффект. Повтор uncertain Add не ручной тест. |
| ☐ | Q04 — #30 | Отдельно opt-in original-source question evidence. Проверить оригинальную цитату и реально отвечающий результат, в том числе отрицательный/null scientific finding. Partial/unknown не «нет ответа во всей статье». Это эксперимент; улучшение ranking/latency требует отдельного held-out исследования. |
| ☐ | X01b — #26 | Только с разрешением на refresh labels/training/artifact replacement нажать Retrain. Выбрана подтверждённая конфигурация, статус привязан к одному job; при недоступном статусе Retry status читает его, не запускает новое обучение. История/restore — CLI, не Ops-кнопка. |

## F. Условная native-приёмка — только копия Zotero

| Проверено | ID / ишьюсы | Действие → наблюдаемый PASS |
|---|---|---|
| ☐ | F03b — #20, #28 | Explicit Add usable review (включая negative recommendation), затем mixed batch. Заблокированные rows не объявлены добавленными; проверить **реальные** parent item, PDF/attachment и partial errors. Item created ≠ PDF attached. |
| ☐ | N04 — #7, #48 | Существующие tag/collection/priority/My notes editors: Save, отказ force, обновление черновика. Текст сохраняется при отказе/неопределённом исходе; manual clear не стирает questions. Закрыть Zotero для нормального положительного write; не форсировать ради PASS. |
| ☐ | N05 — #7, #48 | Ops → Pending Changes: видимые filter/selection, Preview → отдельно Edit/Save → explicit Apply/Reject. Проверить backup, terminal row и реальную библиотеку. Direct/system-mirror history не разрешает Pending approval; нет обещания exposed Undo. Неопределённый outcome сначала сверить, не replay. |
| ☐ | N07 — #8 | На заранее подготовленной scored row с полным triage artifact перезапустить **собственный** сервис, позднее materialize. Прочитать native note без app/PDF: summary/methods/findings/relevance/limitations/guidance сохранены. Legacy fallback явно помечен; triage/digest/manual/verdict notes не перезаписывают друг друга. Полезность на 10 статьях — отдельно. |

## G. Условные device/offline/deployment

| Проверено | ID / ишьюсы | Действие → наблюдаемый PASS |
|---|---|---|
| ☐ | N06 — #19 | Сначала active offline shell и завершённый Sync now; **тот же origin/storage**. Остановить собственный backend, открыть ещё не открывавшуюся synced paper, сохранить note/verdict, перезапустить браузер, reconnect. Device edits пережили restart и дождались ACK; PDFs/AI/native work не обещаны offline. |
| ☐ | N06 conflict — #19 | На втором независимом клиенте изменить то же поле, затем reconnect. Видны обе версии, решение явное; разные поля не конфликтуют. Не очищать outbox; ошибки protocol/storage не названы «синхронизировано». |
| ☐ | X02c — #19 | На физическом устройстве повторить disconnect/restart/storage-lifetime. Затем **отдельно** на уже развёрнутом owner-authenticated HTTPS gateway проверить owner / anonymous / non-owner / revoked. Не включать unauthenticated LAN и не обходить TLS. 390px localhost не является этим тестом. |

## H. Не перекладывать на обычного пользователя

Эти функции входят в техническую проверку/операторскую приёмку, но не требуют
изобретать UI или давать человеку SQL/инъекцию сетевых гонок. Их фактический
машинный статус — в [отчёте](README.md) и [results.json](results.json).

| ID | Отдельная проверка / связь |
|---|---|
| F04 — #12 | CLI `research-feed run`: bounded dates, `--shortlist-budget`, `--card-budget`, `--cached-only`, optional `--queue-zotero`. **Нет** флагов `--dry-run`/`--budget`. Default без queue не пишет Zotero, но может вызвать models/acquisition и публикует локальный bundle; cached-only тоже пишет новый report/state. |
| E01 — #27 | `telegram-auth`, collection, saved `--input-jsonl` processing и passive report reading — отдельные операции. Авторизация только владельцем, SDK/аккаунт условны; ML сейчас shadow, не принятый hard gate. |
| E02 — #47 | `goldenset analyze-feedback --project-root ROOT --cutoff AWARE_ISO`; manual/weekly, два test-clock цикла/restart, aliases/retractions/gaps/future leakage и passive replay. Никакой новой dashboard; reports не меняют labels/models. |
| E03 — #37/#38/#42 | `python -m tools.method_validation {support,priority,collections} --config FILE`; отдельно `--run`, затем `--inspect ATTEMPT`. Frozen/replay/native positive/refusal и held-out human/model/resource qualification — разные результаты. |
| E04 — #49 | `zotero-summarizer faithbench interpretation --project-root ROOT --run-id ID`: opt-in A/B/C, ordinary-default isolation, valid base при card-only failure, все assigned questions в denominator. Научную пользу не доказывает synthetic zero-error. |
| R05 — #40 | Retained-audit export — **внутренний** `_review_cache.export_review_audit` в initialized project; публичного UI/CLI/HTTP export нет. Exact byte/source binding ≠ scientific entailment. |
| Q05b / D01 — #7/#30/#46 | Held GET/Add ownership, request UUID/replay, MCP actual semantic confirmation, quote checkpoints/capabilities и malformed ACK — проверки разработчика. Человек проверяет видимость/копирование, не удерживает transport вручную. |
| D02 — PR-only native maintenance | Raw RSS read-state backup-before-effects и retention-after-success: owned native SQLite tests, не вмешательство в основную библиотеку. |
| D03 / X03 — #7/#22/#23 | Selected-root CLI до model imports, архитектура/ADR provenance, исследовательские решения. RepoDoc не принят; нет новой пользовательской graph-функции. |
| F06 — #160/#161 | Не implemented UI: #160 — research policy; #161 — унаследованные collection-tail/last-author/person-h-index/organisation gaps. Prefilter metadata PR не реализует эти требования. Их нельзя отметить PASS по этому чеклисту. |

## Лист результата

```text
Revision / OS / browser / origin / AI mode: __________________________
Prepared data keys + backup: ________________________________________
ID + subcheck: ______  PASS / FAIL / BLOCKED / N/A
Expected: __________________________________________________________
Observed + screenshot/log/reference: _______________________________
Side effects/native readback checked: ______________________________
Tester / date: _____________________________________________________
```

**Решение человека:** разрешённые основные сценарии без FAIL → можно продолжить
ограниченный UAT. BLOCKED не превращать в PASS; недоступные conditional workflows
не считать принятыми. Полная production/local/hardware/scientific/device-ready
готовность этим документом не заявляется.
