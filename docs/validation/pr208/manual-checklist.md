# PR208 — ручной чеклист добавленного функционала

```text
Добавленная функция → исходный GitHub issue → действие → результат
```

**Версия 2: только функционал, добавленный в PR208 относительно `main@bd5e267`.**
Не общий smoke приложения, не перечень исправленных багов и не весь backlog.
[Происхождение функций: issue → реализация](issue-scope.md) ·
[PR208](https://github.com/Vlislavn/zotero-summarizer/pull/208).
Проверяется фактически добавленная часть функции, а не закрытие всего issue.

**Человеком ещё не выполнен.** Предыдущие8агентов проверяли более широкий
чеклист версии1; их результаты не считаются проверкой этой новой версии.

## Подготовка и запись результата

- Записать commit, ОС, браузер и origin. Для записей использовать **отдельно
  выбранную копию** Zotero/PDF и backup; новый project root сам библиотеку не копирует.
- Для AI/сетевых функций нужны уже разрешённые routes, источники и assets;
  для Telegram — разрешённая сессия, для remote — настроенный HTTPS gateway.
  Без prerequisite ставить **BLOCKED**, а не PASS; новые keys/веса ради чеклиста не нужны.
- Сохранённые обзоры/отчёты/предложения должны существовать в тестовой копии.
  Если их нет, создать обычным workflow либо отметить соответствующий пункт BLOCKED.
- У каждого пункта записать **PASS / FAIL / BLOCKED / N/A**, фактический результат
  и ссылку на screenshot/вывод. Чекбоксы ниже пока не отмечены.

## A. Функции в приложении

| Проверено / ID | Добавленная функция и исходный GitHub issue | Как проверить вручную | Ожидаемый результат |
|---|---|---|---|
| ☐ FEATURE-01 | **HackerNoon как RSS-источник** — [#16](https://github.com/Vlislavn/zotero-summarizer/issues/16) | Settings → Sources: добавить предложенные HackerNoon feeds, отдельно Refresh, открыть полученные записи в Today. Отключить один feed и reload. | Подписки/состояние сохраняются; статьи поступают в обычный RSS workflow; Save подписки не запускает скрытый refresh. Полезность practitioner-triage проверять на самих статьях, не по успешному fetch. |
| ☐ FEATURE-02 | **Объяснение выбора статьи в Today** — [#33](https://github.com/Vlislavn/zotero-summarizer/issues/33) | В Today раскрыть **Why selected** у статьи с review и у статьи без подтверждённого review. | Видны причины и записанная политика выбора; relevance отделена от evidence, неизвестная оценка не названа плохим качеством. Порядок карточек раскрытием не меняется. |
| ☐ FEATURE-03 | **План чтения рядом с вердиктом** — [#9](https://github.com/Vlislavn/zotero-summarizer/issues/9) | Открыть сохранённый обзор с read/skip targets и временем в карточке и на `/paper/KEY?autoRun=false`, не открывая Details. | Рядом с READ/SKIM видны упорядоченные части для чтения, что пропустить и что добавляет оригинал. Для отсутствующего решения нет старого плана; неизвестное время не превращено в0. |
| ☐ FEATURE-04 | **Раздельные идея, доказательства и сложность изложения** — [#15](https://github.com/Vlislavn/zotero-summarizer/issues/15) | В обзоре раскрыть Idea/Evidence/Writing; сравнить полезную идею со сложным изложением и работу со слабыми доказательствами. | Scientific grade не подменяется красивым текстом или self-grade. При ограничении чтения до SKIM видны причина и конкретные targets; полезная идея остаётся доступной. |
| ☐ FEATURE-05 | **Автоматическое сохранение отправленных вопросов** — [#48](https://github.com/Vlislavn/zotero-summarizer/issues/48) | Из Today и Library отправить typed и preset question, в том числе на paper без PDF/при недоступном ответе. Reload; затем изменить/очистить manual note. Если разрешена native-запись, проверить My notes в копии Zotero после доставки. | Каждый вопрос остаётся отдельной записью My notes независимо от ответа; manual note его не стирает. Retry answer не создаёт новый вопрос, осознанная повторная отправка создаёт. Device/app save и доставка в Zotero различимы. |
| ☐ FEATURE-06 | **Related in my library** — [#46](https://github.com/Vlislavn/zotero-summarizer/issues/46) | На paper открыть **Show related saved papers**; затем отдельно **Try with comments**, сначала отменить, затем подтвердить разрешённую обработку. | До3других saved-paper families, включая уже прочитанные; собственная версия не рекомендована. Ссылки открываются; comments assessment требует явного согласия и показывает использованные excerpts. Metadata similarity не названа доказанным подтверждением/опровержением. |
| ☐ FEATURE-07 | **Find new relevant из сохранённой статьи** — [#46](https://github.com/Vlislavn/zotero-summarizer/issues/46) | На saved paper нажать **Find new relevant**, открыть Search session, раскрыть pool, reload. При разрешении явно Review/Add один результат. | Есть bounded внешний набор и до3рекомендаций; уже сохранённые работы не названы новыми, unknown membership обозначен. Private seed comments — отдельное согласие; Add не происходит автоматически. |
| ☐ FEATURE-08 | **Поиск ответа на вопрос в оригинале до выбора обзоров** — [#30](https://github.com/Vlislavn/zotero-summarizer/issues/30) | Targeted Search: тема + вопрос, включить **Experimental original-source question evidence**. Раскрыть результат/цитату и сопоставить с оригиналом, затем reload session. | Видны answer-evidence state, точный источник/locator и scope; checked sufficient evidence учитывается в Recommended. Partial/непроверенное не выдаётся за отсутствие ответа во всей статье; отрицательный научный результат тоже может отвечать. |
| ☐ FEATURE-09 | **Просмотр и скачивание сохранённых Weekly digests** — [#12](https://github.com/Vlislavn/zotero-summarizer/issues/12) | Открыть `/weekly`, выбрать saved run, раскрыть engineering card, скачать JSON/Markdown, открыть bookmark этого run. | Показывается выбранный сохранённый отчёт, downloads относятся к нему; pending review/неизвестное evidence явно обозначены. Просмотр/скачивание не генерируют новый обзор. |
| ☐ FEATURE-10 | **Чтение working set и редактирование offline** — [#19](https://github.com/Vlislavn/zotero-summarizer/issues/19) | При доступном backend завершить Sync now и подготовить offline shell. На том же origin/storage отключить собственный backend, открыть ещё не открывавшуюся synced paper, сохранить note/verdict, перезапустить браузер, reconnect. Отдельно создать конфликт тем же полем со второго клиента и выбрать версию. | Синхронизированный контекст доступен, изменения переживают restart и ждут ACK; после reconnect применяются, конфликт показывает обе версии. PDFs и новые AI-вызовы offline не обещаны. |
| ☐ FEATURE-11 | **Удалённый доступ через owner-authenticated HTTPS** — [#19](https://github.com/Vlislavn/zotero-summarizer/issues/19) | Только на уже согласованном gateway открыть приложение с другого устройства как owner; отдельно проверить anonymous/non-owner/revoked access. | Owner использует приложение/sync, остальные не получают доступ. Это отдельный тест реального TLS/device; эмуляция390px не заменяет его. Не включать unauthenticated LAN и не обходить TLS. |

## B. Добавленные CLI-функции

Это пользовательские операторские функции, а не дополнительные обязательные UI-шаги.
Пути/окно/config выбрать для тестового проекта; команды запуска могут вызвать AI
или сбор данных. Без подготовленного окружения — BLOCKED.

| Проверено / ID | Добавленная функция и исходный GitHub issue | Как проверить вручную | Ожидаемый результат |
|---|---|---|---|
| ☐ FEATURE-12 | **Source-qualified engineering cards из RSS для проектов** — [#12](https://github.com/Vlislavn/zotero-summarizer/issues/12) | Выполнить `zotero-summarizer research-feed run --project-root ROOT --from START --to END --shortlist-budget N --card-budget M`; прочитать JSON/Markdown и открыть saved run в Weekly. Без новой генерации — `--cached-only` по уже qualified inputs. | Карточки связывают статью с проектом, artifacts и reproduction blockers; неизвестное не превращено в измеренные сроки. Лимиты соблюдены, исходники доступны для проверки. Без `--queue-zotero` нет queued Zotero write, но локальный отчёт всё равно публикуется. |
| ☐ FEATURE-13 | **Telegram news: сбор и обработка сохранённых сообщений** — [#27](https://github.com/Vlislavn/zotero-summarizer/issues/27) | С разрешённой сессией использовать `research-feed telegram-auth`, затем `research-feed run --source telegram --collect-only --project-root ROOT`. Отдельно обработать разрешённый saved input через `--input-jsonl FILE`, открыть report. | Сохранены тексты/идентичности и ссылки на сообщения; collection и model processing разделены, повторный сбор не дублирует известные posts. Просмотр saved report не активирует Telegram. ML остаётся shadow, не принятым hard gate. |
| ☐ FEATURE-14 | **Анализ накопленных решений: manual и еженедельно** — [#47](https://github.com/Vlislavn/zotero-summarizer/issues/47) | На подготовленных labels выполнить `goldenset analyze-feedback --project-root ROOT --cutoff AWARE_ISO`, прочитать JSON/Markdown. Если используется weekly operation, включить её существующей конфигурацией и проверить опубликованный scheduled report после наступления срока. | Есть описательный отчёт, coverage/ограничения и traceable examples; insufficient data обозначена. Отчёт не меняет labels/models; manual и weekly используют ту же логику. Отсутствие unbiased-quality evidence не скрыто. |

## C. Добавленные экспериментальные инструменты — только при их использовании

Проверять на подготовленных frozen inputs, не на основной библиотеке.
Получение tool output не доказывает научную пользу или победу модели.

| Проверено / ID | Добавленная функция и исходный GitHub issue | Как проверить вручную | Ожидаемый результат |
|---|---|---|---|
| ☐ FEATURE-15 | **Сравнение criterion–evidence support** — [#37](https://github.com/Vlislavn/zotero-summarizer/issues/37) | `python -m tools.method_validation support --config FILE`; отдельно разрешить `--run`, затем `--inspect ATTEMPT`. | До запуска показаны admission/ограничения; после есть source-linked readouts, неизвестные/ошибки отдельно от negatives. Inspect не делает новый model run. |
| ☐ FEATURE-16 | **Сравнение reading priority** — [#38](https://github.com/Vlislavn/zotero-summarizer/issues/38) | Аналогично выполнить `python -m tools.method_validation priority --config FILE`, явно `--run` и `--inspect ATTEMPT`. | В отчёте сравниваются admitted frozen priorities с provenance/coverage; отсутствующие решения не выдуманы. Production ranking не меняется. |
| ☐ FEATURE-17 | **Сравнение распределения по существующим коллекциям** — [#42](https://github.com/Vlislavn/zotero-summarizer/issues/42) | Аналогично выполнить `python -m tools.method_validation collections --config FILE`, явно `--run` и `--inspect ATTEMPT`. | Результат относится к выбранной существующей taxonomy, допускает несколько memberships/unknown; новые имена и native membership не применяются автоматически. |
| ☐ FEATURE-18 | **Эксперимент с Finding / Scope / Unproven карточками** — [#49](https://github.com/Vlislavn/zotero-summarizer/issues/49) | На prepared study inputs явно выполнить `zotero-summarizer faithbench interpretation --project-root ROOT --run-id ID`; прочитать A/B/C packets и доступные qualified card/QA outputs. | Условие C содержит Finding / Scope / Unproven с исходным evidence; обычный review по умолчанию не получает experiment card. Ошибки и все assigned questions сохранены; вывод adopt/reject не объявлен без независимых человеческих оценок. |

## Лист результата

```text
Commit / окружение / выбранная копия: _______________________________
FEATURE-__: PASS / FAIL / BLOCKED / N/A
Действия и входные данные: _________________________________________
Ожидаемый / фактический результат: _________________________________
Screenshot / output / native readback: _____________________________
Проверил / дата: ___________________________________________________
```

Общие регрессии приложения, низкоуровневые race/replay/backup тесты, внутренний
export и нереализованные пожелания не являются пунктами этого чеклиста.
