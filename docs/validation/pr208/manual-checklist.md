# PR208 — финальный чеклист приёмки владельца (v4)

```text
Страница / команда → что сделать в реальном приложении → ожидаемый результат → PASS/FAIL + апрув вида
```

**Версия 4 (перед вливкой в `main`).** Охватывает **весь** пользовательский функционал
приложения, а не только 18 пунктов, добавленных PR (v3.3). Пункты v3.3 сохранены
с прежними issue-ссылками — см. [«Соответствие v3.3»](#соответствие-v33).
Источник списка: [docs/features.md](../../features.md), `CHANGELOG.md` [Unreleased], routes
`frontend/src/App.jsx`, `zotero-summarizer --help`, `make help`. Происхождение функций:
[issue-scope.md](issue-scope.md) · [PR208](https://github.com/Vlislavn/zotero-summarizer/pull/208).

**Человеком ещё не выполнен.** Автотесты и агентские прогоны не заменяют апрув владельца.

## Как пользоваться

Две отметки в первой колонке:

- ✍ — **пишет данные владельца** (Zotero, labels, модель, конфиг): только на **копии** Zotero/PDF и `data/`.
- 🔌 — нужен внешний prerequisite (Telegram-сессия, Tailscale, облачный ключ, локальная модель, интернет). Без него — **BLOCKED**, не PASS.

Колонки: **Результат** — `PASS` / `FAIL` / `BLOCKED` / `N/A` + ссылка на screenshot/вывод;
**Апрув** — владелец: «вид и понятность ок» (✅ / правки: …). Подписи UI даны в кавычках
как в приложении (английские; Telegram-сводки — на языке чата).

### Подготовка

- Записать commit, ОС, браузер, origin (`http://127.0.0.1:8000` или HTTPS gateway). Запуск: `make install`, затем `make run`.
- Для ✍-пунктов — отдельная копия Zotero/PDF + backup; проект с новым root библиотеку сам не копирует.
- RSS: `RssNamespaceWarning:` в **Last source issue** — предупреждение, не отказ refresh; не доказывает поступление статей.
  Настоящий отказ/пустой вход → URL/ошибку записать, зависимые пункты — BLOCKED.
- Нет сохранённого review/отчёта в копии — создать обычным workflow или BLOCKED.
- Автоматические ИИ-пункты проверять на разрешённых провайдерах; новые ключи/веса ради чеклиста не заводить.
- Общий вид везде: 390 px (iPhone) без горизонтального скролла, текст читаем, кнопки ≥44 px; нет сырых id/HTML/JSON.

---

## 1. Первый запуск и навигация

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ SETUP-01 ✍ | `/setup` (чистая копия `data/`) | Пройти мастер: цели, пути Zotero, режим ИИ (локальный / облачный / без ИИ). | Нет `goals.yaml`/`.env` → созданы; существующие не перезаписаны. Дальше — Today. Шаги понятны. | | |
| ☐ SETUP-02 | `/setup` повторно; `zotero-summarizer setup` | Открыть `/setup` на уже настроенном; затем `/library` в чистом проекте. | Вернувшегося пользователя не гонят в мастер; первый запуск с `/` или `/library` ведёт в `/setup`. | | |
| ☐ SETUP-03 🔌 | Ops → System → «Check readiness»; `zotero-summarizer doctor` | Нажать **Check readiness**; то же в терминале (`--json`). | Локальный режим «Ready» только после ответа модели и dry-run без записи; нехватка данных о памяти = «unknown», не угадано. | | |
| ☐ NAV-01 | верхнее меню | Пройти **Today · Library · Search · Weekly · Settings · Ops**; открыть `/`. | `/` ведёт на Today; активная вкладка подсвечена; меню переносится на 390 px. | | |
| ☐ NAV-02 | `/review`, `/triage`, `/pending`, `/annotate`, `/xyz` | Открыть старые адреса (в т.ч. с `?state=…`). | Ведут в Ops (вкладки) / Library «Select & label»; query сохранён; неизвестный адрес → Library. | | |

## 2. Today `/today`

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ TODAY-01 | Today | Открыть страницу с готовым shortlist. | Одна колонка карточек под «Today’s reading»; читаемые счётчики; без сырых feed-кодов; порядок по близости к тексту целей. | | |
| ☐ TODAY-02 ✍ | Today → «New shortlist» | Нажать «New shortlist». Затем «Refresh status». | «Selecting your shortlist…», по окончании новый список; при ошибке — «Selection failed — existing shortlist kept». | | |
| ☐ TODAY-03 | карточка | Раскрыть **more** у карточки. | Авторы/abstract/легенда + причины выбора и «Policy at selection»; порядок карточек не меняется. | | |
| ☐ TODAY-04 | карточка | Посмотреть AI-карточку (TL;DR, key result, New/Take/Caveat/Code). | ≤4 коротких пункта на английском, одна строка «почему» с целью; с отключённым ИИ/без согласия — только факты + причина. | | |
| ☐ TODAY-05 | карточка | Посмотреть полоску фактов (иконки) и **more**. | Только найденные факты (одна строка на iPhone); если нет ни одного — одна тихая строка; **more** перечисляет все проверенные. | | |
| ☐ TODAY-06 ✍ | карточка | Нажать **must / should / could / skip**; затем «Undo». У skip — «Reason (optional, one tap)» chips. | Решение записано и показано на карточке; Undo отменяет; chip причины сохраняется. Видны «Quality A», «k of n checks». | | |
| ☐ TODAY-07 ✍ | карточка/нижняя панель | Выбрать карточки («Select all»), нажать **Add to library** и **Trash**; выбрать коллекцию. | Add — только для статей с актуальным full-text review (иначе «…skipped by Add — Trash still works»); Trash работает всегда; двойной Add не дублирует. | | |
| ☐ TODAY-08 | карточка | Найти карточку «Random check». | Помечена «Random check» (калибровка, не рекомендация); ≤2 в день. | | |
| ☐ TODAY-09 | низ страницы | Раскрыть **Not reviewed yet (N)**; открыть одну. | Лучшие по рангу без review, без кнопок вердикта; открытие запускает review на странице статьи. | | |
| ☐ TODAY-10 ✍ | карточка | Найти карточку с «Outdated review» → **Regenerate**. | Бейдж «Outdated review · regenerating…»; после — актуальный review; при выключенном ИИ — «AI not connected (Settings)». | | |
| ☐ TODAY-11 | меню «Today task settings» | Открыть **Article selection**, **RSS sources**, **Telegram today**. | Каждая ведёт в свою задачу (`?task=selection`/`rss`, Weekly). Неверный `?task=` → «Today task not found» + «Back to Today». | | |
| ☐ TODAY-12 | Today (пустой/ошибка) | Пустой shortlist; недоступный статус. | «No papers in today’s shortlist yet.» + пути «New shortlist» / «Triage backlog»; ошибки читаемые, не молчаливые. | | |
| ☐ TODAY-13 ✍ | Today → «Triage backlog» | Запустить; смотреть статус «Checking triage status…». | Идёт прогресс; можно оставить страницу; Ops → Triage jobs видит job. | | |
| ☐ TODAY-14 | `data/today-exposure/` | После просмотра открыть файл дня. | Локальный `.jsonl` с показанными статьями/временем/ссылками; `ZS_TODAY_EXPOSURE_LOG=0` отключает. | | |

## 3. Страница статьи `/paper/:key`

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ PAPER-01 | `/paper/KEY` | Открыть saved paper на 390 px. | Verdict-карточка на первом экране: вердикт, why, read first, code row; авторы свёрнуты «+N more»; без sticky-перекрытий. | | |
| ☐ PAPER-02 | карточка | Проверить полосу фактов под вердиктом. | Факты (full text, свой код, публикация, группа) без пустых кружков. | | |
| ☐ PAPER-03 | review | Открыть review с планом (v3.3 FEATURE-03, #9). | Рядом с READ/SKIM: «Read first», «Skip», «What the original adds»; нет плана для отсутствующего решения; время «Time not estimated», не 0. | | |
| ☐ PAPER-04 | review | Раскрыть «Idea, evidence and writing» (FEATURE-04, #15). | Идея/доказательства/изложение раздельно; при SKIM — «Why SKIM» и конкретные targets. | | |
| ☐ PAPER-05 | review | Раскрыть **Evidence** fold. | Дословные цитаты по полям (Summary, Findings, Methods…); «Show full passage»; «Quotes not saved — re-run the review» вместо пустоты. | | |
| ☐ PAPER-06 | review | «Red flags», «Possible overstatement», goal board, «Details», «On this page». | Информируют, но не меняют вердикт; тип статьи (в т.ч. experience report; «type uncertain» — общий чеклист) виден; секции сворачиваются. | | |
| ☐ PAPER-07 | review | Вердикт: **Decide** / **Save verdict** / «Yours:» / **Delete**; sticky-панель на телефоне. | Своё решение сохраняется, показано «Yours:», «Auto:» отдельно; Confirm/Override на предложенном вердикте. | | |
| ☐ PAPER-08 ✍ | review → «Ask this paper» | Ответить на typed и preset вопрос (напр. «Is the code released?»), reload; изменить **My notes** (FEATURE-05, #48). | Ответ с цитатой или отказ; вопрос сохранён отдельной записью («Saved question»), manual note его не стирает; различимы «Saved on this device» / «Saved in the app» / «Saved to the app and Zotero». | | |
| ☐ PAPER-09 | «Answer mode» | Переключить «Fast retrieval» / «Full context» / «Full text». | Режимы, читающие PDF, требуют PDF; без PDF — понятное сообщение. | | |
| ☐ PAPER-10 | «Find similar papers» → «Show related saved papers» (FEATURE-06, #46) | Открыть **Related in my library**; затем «Try with comments» (отменить, потом подтвердить). | До 3 других saved-семейств; сама статья и отклонённые исключены; comments — только после явного согласия; без него «Metadata evidence». | | |
| ☐ PAPER-11 | «Find similar papers» → «Find new relevant» (FEATURE-07, #46) | Нажать, раскрыть pool, reload. | Внешний набор ≤3 рекомендаций; уже сохранённые не названы новыми; **ничего не добавляется** само. Работает и для feed-only статьи. | | |
| ☐ PAPER-12 ✍ | review | Нажать **Regenerate** / «Run deeper review» / «Re-run deeper review». | Прогресс; провал сохраняет прошлый валидный review и показывает реальную причину; зависание не опрашивается вечно. | | |
| ☐ PAPER-13 | review | Статья с «Outdated review». | Остаётся рекомендованной, видны вердикт и Add для того же PDF; «Older review — re-run for the new digest.», не пустая панель. | | |
| ☐ PAPER-14 | вкладка «Figures» | Открыть Figures; stale-статья. | Figures и Tables отдельно; stale → «Rebuilding figures…»/«Rebuild»; без битых картинок. | | |
| ☐ PAPER-15 ✍ | paper → тег/коллекция | «Add to collection…», редактор тегов. | Запись в Zotero с backup; видна в копии Zotero. | | |
| ☐ PAPER-16 | «Open full review ↗» | Из Library открыть в новой вкладке. | Открывается `/paper/KEY`; назад возвращает в список. | | |

## 4. Library `/library`

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ LIB-01 | Library → **Read next** | Открыть очередь; сортировка «Sort by» (Best match…), направление. | Очередь с рангом, счётчики читаемые; нет сырого HTML; пояснения свёрнуты. | | |
| ☐ LIB-02 | фильтры | «Journal», «Matches my goals», «Prestige», «Quality»; «Browse & filter», Collections, Top Tags. | Список фильтруется; сброс возвращает всё. | | |
| ☐ LIB-03 | строка | Раскрыть строку: digest, план чтения, figures, предложенный вердикт. | Как на странице статьи; «Flagged»/«Low confidence» видны; Confirm/Override сохраняются. | | |
| ☐ LIB-04 ✍ | Library → **Select & label** (`?mode=batch`) | «Select» нескольких → bulk verdict → «Marked read». | Метки проставлены, история хранится; ошибки — на уровне статьи. | | |
| ☐ LIB-05 | Predictions / top picks | Открыть top picks; «All reviewed». | Незакрытые top picks; если всех решено — «No undecided top picks.» | | |
| ☐ LIB-06 ✍ | «Fetch full text» | Нажать; наблюдать «Fetching full text…». | Идёт, не блокирует; PDF из OA (UA, bioRxiv `.full.pdf`) или браузерной ступени; paywall/Cloudflare не ломает; «No PDF» помечено. | | |
| ☐ LIB-07 ✍ | меню «Zotero ▾» / «Sync all → Zotero» | Запустить на копии. | Шаги «2/3 — writing relevance tags», «3/3 — stamping ranks»; при открытом Zotero — «Sync cancelled — close Zotero, then retry.» | | |
| ☐ LIB-08 ✍ | «Rescore» | Нажать на выбранных. | «Scoring…», затем обновлённые оценки; ранжирование не ломается. | | |
| ☐ LIB-09 | «Ask this paper» в строке | Вопрос из Library (FEATURE-05). | Как PAPER-08; без PDF — ответ с отказом, вопрос сохранён. | | |

## 5. Search `/search`

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ SRCH-01 🔌 | Search | Тема → **Search**; прогресс «Searching…». | Дедуплицированные кандидаты (arXiv/Europe PMC/OpenAlex/Crossref/Semantic Scholar), ссылки настоящие, не угаданные по названию. | | |
| ☐ SRCH-02 | «Explicit constraints (optional)» | «Required/Excluded exact phrases», «Only publication types». | Ограничения подтверждаются владельцем; членство в библиотеке неизвестно = «Membership unknown — not established new.» | | |
| ☐ SRCH-03 🔌 | «Agentic refinement» | Включить; «Refining the search…», «Deep-reviewing the top papers…». | Видны шаги; результат воспроизводим; reload сессии сохраняет. | | |
| ☐ SRCH-04 🔌 | Targeted Search (FEATURE-08, #30) | Тема + вопрос + «Experimental original-source question evidence». | Состояние answer-evidence, точный источник/locator; partial ≠ «нет ответа»; в Recommended — только проверенное. | | |
| ☐ SRCH-05 ✍ | результат → «Add to library» / «Add to:» | Добавить одну статью в коллекцию на копии. | Add только по явному клику; backup; после неопределённого Add — «Reconcile retained Add», без дубля. | | |
| ☐ SRCH-06 | сессия | Открыть сессию, раскрыть «Metadata evidence». | Диагностика провайдеров различает «пусто» и «ошибка» («Provider retrieval failed.»), не говорит о полноте охвата. | | |
| ☐ SRCH-07 🔌 | comment assessment | Ошибка/отключение («Comment assessment is disabled.»). | Понятный отказ; без согласия приватные comments не уходят. | | |

## 6. Weekly и Telegram `/weekly`

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ WEEK-01 | Weekly | Открыть без сохранённых отчётов и с ними. | Пустое состояние понятно; «Refresh saved runs» перечитывает; Weekly не генерирует сам. | | |
| ☐ WEEK-02 | «Choose a saved run» (FEATURE-09, #12) | Выбрать run, раскрыть карточку, «Download canonical JSON» / «Download Markdown». | Показан именно этот run; файлы совпадают с сохранённым; «Needs a current review / full text» явно. | | |
| ☐ WEEK-03 | карточка digest | Раскрыть «Project use», «Why chosen», «Evidence gaps», «Missing reproduction inputs». | Совпадения с проектом — непроверенные заметки; неизвестное не превращено в сроки. | | |
| ☐ WEEK-04 🔌 | Weekly → «Telegram sources» (`?task=telegram`) | «Add Telegram source», «Save Telegram sources»; выбрать локального провайдера. | Каналы/группы/топики сохраняются; без модели — режим «No summary»; до ~30 каналов. | | |
| ☐ WEEK-05 🔌 | «Allow & check channels» | Один раз разрешить («Allowed.»), затем «Check channels». «Revoke permission». | Фоновая задача с этапами; новые посты появляются по окончании; согласие отзывается. | | |
| ☐ WEEK-06 🔌 | страница сессии | «Copy command» → `research-feed telegram-auth` в терминале. | Команда копируется («Copied»); приложение не вступает в чаты и не пишет. | | |
| ☐ WEEK-07 🔌 | «Trends» | Посмотреть топ-5; «Show all N trends» / «Show fewer trends». | Метка, gist, идеи/инструменты, маркер new/growing, ссылки на посты. | | |
| ☐ WEEK-08 🔌 | карточки постов | Раскрыть строки обсуждения и **Disagreement**; «Details», «Continues …». | Строка цитирует точные комментарии; «Disagreement» только если найден; комментарии грузятся по тапу. | | |
| ☐ WEEK-09 🔌 ✍ | пост | **Done**, «Undo», «Mark all read». | Прочитанное сворачивается в «n read»; Undo возвращает. | | |
| ☐ WEEK-10 🔌 ✍ | «Your feedback» | Chips «Useful / New to me / Want to try / Good discussion», «Not a fit» + причина, заметка. | Сохраняется; «Not a fit» хранится в базе; «Export feedback» скачивает. | | |
| ☐ WEEK-11 🔌 | «Past digests» | Открыть дату архива. | Те же карточки; сначала непрочитанные; с lead-темой. | | |
| ☐ WEEK-12 🔌 | ошибки/лимиты | Лимит Telegram, провал модели. | Посты без комментариев сохранены; канал не отброшен; ошибки читаемы; облачный провайдер — только после явного разрешения. | | |

## 7. RSS и backfill (Today → «RSS sources»)

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ RSS-01 ✍ | `/today?task=rss` | «Add subscription», «Save subscription»; Edit/Enabled; удалить. | Состояние сохраняется; Save **не** запускает refresh («Feed updated. Nothing fetched.»). | | |
| ☐ RSS-02 ✍ | «Import feeds from Zotero» | Импортировать подписки (копия). | Список появился; дубли не создаются. | | |
| ☐ RSS-03 🔌 | «Suggested engineering feeds» (FEATURE-01, #16) | Добавить «HackerNoon AI Agents»/«Agentic AI»; **Refresh RSS now**; затем «Triage backlog». | Статьи в обычном workflow; пустой upstream — отдельное состояние, не PASS за triage. | | |
| ☐ RSS-04 🔌 | «Refresh RSS now» | Один неисправный feed. | Различаются «nothing new», «skipped while offline», «source failed»; `RssNamespaceWarning:` — предупреждение, не отказ. | | |
| ☐ RSS-05 🔌 | refresh после паузы | Feed не проверялся несколько дней (arXiv). | Догоняет до watermark; «Incomplete fetch», если обрезан; ничего не теряется. | | |
| ☐ RSS-06 ✍ | пометка прочитанным в Zotero | Mark read на копии. | Backup перед записью; старые backups чистятся (5) только после успеха. | | |

## 8. Settings `/settings`

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ SETT-01 | Settings | Открыть обзор. | Сводка: «AI-assisted features», «Model for each step», «Review depth», «Checklist engine», «Background processing», «Zotero & PDFs», «Library access», «Output language»; «Change» ведут в задачу. | | |
| ☐ SETT-02 | полоса готовности | «Needs attention». | Только открытые проблемы; каждая причина и адрес показаны один раз. | | |
| ☐ SETT-03 ✍ 🔌 | «Choose models» (AI models) | «Add provider» → API address/protocol/Authorization → «Connect provider» / «Load models». | Явное разрешение именно этому адресу; ключ из env/keyring не показан; видна таблица **impact** до «Use as default»/«Use for selected tasks». | | |
| ☐ SETT-04 🔌 | AI models | «Test saved model», «Test selected tasks». | Один короткий prompt; ответ или конкретная ошибка (HTTP/timeout/empty/«unknown»); для локальной модели сначала память: `memory_insufficient`/`swap_nearly_full`/`model_size_unknown` с цифрами; без повторов и подмены. | | |
| ☐ SETT-05 | «Model for each step» | Таблица шагов. | Модель, медиана времени и статус по шагу: «Provider reachable», «Not reachable», «Provider needs approval». | | |
| ☐ SETT-06 ✍ | «Change AI mode» | Вкл/выкл AI-assisted features. | Выкл → «ML feed ranking remains available»; Today/карточки показывают «Enable AI». | | |
| ☐ SETT-07 ✍ | Review depth (`?task=depth`) | Выбрать **Thorough**/**Fast**; Save. | Применяется к следующему review; review другой глубины — «Outdated». | | |
| ☐ SETT-08 ✍ 🔌 | Checklist engine (`?task=engine`) | **Off / Local / Cloud**; «Access key»/«Replace access key»; **Test**. | Off по умолчанию; Cloud предупреждает об отправке текста; Test показывает результат; ключ не выводится. | | |
| ☐ SETT-09 ✍ | Background processing (`?task=processing`) | Изменить «Papers reviewed at once», «Calls per paper», «Feed catch-up days», «Pages per feed»; вернуть к default. | Значения по умолчанию (8/2/7/10) не пишутся в `goals.yaml`; изменённые сохраняются. | | |
| ☐ SETT-10 ✍ | Library access (`?task=university`) | «Change library access»: вкл/выкл browser fetch. | «Browser fetch enabled · sign-in is separate» / «off»; OA-поиск не меняется. | | |
| ☐ SETT-11 ✍ | Zotero & PDFs (`?task=zotero`) | «Change Zotero paths». | Пути сохраняются; применяются **после перезапуска**. | | |
| ☐ SETT-12 ✍ | Output language (`?task=language`) | Сменить язык. | Сохранён; следующие выходы на нём. | | |
| ☐ SETT-13 | Today «Article selection» (`/today?task=selection`) | Открыть и сохранить на копии. | Сохраняет только свои поля; уход с несохранённым спрашивает. | | |
| ☐ SETT-14 | несохранённые изменения | Уйти с несохранённой задачи; неопределённый ответ сохранения. | Вопрос подтверждения; уход не отменяет уже отправленное; неопределённый save → перечитывание, не автоповтор. | | |
| ☐ SETT-15 | неверный `?task=` | `/settings?task=bad`. | «Settings task not found» + «Back to Settings»; ничего не изменено. | | |

## 9. Ops `/ops`

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ OPS-01 | Ops → **Feed review** | Открыть; «Gate-rejected pile»; relabel. | Список с h-index/venue; можно переразметить; без Re-label Audit страницы. | | |
| ☐ OPS-02 ✍ | Feed review | «Apply approved → Zotero» на копии. | Запись с backup; ошибки по строкам. | | |
| ☐ OPS-03 | Ops → **Triage jobs** | Открыть; «Recent Jobs», фильтр «Last 7 days». | Статусы job, «Agreement», «No errors.»; Cancel активного job. | | |
| ☐ OPS-04 ✍ | Triage feedback | «Approve»/«Reject» по завершённой статье. | Сохраняется («Approved by you»). | | |
| ☐ OPS-05 | Ops → **Pending changes** | Открыть список; клавиатурная навигация. | Pending видны; фильтр статуса; «Select all»/«Clear». | | |
| ☐ OPS-06 ✍ | Pending | Apply выбранных; Reject; «Retrying failed change…». | Apply с backup; при запущенном Zotero «Apply cancelled — Zotero is running.»; Reject — одна транзакция; stale selection отклонён целиком. | | |
| ☐ OPS-07 | Ops → **System** | «Check readiness», «Calibrate reviews» (`?task=`). | Страницы-задачи с «Back to System»; без запуска, пока не нажато. | | |
| ☐ OPS-08 ✍ 🔌 | «Calibrate reviews» → «Start review calibration» | Запустить. | «Calibrating… measuring endpoint»; незавершённый run блокирует новый; не обещает длину вывода. | | |
| ☐ OPS-09 🔌 | «Check readiness» → «Verify setup» | Запустить; «Stop verification». | Чек-лист «Setup checks», «Strict offline», «Local inference»; «unknown», не догадка. | | |
| ☐ OPS-10 | **Your decisions** | «Run now» (и переключатель weekly). | Отчёт: что изменилось, согласие с предсказаниями, диапазоны, пробелы данных; read-only. | | |
| ☐ OPS-11 ✍ | Model card / «Retrain model» | «Refresh»; «Retrain model» → «Replace model and retrain». | Диалог «Retrain the feed gate?» с Replaces/Takes; обучение «Training…»; не стартует при неизвестной конфигурации. | | |

## 10. Офлайн / PWA и удалённый доступ

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ OFF-01 | значок статуса | «Connected»/«Offline»/«Server unreachable»/«Connection not confirmed»; «Details». | Статус честный; при недоступном сервере кэш показывается. | | |
| ☐ OFF-02 ✍ | «Sync now» → offline (FEATURE-10, #19) | Sync now; отключить backend; открыть новую synced paper; заметка/вердикт; перезапуск браузера; reconnect. | Контекст доступен; очередь переживает restart и ждёт ACK; PDF и новые AI-вызовы offline не обещаны. | | |
| ☐ OFF-03 ✍ | «Sync conflict for …» | Конфликт с другого клиента; «Keep mine» / «Use server» / «Open paper». | «Changed elsewhere — compare before resolving»; обе версии видны. | | |
| ☐ OFF-04 🔌 | `make iphone` (FEATURE-11, #19) | Открыть `https://<mac>.<tailnet>.ts.net` с iPhone; затем чужой/анонимный. | Owner открывает и синхронизирует; остальные — нет; Ctrl+C снимает mapping; `make run` отклоняет tailnet-адрес. | | |
| ☐ OFF-05 🔌 | PWA на iPhone | «На экран Домой», открыть. | Запускается как приложение; верстка 390 px; offline Generate отключён. | | |
| ☐ OFF-06 | `zotero-summarizer prefetch-models [--check]` | `--check` без сети. | Список кэша; strict offline отказывает на некэшированном. | | |

## 11. MCP-инструменты

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ MCP-01 | `zotero-summarizer mcp` | Подключить клиент; вызвать `get_library_status`, `search_papers`, `get_paper`, `find_similar_papers`. | Данные из запущенного приложения; для similar нужен непустой запрос/seed; очередь чтения не выдаётся за рекомендации. | | |
| ☐ MCP-02 ✍ | `start_triage`, `get_job_status`, `cancel_job`, `submit_feedback` | На копии. | Работают по правилам согласия; контент статьи не считается разрешением. | | |
| ☐ MCP-03 ✍ | `list_pending_changes`, `apply_pending_changes`, `manage_tags`, `manage_collections`, `set_reading_priority` | На копии. | Применение только с явным подтверждением, backup. | | |

## 12. CLI и `make`

| ID | Команда | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ CLI-01 | `make help`, `make` | Выполнить. | Bare `make` = help; перечислены install/run/iphone/dev/test/lint/check. | | |
| ☐ CLI-02 | `make install`, `make run` | На чистом клоне. | Зависимости, UI собран/пересобран, Today на `127.0.0.1:8000`. | | |
| ☐ CLI-03 | `zotero-summarizer --help` | Выполнить. | Есть serve, serve-mobile, mcp, migrate, setup, doctor, calibrate, feeds, goldenset, faithbench, research-feed; удалённых `make api/ui` нет. | | |
| ☐ CLI-04 | `zotero-summarizer migrate`; `smoke-test` | Выполнить на копии. | Миграции без потери; smoke OK. | | |
| ☐ CLI-05 🔌 | `zotero-summarizer feeds list` / `preview` / `run` | Preview (read-only, без LLM), затем run на копии. | Список/preview без записи; run паркует в `awaiting_review`. | | |
| ☐ CLI-06 🔌 | `research-feed run … --from --to --shortlist-budget --card-budget` (FEATURE-12, #12) | Запустить; `--cached-only`; `research-feed view`. | JSON+Markdown, карточки связаны с проектом; лимиты соблюдены; без `--queue-zotero` Zotero не пишется. | | |
| ☐ CLI-07 🔌 | `research-feed telegram-auth` (FEATURE-13, #27) | Авторизоваться один раз. | Сессия сохранена; **сбор — только из UI**; `run --source telegram` удалён (см. WEEK-05). | | |
| ☐ CLI-08 | `goldenset analyze-feedback --project-root ROOT --cutoff ISO` (FEATURE-14, #47) | Запустить; weekly по конфигу. | Описательный отчёт, ограничения, примеры; labels/модель не меняются. | | |
| ☐ CLI-09 ✍ | `goldenset model-history`, `restore-model` | Список, затем restore на копии. | Снимки перечислены; текущая модель сохраняется; нужен restart daemon. | | |
| ☐ CLI-10 ✍ | `goldenset export`, `train-classifier` | На копии. | Экспорт CSV/JSONL; обучение не пишет в golden CSV. | | |
| ☐ CLI-11 🔌 | `zotero-summarizer doctor [--json --fix]` | Выполнить. | Чек-лист; `--fix` — только безопасные исправления. | | |
| ☐ CLI-12 🔌 | `zotero-summarizer calibrate --papers 3` | Запустить. | Подбор технических default под вашу среду. | | |
| ☐ CLI-13 | `zotero-summarizer serve` / `feeds serve` | Запустить на свободном порту (не `:8000` релиза). | Ошибка bind сообщается, чужой процесс не убивается. | | |
| ☐ CLI-14 | `make lint`, `make test`, `make check` | Выполнить. | Зелёные (кроме известного macOS fork crash — сравнить с baseline). | | |

## 13. Запись в Zotero

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ ZOT-01 ✍ | любая запись | Add/тег/коллекция/Apply на копии. | Перед записью backup базы Zotero; результат виден в копии Zotero. | | |
| ☐ ZOT-02 ✍ | Zotero note | Открыть review-note в копии. | Заметка несёт весь review: digest, rubric с цитатами, «k of n checks», red flags, type, goal board, code link, section lines. | | |
| ☐ ZOT-03 ✍ | вердикт → note | Поставить вердикт. | Вердикт и rationale зеркалятся заметкой; не считаются одобрением Pending. | | |
| ☐ ZOT-04 | неопределённая запись | Прервать запись. | Показан «unconfirmed», без авто-повтора. | | |

## 14. Экспериментальные инструменты (только при использовании)

Только на frozen inputs, не на основной библиотеке. Вывод инструмента ≠ научная польза.

> Примеры EXP-01…04 — **синтетические** (без данных владельца и вызовов моделей): `python -m tools.method_validation.examples OUT_DIR` пишет их в пустую `OUT_DIR`; они проверяют проводку CLI, а не качество методов. Ошибки путей/конфигов — одна строка и код 2.

| ID | Команда | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ EXP-01 🔌 | `python -m tools.method_validation support --config FILE` (FEATURE-15, #37) | Синтетический пример: `python -m tools.method_validation.examples /tmp/exp` печатает готовые команды. Без `--run` (preview, 0 вызовов); затем `--run` (replay записанных ответов, пишет только в `/tmp/exp/support/selected`), `--inspect <каталог из «Private artifacts:»>`. Свои данные — свой frozen config. | Ограничения до запуска; после — source-linked readouts; inspect не запускает модель. | | |
| ☐ EXP-02 🔌 | `… priority --config FILE` (FEATURE-16, #38) | То же на `/tmp/exp/priority/config.json` (см. EXP-01). | Сравнение admitted frozen priorities; production ranking не меняется. | | |
| ☐ EXP-03 🔌 | `… collections --config FILE` (FEATURE-17, #42) | То же на `/tmp/exp/collections/config.json` (см. EXP-01). | Только существующие коллекции; новые имена/membership не применяются. | | |
| ☐ EXP-04 🔌 | `zotero-summarizer faithbench interpretation --project-root ROOT --recipe FILE` (FEATURE-18, #49) | Inspect без моделей на синтетическом рецепте: `ZOTERO_SUMMARIZER_HOME=/tmp/exp/faithbench/home zotero-summarizer faithbench interpretation --recipe /tmp/exp/faithbench/home/data/faithbench/recipe.json` → `INSPECTED_NOT_EXECUTED`; `--run --capture-local` — отдельно (нужны провайдер и ваш рецепт); `--run-id ID`. Без рецепта — подсказка с кодом 2, не traceback. | Условие C: Finding / Scope / Unproven с evidence; обычный review карточку не получает; вывод adopt/reject без оценок людей не объявлен. | | |

---

## Соответствие v3.3

| v3.3 | v4 ID | Issue | | v3.3 | v4 ID | Issue |
|---|---|---|---|---|---|---|
| FEATURE-01 | RSS-03 | [#16](https://github.com/Vlislavn/zotero-summarizer/issues/16) | | FEATURE-10 | OFF-02, OFF-03 | [#19](https://github.com/Vlislavn/zotero-summarizer/issues/19) |
| FEATURE-02 | TODAY-03 | [#33](https://github.com/Vlislavn/zotero-summarizer/issues/33) | | FEATURE-11 | OFF-04 | [#19](https://github.com/Vlislavn/zotero-summarizer/issues/19) |
| FEATURE-03 | PAPER-03 | [#9](https://github.com/Vlislavn/zotero-summarizer/issues/9) | | FEATURE-12 | CLI-06 | [#12](https://github.com/Vlislavn/zotero-summarizer/issues/12) |
| FEATURE-04 | PAPER-04 | [#15](https://github.com/Vlislavn/zotero-summarizer/issues/15) | | FEATURE-13 | CLI-07, WEEK-05 | [#27](https://github.com/Vlislavn/zotero-summarizer/issues/27) |
| FEATURE-05 | PAPER-08, LIB-09 | [#48](https://github.com/Vlislavn/zotero-summarizer/issues/48) | | FEATURE-14 | CLI-08 | [#47](https://github.com/Vlislavn/zotero-summarizer/issues/47) |
| FEATURE-06 | PAPER-10 | [#46](https://github.com/Vlislavn/zotero-summarizer/issues/46) | | FEATURE-15 | EXP-01 | [#37](https://github.com/Vlislavn/zotero-summarizer/issues/37) |
| FEATURE-07 | PAPER-11 | [#46](https://github.com/Vlislavn/zotero-summarizer/issues/46) | | FEATURE-16 | EXP-02 | [#38](https://github.com/Vlislavn/zotero-summarizer/issues/38) |
| FEATURE-08 | SRCH-04 | [#30](https://github.com/Vlislavn/zotero-summarizer/issues/30) | | FEATURE-17 | EXP-03 | [#42](https://github.com/Vlislavn/zotero-summarizer/issues/42) |
| FEATURE-09 | WEEK-02 | [#12](https://github.com/Vlislavn/zotero-summarizer/issues/12) | | FEATURE-18 | EXP-04 | [#49](https://github.com/Vlislavn/zotero-summarizer/issues/49) |

Изменения относительно v3.3: «Why selected» теперь раскрывается кнопкой **more** (TODAY-03); сбор Telegram
через `research-feed run --source telegram --collect-only` удалён — сбор идёт только из UI
(WEEK-05), в терминале остался `telegram-auth` (CLI-07). Остальные v3.3 пункты не менялись.

## Лист результата

```text
Commit / окружение / копия: ________________________________________
ID: PASS / FAIL / BLOCKED / N/A
Действия и входные данные: _________________________________________
Ожидаемый / фактический результат: _________________________________
Screenshot / output / readback: ____________________________________
Апрув вида и понятности (владелец): ✅ / правки: ____________________
Проверил / дата: ___________________________________________________
```

Низкоуровневые race/replay/backup тесты и нереализованные пожелания — вне чеклиста.
