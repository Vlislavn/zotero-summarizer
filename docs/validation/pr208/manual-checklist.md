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

## Результаты агентского прогона (2026-11-11)

**Апрув владельца ещё не поставлен:** колонка «Апрув» пуста, агентский прогон её не заменяет.
В колонке «Результат» — итоговый результат прогона по каждому ID (позднее перепроверка заменяет раннюю)
и путь к evidence; пути `R3/…` и `fixes-w…/…` — относительно `data/perf-20261107/acceptance/`.

- **Сборка:** commit `77ea225` + более поздние исправления (w6: стабильный fingerprint «Find new relevant», «Reviewed N of 10» и текст источников в Search). Часть исправлений w2–w5 проверена в приложении (пометка «исправлено (wN), перепроверено»), остальные — только тестами («PASS (исправление проверено тестами)»).
- **Окружение:** acceptance-клон домашнего каталога владельца (`cp -c`), keyring в памяти (ключи владельца не затронуты), реальные ИИ-маршруты владельца (kather/sota и локальная qwen3:8b); живой сервер `:8000` не использовался для записи.
- **Итого 126 пунктов:** PASS — 118, FAIL — 1, BLOCKED-owner — 5, BLOCKED-external — 2, NOT-RUN — 0.
  - FAIL: WEEK-03 (косметика: сырой `dataset_provenance` под «Reproducibility: blocked»).
  - BLOCKED-owner: LIB-06, WEEK-05, OFF-04, CLI-07, ZOT-04.
  - BLOCKED-external: PAPER-11 (OpenAlex 429), WEEK-12 (лимит Telegram не вызвать).
  - «PASS (частично…)» означает, что часть шагов пункта не прогонялась — причина в той же ячейке.

## Что остаётся владельцу

**Не проверено без вас (BLOCKED-owner):**

- Настоящий iPhone и TLS: OFF-04 (`make iphone`, открыть адрес tailnet с телефона и с чужого устройства), устройственная часть OFF-05 («Add to Home Screen»).
- Вход в Telegram из терминала (CLI-07, `research-feed telegram-auth`) и фоновый сбор после него (WEEK-05).
- LIB-06: массовое «Fetch full text» не запускалось — оно скачало бы тысячи PDF; проверена только защита «Zotero appears to be running…».
- ZOT-04: прерывание записи в Zotero на лету без остановки сервера не воспроизвести.

**Один клик на живом сервере.** Если в Settings → AI models показан баннер, нажмите «Approve this connection»:
одобрение истекло после правок релизного конфига. Исправление сохраняет одобрения при несвязанных сохранениях,
так что дальше такое не повторится.

**Известные ограничения:**

- У сохранённых подключений нет кнопки удаления.
- Повторное «Connect provider» на тот же адрес создаёт дубль подключения.
- Счётчик «updated» при RSS-refresh у «живых» лент не падает до 0 (ленты меняются между запусками).
- Автообзор в Search занимает минуты, когда подготовка Today использует ту же модель.
- Сразу после вердикта на карточке Today ≈1.5 с мелькает «Updating…».
- Под «Reproducibility: blocked» в Weekly остался сырой `dataset_provenance`.
- Во время прогонов у Mac была нехватка памяти и swap (до 24 из 25 GiB) и высокая загрузка CPU: длинные ИИ-операции (калибровка, review, триаж) шли десятки минут, локальная модель иногда не проходила проверку памяти.

**Как повторить проверки** (подробности — `data/perf-20261107/acceptance/QA-BRIEF.md`):

1. Клонировать домашний каталог приложения и копию Zotero командой `cp -c` (APFS clone) в `/tmp`, не трогая оригинал.
2. Запустить сервер на отдельном порту (не `:8000`) с этим домом и модулем keyring, хранящим ключи только в памяти (`PYTHON_KEYRING_BACKEND` на «null/in-memory» backend).
3. Отдавать пропатченный `dist` фронтенда из `/tmp` (сборка из текущего дерева).
4. В Settings → AI models нажать «Approve this connection», затем идти по таблицам этого файла.
5. Браузер — Playwright, вьюпорты 390×844 и 1440×900; скриншоты и выводы складывать в `data/perf-20261107/acceptance/R3/<срез>/`.
6. Команды CLI запускать с тем же `ZOTERO_SUMMARIZER_HOME` и keyring в памяти; живой `:8000` — только чтение.

## Как пользоваться

Две отметки в первой колонке:

- ✍ — **пишет данные владельца** (Zotero, labels, модель, конфиг): только на **копии** Zotero/PDF и `data/`.
- 🔌 — нужен внешний prerequisite (Telegram-сессия, Tailscale, облачный ключ, локальная модель, интернет). Без него — **BLOCKED**, не PASS.

Колонки: **Результат** — `PASS` / `FAIL` / `BLOCKED-owner` / `BLOCKED-external` / `NOT-RUN` + ссылка на screenshot/вывод;
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
| ☐ SETUP-01 ✍ | `/setup` (чистая копия `data/`) | Пройти мастер: цели, пути Zotero, режим ИИ (локальный / облачный / без ИИ). | Нет `goals.yaml`/`.env` → созданы; существующие не перезаписаны. Дальше — Today. Шаги понятны. | PASS — пустой дом: `/`, `/library`, `/today` → `/setup`; goals.yaml, .env, data/ созданы. `R3/S1/SETUP-01-*.png` | |
| ☐ SETUP-02 | `/setup` повторно; `zotero-summarizer setup` | Открыть `/setup` на уже настроенном; затем `/library` в чистом проекте. | Вернувшегося пользователя не гонят в мастер; первый запуск с `/` или `/library` ведёт в `/setup`. | PASS — настроенный `/setup` — «Already set up», без принудительного редиректа. `R3/S1/SETUP-02-8790-d.png` | |
| ☐ SETUP-03 🔌 | Ops → System → «Check readiness»; `zotero-summarizer doctor` | Нажать **Check readiness**; то же в терминале (`--json`). | Локальный режим «Ready» только после ответа модели и dry-run без записи; нехватка данных о памяти = «unknown», не угадано. | PASS — не «Ready»; память неизвестна → «Not enough free-memory information…»; `doctor --json` needs_action. `R3/S1/SETUP-03-1-d.png` | |
| ☐ NAV-01 | верхнее меню | Пройти **Today · Library · Search · Weekly · Settings · Ops**; открыть `/`. | `/` ведёт на Today; активная вкладка подсвечена; меню переносится на 390 px. | PASS — все 6 вкладок, 390 px без overflow. `R3/S1/NAV-01-*-m.png` | |
| ☐ NAV-02 | `/review`, `/triage`, `/pending`, `/annotate`, `/xyz` | Открыть старые адреса (в т.ч. с `?state=…`). | Ведут в Ops (вкладки) / Library «Select & label»; query сохранён; неизвестный адрес → Library. | PASS — редиректы со старых адресов, query сохранён; `/xyz` → Library (query отбрасывается). `R3/S1/(stdout прогона S1, results-S1.md)` | |

## 2. Today `/today`

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ TODAY-01 | Today | Открыть страницу с готовым shortlist. | Одна колонка карточек под «Today’s reading»; читаемые счётчики; без сырых feed-кодов; порядок по близости к тексту целей. | PASS — одна колонка, читаемые метки источников. `R3/S1/TODAY-01-{d,m}.png` | |
| ☐ TODAY-02 ✍ | Today → «New shortlist» | Нажать «New shortlist». Затем «Refresh status». | «Selecting your shortlist…», по окончании новый список; при ошибке — «Selection failed — existing shortlist kept». | PASS — ошибка 500 → «Selection failed — existing shortlist kept»; «Selecting your shortlist…» не поймано (завершается <1 с). `R3/S1/TODAY-02-*.png` | |
| ☐ TODAY-03 | карточка | Раскрыть **more** у карточки. | Авторы/abstract/легенда + причины выбора и «Policy at selection»; порядок карточек не меняется. | PASS — заголовок «Policy at selection» дословно не найден, причины выбора есть. `R3/S1/TODAY-03-more-d.png` | |
| ☐ TODAY-04 | карточка | Посмотреть AI-карточку (TL;DR, key result, New/Take/Caveat/Code). | ≤4 коротких пункта на английском, одна строка «почему» с целью; с отключённым ИИ/без согласия — только факты + причина. | PASS — вариант «без ИИ/согласия» не прогнан (ИИ одобрен). `R3/S1/TODAY-01-d.png` | |
| ☐ TODAY-05 | карточка | Посмотреть полоску фактов (иконки) и **more**. | Только найденные факты (одна строка на iPhone); если нет ни одного — одна тихая строка; **more** перечисляет все проверенные. | PASS — одна строка иконок на 390. `R3/S1/TODAY-01-m.png` | |
| ☐ TODAY-06 ✍ | карточка | Нажать **must / should / could / skip**; затем «Undo». У skip — «Reason (optional, one tap)» chips. | Решение записано и показано на карточке; Undo отменяет; chip причины сохраняется. Видны «Quality A», «k of n checks». | PASS — Undo и chips работают; после вердикта остаётся короткая вспышка «Updating…» ≈1.5 с (R5, косметика). `R3/S1/TODAY-06-*.png; R3/R5/today-verdict-d.png` | |
| ☐ TODAY-07 ✍ | карточка/нижняя панель | Выбрать карточки («Select all»), нажать **Add to library** и **Trash**; выбрать коллекцию. | Add — только для статей с актуальным full-text review (иначе «…skipped by Add — Trash still works»); Trash работает всегда; двойной Add не дублирует. | PASS — частично: Add не дублирует; текст «skipped by Add» не виден (в shortlist только reviewed); коллекция не менялась. `R3/S1/TODAY-07-*.png` | |
| ☐ TODAY-08 | карточка | Найти карточку «Random check». | Помечена «Random check» (калибровка, не рекомендация); ≤2 в день. | PASS — ≤2 «Random check». `R3/S1/TODAY-01-d.png` | |
| ☐ TODAY-09 | низ страницы | Раскрыть **Not reviewed yet (N)**; открыть одну. | Лучшие по рангу без review, без кнопок вердикта; открытие запускает review на странице статьи. | PASS — открытие ведёт на страницу статьи, review запускается кнопкой «Generate review» (не сам). `R3/S1/TODAY-09-open-d.png` | |
| ☐ TODAY-10 ✍ | карточка | Найти карточку с «Outdated review» → **Regenerate**. | Бейдж «Outdated review · regenerating…»; после — актуальный review; при выключенном ИИ — «AI not connected (Settings)». | PASS — частично: реальный outdated перегенерирован (~5 мин); карточка Today проверена на подмене ответа. `R3/S1/TODAY-10-*.png` | |
| ☐ TODAY-11 | меню «Today task settings» | Открыть **Article selection**, **RSS sources**, **Telegram today**. | Каждая ведёт в свою задачу (`?task=selection`/`rss`, Weekly). Неверный `?task=` → «Today task not found» + «Back to Today». | PASS — меню More и `?task=…`, неизвестный task → «Today task not found». `R3/S1/TODAY-11-*.png` | |
| ☐ TODAY-12 | Today (пустой/ошибка) | Пустой shortlist; недоступный статус. | «No papers in today’s shortlist yet.» + пути «New shortlist» / «Triage backlog»; ошибки читаемые, не молчаливые. | PASS — пустое и ошибочное состояния читаемы. `R3/S1/TODAY-12-*.png` | |
| ☐ TODAY-13 ✍ | Today → «Triage backlog» | Запустить; смотреть статус «Checking triage status…». | Идёт прогресс в собственной строке статуса на Today; можно оставить страницу и вернуться (Ops → Triage jobs этот запуск не показывает). | PASS — исправлено (w4: описание строки статуса на Today), перепроверено прогоном: прогресс виден, уход со страницы не останавливает; в Ops → Triage jobs не показывается — задумано. `R3/S1/TODAY-13-running-d.png, TODAY-13-ops-d.png` | |
| ☐ TODAY-14 | `data/today-exposure/` | После просмотра открыть файл дня. | Локальный `.jsonl` с показанными статьями/временем/ссылками; `ZS_TODAY_EXPOSURE_LOG=0` отключает. | PASS — лог `data/today-exposure/<день>.jsonl`: slate/card_shown/note_saved; выключатель `ZS_TODAY_EXPOSURE_LOG=0` вживую не прогонялся. `/tmp/zs-accept/home/data/today-exposure/2026-11-11.jsonl (описано в results-S1.md)` | |

## 3. Страница статьи `/paper/:key`

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ PAPER-01 | `/paper/KEY` | Открыть saved paper на 390 px. | Verdict-карточка на первом экране: вердикт, why, read first, code row; авторы свёрнуты «+N more»; без sticky-перекрытий. | PASS — 390: вердикт и «why» на первом экране. `R3/S2/shots/PAPER-01-top-m.png` | |
| ☐ PAPER-02 | карточка | Проверить полосу фактов под вердиктом. | Факты (full text, свой код, публикация, группа) без пустых кружков. | PASS — факты за «more»; у части проверенных статей все «unknown». `R3/S2/shots/PAPER-02-facts-m.png` | |
| ☐ PAPER-03 | review | Открыть review с планом (v3.3 FEATURE-03, #9). | Рядом с READ/SKIM: «Read first», «Skip», «What the original adds»; нет плана для отсутствующего решения; время «Time not estimated», не 0. | PASS — «Time not estimated» не воспроизводился. `R3/S2/shots/PAPER-02-full-m.txt (R3/S2/txt/)` | |
| ☐ PAPER-04 | review | Под вердиктом раскрыть строку-сворачиваемый блок «Idea: … · Evidence: … · Writing: …» (FEATURE-04, #15). | Внутри идея/качество/изложение раздельно; при SKIM — «Why SKIM» (причины) и конкретные targets. | PASS — разбор идея/качество/письмо, «Why SKIM». `R3/S2/shots/PAPER-04-whyskim-m.png` | |
| ☐ PAPER-05 | review | Раскрыть **Evidence** fold. | Дословные цитаты по полям (Summary, Findings, Methods…); «Show full passage»; «Quotes not saved — re-run the review» вместо пустоты. | PASS — цитаты + «Show full passage». `R3/S2/shots/PAPER-05-evidence-m.png` | |
| ☐ PAPER-06 | review | «Red flags», «Possible overstatement», goal board, «Details», «On this page». | Информируют, но не меняют вердикт; тип статьи (в т.ч. experience report; «type uncertain» — общий чеклист) виден; секции сворачиваются. | PASS — частично: «Possible overstatement», experience-report, «type uncertain» в копии нет. `R3/S2/shots/PAPER-06-full-d.txt (R3/S2/txt/)` | |
| ☐ PAPER-07 | review | Вердикт: **Decide** / **Save verdict** / «Yours:» / **Delete**; sticky-панель на телефоне. | Своё решение сохраняется, показано «Yours:», «Auto:» отдельно; Confirm/Override на предложенном вердикте. | PASS — Save verdict → «Yours: Could read», Edit/Delete, Delete с подтверждением; Confirm/Override не прогонялись. `R3/R4b/shots/p07b-v1-d.png, p07c-d1-d.png` | |
| ☐ PAPER-08 ✍ | review → «Ask this paper» | Ответить на typed и preset вопрос (напр. «Is the code released?»), reload; изменить **My notes** (FEATURE-05, #48). | Ответ с цитатой или отказ; вопрос сохранён отдельной записью («Saved question»), manual note его не стирает; различимы «Saved on this device» / «Saved in the app» / «Saved to the app and Zotero». | PASS — исправлено (w5), перепроверено: 9TG4SAGY теперь отвечает с проверенной цитатой (18 с). `R3/R5/(API-ответ в results-R5.md); R3/S2/shots/PAPER-08-reload-m.png` | |
| ☐ PAPER-09 | «Answer mode» | Переключить «Fast retrieval» / «Full context» / «Full text». | Режимы, читающие PDF, требуют PDF; без PDF — понятное сообщение. | PASS — исправлено (w5), перепроверено: «This paper has no PDF yet — attach one in Zotero or use Fetch full text.» за 4 с. `R3/R4a/shots/ask-XD9ADW6D-m.png` | |
| ☐ PAPER-10 | «Find similar papers» → «Show related saved papers» (FEATURE-06, #46) | Открыть **Related in my library**; затем «Try with comments» (отменить, потом подтвердить). | До 3 других saved-семейств; сама статья и отклонённые исключены; comments — только после явного согласия; без него «Metadata evidence». | PASS — исправлено (w5), перепроверено: «The comment check was too large for the model’s input limit…»; комментарии уходят только после подтверждения. `R3/R4a/shots/assess2-SRCH07-m.png` | |
| ☐ PAPER-11 | «Find similar papers» → «Find new relevant» (FEATURE-07, #46) | Нажать, раскрыть pool, reload. | Внешний набор ≤3 рекомендаций; уже сохранённые не названы новыми; **ничего не добавляется** само. Работает и для feed-only статьи. | BLOCKED-external — OpenAlex недоступен/429: «The search did not finish. Try again later.». `R3/R4b/shots/p11c-find2-d.png` | |
| ☐ PAPER-12 ✍ | review | Нажать **Regenerate** / «Run deeper review» / «Re-run deeper review». | Прогресс; провал сохраняет прошлый валидный review и показывает реальную причину; зависание не опрашивается вечно. | PASS — частично: Generate/Regenerate работают; один review упал по усечению вывода — исправление проверено тестами (w5: повтор с вдвое большим бюджетом вывода). `R3/S2/shots/PAPER-12gen2-done-m.png, PAPER-12regen-done-m.png` | |
| ☐ PAPER-13 | review | Статья с «Outdated review». | Остаётся рекомендованной, видны вердикт и Add для того же PDF; «Older review — re-run for the new digest.», не пустая панель. | PASS — «Outdated review: model or prompts changed. Regenerate». `R3/S2/shots/PAPER-01-top-m.png` | |
| ☐ PAPER-14 | вкладка «Figures» | Открыть Figures; stale-статья. | Figures и Tables отдельно; stale → «Rebuilding figures…»/«Rebuild»; без битых картинок. | PASS — частично: ветка «Rebuilding figures…» не воспроизводилась. `R3/S2/shots/PAPER-14-9LMXP2R7-m.png` | |
| ☐ PAPER-15 ✍ | paper → тег/коллекция | «Add to collection…», редактор тегов. | Запись в Zotero с backup; видна в копии Zotero. | PASS — коллекция и тег записаны, backup, подтверждено в sqlite копии. `R3/R4b/shots/p15b-tags1-d.png` | |
| ☐ PAPER-16 | «Open full review ↗» | Из Library открыть в новой вкладке. | Открывается `/paper/KEY`; назад возвращает в список. | PASS — «Open full review ↗» в новой вкладке, «← Back». `R3/S2/shots/PAPER-16-back-d.png` | |

## 4. Library `/library`

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ LIB-01 | Library → **Read next** | Открыть очередь; сортировка «Sort by» (Best match…), направление. | Очередь с рангом, счётчики читаемые; нет сырого HTML; пояснения свёрнуты. | PASS — сортировки, 390 без overflow. `R3/S3/txt/LIB01-*` | |
| ☐ LIB-02 | фильтры | «Journal», «Matches my goals», «Prestige», «Quality»; «Browse & filter», Collections, Top Tags. | Список фильтруется; сброс возвращает всё. | PASS — частично: фильтры Journal/Prestige не найдены по имени. `R3/S3/txt/LIB02c-d.txt` | |
| ☐ LIB-03 | строка | Раскрыть строку: digest, план чтения, figures, предложенный вердикт. | Как на странице статьи; «Flagged»/«Low confidence» видны; Confirm/Override сохраняются. | PASS — частично: Confirm/Override не сохранялись. `R3/S3/shots/LIB03-exp-d.png` | |
| ☐ LIB-04 ✍ | Library → **Select & label** (`?mode=batch`) | «Select» нескольких → bulk verdict → «Marked read». | Метки проставлены, история хранится; ошибки — на уровне статьи. | PASS — bulk «Could read» ×2 и «Mark read (2)» записаны, backup. `R3/R4a/shots/verdict-LIB04-d.png, markread-LIB04-d.png` | |
| ☐ LIB-05 | Predictions / top picks | Открыть top picks; «All reviewed». | Незакрытые top picks; если всех решено — «No undecided top picks.» | PASS — «All top picks reviewed — Confirm or Override on the rows below.». `R3/S3/txt/LIB01-top` | |
| ☐ LIB-06 ✍ | «Fetch full text» | Нажать; наблюдать «Fetching full text…». | Идёт, не блокирует; PDF из OA (UA, bioRxiv `.full.pdf`) или браузерной ступени; paywall/Cloudflare не ломает; «No PDF» помечено. | BLOCKED-owner — единственное действие «Fetch full text» — массовое (тысячи PDF на копии, диск ограничен); проверена только защита «Zotero appears to be running. Fetch + attach anyway?» → отмена. `R3/R4a/shots/guard-Fetch-LIB07g-d.png` | |
| ☐ LIB-07 ✍ | меню «Zotero ▾» / «Sync all → Zotero» | Запустить на копии. | Шаги «2/3 — writing relevance tags», «3/3 — stamping ranks»; при открытом Zotero — «Sync cancelled — close Zotero, then retry.» | PASS — Sync all: 3 шага, 628 тегов, 2472 papers ranked, backup; защита при запущенном Zotero. `R3/R4a/shots/sync0-LIB07-d.png` | |
| ☐ LIB-08 ✍ | «Rescore» | Нажать на выбранных. | «Scoring…», затем обновлённые оценки; ранжирование не ломается. | PASS — «Scoring…» ≈6 мин → «Scores from 2026-11-11 (just now)». `R3/R4a/shots/resc0-LIB08-d.png` | |
| ☐ LIB-09 | «Ask this paper» в строке | Вопрос из Library (FEATURE-05). | Как PAPER-08; без PDF — ответ с отказом, вопрос сохранён. | PASS — исправлено (w5), перепроверено: ответ без PDF «This paper has no PDF yet…»; вопрос сохраняется. `R3/S3/txt/LIB09b-answer-d.txt; R3/R4a/shots/ask-XD9ADW6D-m.png` | |

## 5. Search `/search`

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ SRCH-01 🔌 | Search | Тема → **Search**; прогресс «Searching…». | Дедуплицированные кандидаты (arXiv/Europe PMC/OpenAlex/Crossref/Semantic Scholar), ссылки настоящие, не угаданные по названию. | PASS — 20 кандидатов, 390 без overflow, сессия переживает reload. `R3/R4a/shots/res-SRCH01-m.png` | |
| ☐ SRCH-02 | «Explicit constraints (optional)» | «Required/Excluded exact phrases», «Only publication types». | Ограничения подтверждаются владельцем; членство в библиотеке неизвестно = «Membership unknown — not established new.» | PASS — required/excluded соблюдены; «Find new relevant» → 0 кандидатов из-за rate limit OpenAlex (формулировка membership не увидена). `R3/R4a/shots/(R3/R4a/txt/SRCH02-c-m.txt)` | |
| ☐ SRCH-03 🔌 | «Agentic refinement» | Включить; «Refining the search…», «Deep-reviewing the top papers…». | Видны шаги; результат воспроизводим; reload сессии сохраняет. | PASS — исправлено (w6), перепроверено: «Reviewed 6 of 10» → «Reviewed 8 of 10». `R3/R5/srch-prog-m.png` | |
| ☐ SRCH-04 🔌 | Targeted Search (FEATURE-08, #30) | Тема + вопрос + «Experimental original-source question evidence». | Состояние answer-evidence, точный источник/locator; partial ≠ «нет ответа»; в Recommended — только проверенное. | PASS — частично: два разных маршрута (screening local ≠ reviews kather), «Experimental question evidence: complete»; продвижение кандидата не наблюдалось (нет полного текста). `R3/R4a/shots/chg-SRCH04-d.png, kf-SRCH04-d.png` | |
| ☐ SRCH-05 ✍ | результат → «Add to library» / «Add to:» | Добавить одну статью в коллекцию на копии. | Add только по явному клику; backup; после неопределённого Add — «Reconcile retained Add», без дубля. | PASS — Add только по клику → материализован в «Selected», backup. `R3/R4a/shots/add-SRCH05-d.png` | |
| ☐ SRCH-06 | сессия | Открыть сессию, раскрыть «Metadata evidence». | Диагностика провайдеров различает «пусто» и «ошибка» («Provider retrieval failed.»), не говорит о полноте охвата. | PASS — исправлено (w6), перепроверено: «arXiv: no results returned / Europe PMC: 7 results…»; обычный поиск не различает «пусто» и «сбой» (известно). `R3/R5/srch-sources-m.png` | |
| ☐ SRCH-07 🔌 | comment assessment | Ошибка/отключение («Comment assessment is disabled.»). | Понятный отказ; без согласия приватные comments не уходят. | PASS — «Related in my library», подтверждение перед комментариями. `R3/R4a/shots/assess2-SRCH07-m.png` | |

## 6. Weekly и Telegram `/weekly`

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ WEEK-01 | Weekly | Открыть без сохранённых отчётов и с ними. | Пустое состояние понятно; «Refresh saved runs» перечитывает; Weekly не генерирует сам. | PASS — без авто-генерации; «Saved digests». `R3/S4/w0-*.txt` | |
| ☐ WEEK-02 | «Choose a saved run» (FEATURE-09, #12) | Выбрать run, раскрыть карточку, «Download canonical JSON» / «Download Markdown». | Показан именно этот run; файлы совпадают с сохранённым; «Needs a current review / full text» явно. | PASS — имена прогонов — диапазоны дат; JSON совпадает с API. `R3/S4/w2-d.txt, w3-d.txt` | |
| ☐ WEEK-03 | карточка digest | Раскрыть «Project use», «Why chosen», «Evidence gaps», «Missing reproduction inputs». | Совпадения с проектом — непроверенные заметки; неизвестное не превращено в сроки. | FAIL — косметика: под «Reproducibility: blocked» остался сырой `dataset_provenance` (в «Missing reproduction inputs» исправлено); «1 card» исправлено (w4), перепроверено. `R3/R5/weekly-digest-d.png` | |
| ☐ WEEK-04 🔌 | Weekly → «Telegram sources» (`?task=telegram`) | «Add Telegram source», «Save Telegram sources»; выбрать локального провайдера. | Каналы/группы/топики сохраняются; без модели — режим «No summary»; до ~30 каналов. | PASS — Sources: добавить/убрать канал, лимит 30. `R3/S4/w4-*.txt` | |
| ☐ WEEK-05 🔌 | «Allow & check channels» | Один раз разрешить («Allowed.»), затем «Check channels». Отзыв — снять флажок «Allow reading these channels when I press “Check channels”» на странице Sources. | Фоновая задача с этапами; новые посты появляются по окончании; согласие отзывается. | BLOCKED-owner — нет Telegram-сессии: согласие (флажок) и «Allow & check channels» проверены; фоновые этапы и новые посты — владельцу. `R3/S4/w5-m-*.png` | |
| ☐ WEEK-06 🔌 | страница сессии | «Copy command» → `research-feed telegram-auth` в терминале. | Команда копируется («Copied»); приложение не вступает в чаты и не пишет. | PASS — красный блок + команда `zotero-summarizer research-feed telegram-auth` + «Copy command». `R3/S4/w6-d-copied.png` | |
| ☐ WEEK-07 🔌 | «Trends» | Посмотреть топ-5; «Show all N trends» / «Show fewer trends». | Метка, gist, идеи/инструменты, маркер new/growing, ссылки на посты. | PASS — «Show all N trends» проверен на подменённом ответе (в реальных данных всегда 5). `R3/S4/w7-*.txt` | |
| ☐ WEEK-08 🔌 | карточки постов | Раскрыть строки обсуждения и **Disagreement**; «Details», «Continues …». | Строка цитирует точные комментарии; «Disagreement» только если найден; комментарии грузятся по тапу. | PASS — точные комментарии, «All comments», «Disagreement». `R3/S4/w8-point-m.png` | |
| ☐ WEEK-09 🔌 ✍ | пост | **Done**, «Undo», «Mark all read». | Прочитанное сворачивается в «n read»; Undo возвращает. | PASS — Done / «Mark all read» / Undo, состояние сохраняется. `R3/S4/w9-m-markall.png` | |
| ☐ WEEK-10 🔌 ✍ | «Your feedback» | Chips «Useful / New to me / Want to try / Good discussion», «Not a fit» + причина, заметка. | Сохраняется; «Not a fit» хранится в базе; «Export feedback» скачивает. | PASS — chips, «Not a fit» + причины, «Export feedback». `R3/S4/w10-m-chips.png` | |
| ☐ WEEK-11 🔌 | «Past digests» | Открыть дату архива. | Те же карточки; сначала непрочитанные; с lead-темой. | PASS — список прошлых дайджестов и архив. `R3/S4/w11-m-list.png` | |
| ☐ WEEK-12 🔌 | ошибки/лимиты | Лимит Telegram, провал модели. | Посты без комментариев сохранены; канал не отброшен; ошибки читаемы; облачный провайдер — только после явного разрешения. | BLOCKED-external — реальный лимит Telegram не вызвать; формулировки проверены на подменённом `/status`. `R3/S4/w12-m-deferred.png` | |

## 7. RSS и backfill (Today → «RSS sources»)

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ RSS-01 ✍ | `/today?task=rss` | «Add subscription», «Save subscription»; Edit/Enabled; удалить. | Состояние сохраняется; Save **не** запускает refresh («Feed updated. Nothing fetched.»). | PASS — добавить / отключить / удалить; «Last checked» теперь в локальном времени (w4, R5). `R3/S4/r1-m-*.png; R3/R5/rss-d.png` | |
| ☐ RSS-02 ✍ | «Import feeds from Zotero» | Импортировать подписки (копия). | Список появился; дубли не создаются. | PASS — импорт из Zotero без дублей. `R3/S4/r2-m-import.png` | |
| ☐ RSS-03 🔌 | «Suggested engineering feeds» (FEATURE-01, #16) | Добавить «HackerNoon AI Agents»/«Agentic AI»; **Refresh RSS now**; затем «Triage backlog». | Статьи в обычном workflow; пустой upstream — отдельное состояние, не PASS за triage. | PASS — «Add HackerNoon …», «Checked 10 feeds: … new, … updated, … failed»; в повторных refresh «updated» не падает до 0 у живых лент (известное ограничение). `R3/S4/r3-m-refreshed.png; R3/R5/rss-d.png` | |
| ☐ RSS-04 🔌 | «Refresh RSS now» | Один неисправный feed. | Различаются «nothing new», «skipped while offline», «source failed»; `RssNamespaceWarning:` — предупреждение, не отказ. | PASS — формулировки по ленте (429/404/namespace); «unchanged» на реальных лентах >0, но «updated» остаётся ненулевым — известное ограничение. `R3/S4/r3-m.txt, r5-m.txt` | |
| ☐ RSS-05 🔌 | refresh после паузы | Feed не проверялся несколько дней (arXiv). | Догоняет до watermark; «Incomplete fetch», если обрезан; ничего не теряется. | PASS — бейдж «Incomplete fetch» проверен на подменённом ответе. `R3/S4/r6-m-incomplete.png` | |
| ☐ RSS-06 ✍ | пометка прочитанным в Zotero | Mark read на копии. | Backup перед записью; старые backups чистятся (5) только после успеха. | PASS — «Marked read: 1 paper.», backup, хранится ровно 5. `R3/S4/l3-m-markread.png` | |

## 8. Settings `/settings`

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ SETT-01 | Settings | Открыть обзор. | Сводка: «AI-assisted features», «Model for each step», «Review depth», «Checklist engine», «Background processing», «Zotero & PDFs», «Library access», «Output language»; «Change» ведут в задачу. | PASS — 8 блоков, 390 без overflow. `R3/S5/SETT-01-d/m` | |
| ☐ SETT-02 | полоса готовности | «Needs attention». | Только открытые проблемы; каждая причина и адрес показаны один раз. | PASS — без проблем полосы нет (проверено); стадия с недоступным провайдером в строке: «Not reachable» (R4b) / «Provider status unavailable» (R5). Полоса «Needs attention» для недоступного провайдера стадии не появляется — так и задумано, ожидание уточнено. `R3/R4b/shots/sett02d-settings-d.png; R3/R5/sett02-attention-d.png` | |
| ☐ SETT-03 ✍ 🔌 | «Choose models» (AI models) | «Add provider» → API address/protocol/Authorization → «Connect provider» / «Load models». | Явное разрешение именно этому адресу; ключ из env/keyring не показан; видна таблица **impact** до «Use as default»/«Use for selected tasks». | PASS — «Proposed model assignments» до применения; повторный «Connect provider» плодит дубли подключений (известное ограничение). `R3/S5/SETT-03-a..h` | |
| ☐ SETT-04 🔌 | AI models | «Test saved model», «Test selected tasks». | Один короткий prompt; ответ или конкретная ошибка (HTTP/timeout/empty/«unknown»); для локальной модели сначала память: `memory_insufficient`/`swap_nearly_full`/`model_size_unknown` с цифрами; без повторов и подмены. | PASS — local: «Local test not sent: free 9.2 GiB, need ≥ 11.1 GiB…»; hosted: «✓ Works — answered in 7.8 s». `R3/R4b/shots/sett04-local-d.png, sett04-hosted-d.png` | |
| ☐ SETT-05 | «Model for each step» | Таблица шагов. | Модель, медиана времени и статус по шагу: «Provider reachable», «Not reachable», «Provider needs approval». | PASS — строки модели, «✓ Provider reachable». `R3/S5/SETT-05-a` | |
| ☐ SETT-06 ✍ | «Change AI mode» | Вкл/выкл AI-assisted features. | Выкл → «ML feed ranking remains available»; Today/карточки показывают «Enable AI». | PASS — Off → «ML feed ranking remains available», возвращено On. `R3/S5/SETT-06-b` | |
| ☐ SETT-07 ✍ | Review depth (`?task=depth`) | Выбрать **Thorough**/**Fast**; Save. | Применяется к следующему review; review другой глубины — «Outdated». | PASS — Fast/Thorough, возвращено Thorough. `R3/S5/SETT-07-a/d` | |
| ☐ SETT-08 ✍ 🔌 | Checklist engine (`?task=engine`) | **Off / Local / Cloud**; «Access key»/«Replace access key»; **Test**. | Off по умолчанию; Cloud предупреждает об отправке текста; Test показывает результат; ключ не выводится. | PASS — предупреждение об облаке; ключ не показывается; формулировка Test с плохим ключом улучшена (w5, проверено тестами). `R3/S5/SETT-08-a..d` | |
| ☐ SETT-09 ✍ | Background processing (`?task=processing`) | Изменить «Papers reviewed at once», «Calls per paper», «Feed catch-up days», «Pages per feed»; вернуть к default. | Значения по умолчанию (8/2/7/10) не пишутся в `goals.yaml`; изменённые сохраняются. | PASS — goals.yaml после возврата идентичен исходному. `R3/S5/SETT-09-a/b` | |
| ☐ SETT-10 ✍ | Library access (`?task=university`) | «Change library access»: вкл/выкл browser fetch. | «Browser fetch enabled · sign-in is separate» / «off»; OA-поиск не меняется. | PASS — «Library access saved.». `R3/S5/SETT-10-a/b` | |
| ☐ SETT-11 ✍ | Zotero & PDFs (`?task=zotero`) | «Change Zotero paths». | Пути сохраняются; применяются **после перезапуска**. | PASS — «Paths written. Restart required…», пути возвращены. `R3/S5/SETT-11-a/b` | |
| ☐ SETT-12 ✍ | Output language (`?task=language`) | Сменить язык. | Сохранён; следующие выходы на нём. | PASS — Russian ↔ English. `R3/S5/SETT-12-a` | |
| ☐ SETT-13 | Today «Article selection» (`/today?task=selection`) | Открыть и сохранить на копии. | Сохраняет только свои поля; уход с несохранённым спрашивает. | PASS — «Article selection saved.», diff в 1 строку, возврат. `R3/S5/SETT-13-a` | |
| ☐ SETT-14 | несохранённые изменения | Уйти с несохранённой задачи; неопределённый ответ сохранения. | Вопрос подтверждения; уход не отменяет уже отправленное; неопределённый save → перечитывание, не автоповтор. | PASS — подтверждение при уходе, «Save failed» без авто-повтора. `R3/S5/SETT-14-a/b` | |
| ☐ SETT-15 | неверный `?task=` | `/settings?task=bad`. | «Settings task not found» + «Back to Settings»; ничего не изменено. | PASS — «Settings task not found / Back to Settings». `R3/S5/SETT-15-d/m` | |

## 9. Ops `/ops`

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ OPS-01 | Ops → **Feed review** | Открыть; «Gate-rejected pile»; relabel. | Список с h-index/venue; можно переразметить; без Re-label Audit страницы. | PASS — частично: отбракованные — одна подсказка, Remove доступен; relabel проверенной статьи не прогонялся. `R3/S5/OPS-01-a/b` | |
| ☐ OPS-02 ✍ | Feed review | «Apply approved → Zotero» на копии. | Запись с backup; ошибки по строкам. | PASS — очередь Feed review пуста — применено через Pending: 6 changes applied, теги в sqlite. `R3/R5/pending-apply2-d.png` | |
| ☐ OPS-03 | Ops → **Triage jobs** | Открыть; «Recent Jobs», фильтр «Last 7 days». | Статусы job, «Agreement», «No errors.»; Cancel активного job. | PASS — список заданий; Cancel не нажимали. `R3/S5/OPS-03-a/b` | |
| ☐ OPS-04 ✍ | Triage feedback | «Approve»/«Reject» по завершённой статье. | Сохраняется («Approved by you»). | PASS — Approve → «Approved by you», Reject → «Rejected by you», переживает reload. `R3/R5/triage-approved-d.png, triage-rejected-d.png` | |
| ☐ OPS-05 | Ops → **Pending changes** | Открыть список; клавиатурная навигация. | Pending видны; фильтр статуса; «Select all»/«Clear». | PASS — j/k/space, Select all, вкладки статусов. `R3/S5/OPS-05-a` | |
| ☐ OPS-06 ✍ | Pending | Apply выбранных; Reject; «Retrying failed change…». | Apply с backup; при запущенном Zotero «Apply cancelled — Zotero is running.»; Reject — одна транзакция; stale selection отклонён целиком. | PASS — Regenerate → Apply: «1 change(s) applied», одна digest-заметка; исправление дубликатов заметок (w3) проверено тестами; свёртка одинаковых строк Pending не наблюдалась. `R3/S5/OPS-06-*; R3/R5/pending-applied2-d.png` | |
| ☐ OPS-07 | Ops → **System** | «Check readiness», «Calibrate reviews» (`?task=`). | Страницы-задачи с «Back to System»; без запуска, пока не нажато. | PASS — страницы задач; работа только по клику. `R3/S5/OPS-07-a/b` | |
| ☐ OPS-08 ✍ 🔌 | «Calibrate reviews» → «Start review calibration» | Запустить. | «Calibrating… measuring endpoint»; незавершённый run блокирует новый; не обещает длину вывода. | PASS — калибровка 3/3 за ≈22 мин, status ready, 12000 символов. `R3/R5/ops08-start-d.png` | |
| ☐ OPS-09 🔌 | «Check readiness» → «Verify setup» | Запустить; «Stop verification». | Чек-лист «Setup checks», «Strict offline», «Local inference»; «unknown», не догадка. | PASS — «8 checks passed · 1 unavailable»; AI-проверка честно «Needs action» (память). `R3/S5/OPS-09-a/b` | |
| ☐ OPS-10 | **Your decisions** | «Run now» (и переключатель weekly). | Отчёт: что изменилось, согласие с предсказаниями, диапазоны, пробелы данных; read-only. | PASS — «Run now» ≈20 с, отчёт 14 papers. `R3/R4b/shots/ops10-run-d.png` | |
| ☐ OPS-11 ✍ | Model card / «Retrain model» | «Refresh»; «Retrain model» → «Replace model and retrain». | Диалог «Retrain the feed gate?» с Replaces/Takes; обучение «Training…»; не стартует при неизвестной конфигурации. | PASS — диалог «Retrain the feed gate?» + Cancel; само переобучение не подтверждали (нагрузка CPU). `R3/R4b/shots/ops11-dlg-d.png` | |

## 10. Офлайн / PWA и удалённый доступ

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ OFF-01 | значок статуса | «Connected»/«Offline»/«Server unreachable»/«Connection not confirmed»; «Details». | Статус честный; при недоступном сервере кэш показывается. | PASS — «Connected · N pending changes» / «Offline» / «Server unreachable». `R3/S6/E/OFF-01a-d` | |
| ☐ OFF-02 ✍ | «Sync now» → offline (FEATURE-10, #19) | Sync now; отключить backend; открыть новую synced paper; заметка/вердикт; перезапуск браузера; reconnect. | Контекст доступен; очередь переживает restart и ждёт ACK; PDF и новые AI-вызовы offline не обещаны. | PASS — заметка офлайн → «1 unsent», переживает перезапуск браузера, после reconnect отправлена. `R3/S6/E/OFF-02a-e` | |
| ☐ OFF-03 ✍ | «Sync conflict for …» | Конфликт с другого клиента; «Keep mine» / «Use server» / «Open paper». | «Changed elsewhere — compare before resolving»; обе версии видны. | PASS — исправлено (w4), перепроверено: баннер называет статью по названию, «Keep mine» обновляет поле. `R3/R5/off-conflict-d.png, off-keepmine-d.png` | |
| ☐ OFF-04 🔌 | `make iphone` (FEATURE-11, #19) | Открыть `https://<mac>.<tailnet>.ts.net` с iPhone; затем чужой/анонимный. | Owner открывает и синхронизирует; остальные — нет; Ctrl+C снимает mapping; `make run` отклоняет tailnet-адрес. | BLOCKED-owner — нужен реальный iPhone/TLS; preflight `make -n iphone` и Tailscale пройден. `R3/S6/E/OFF-04-make.txt` | |
| ☐ OFF-05 🔌 | PWA на iPhone | «На экран Домой», открыть. | Запускается как приложение; верстка 390 px; offline Generate отключён. | PASS — серверная часть (apple-touch-icon.png 200); «Add to Home Screen» на iPhone — владельцу. `R3/S6/E/OFF-05.txt` | |
| ☐ OFF-06 | `zotero-summarizer prefetch-models [--check]` | `--check` без сети. | Список кэша; strict offline отказывает на некэшированном. | PASS — `prefetch-models --check` ок; ошибки offline/пустой кэш без traceback — исправление проверено тестами и на scratch-копии (w4). `R3/S6/E/OFF-06*.txt; fixes-w4/cli/after.txt` | |

## 11. MCP-инструменты

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ MCP-01 | `zotero-summarizer mcp` | Подключить клиент; вызвать `get_library_status`, `search_papers`, `get_paper`, `find_similar_papers`. | Данные из запущенного приложения; для similar нужен непустой запрос/seed; очередь чтения не выдаётся за рекомендации. | PASS — 13 инструментов, поиск быстрый (8 batch-запросов). `R3/S6/E/MCP-01.txt` | |
| ☐ MCP-02 ✍ | `start_triage`, `get_job_status`, `cancel_job`, `submit_feedback` | На копии. | Работают по правилам согласия; контент статьи не считается разрешением. | PASS — validation_error / not_found; `start_triage` на реальных ключах не запускали (расход модели). `R3/S6/E/MCP-02-03.txt` | |
| ☐ MCP-03 ✍ | `list_pending_changes`, `apply_pending_changes`, `manage_tags`, `manage_collections`, `set_reading_priority` | На копии. | Применение только с явным подтверждением, backup. | PASS — устаревший hash отклонён, верный применён с backup. `R3/S6/E/MCP-03c.txt` | |

## 12. CLI и `make`

| ID | Команда | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ CLI-01 | `make help`, `make` | Выполнить. | Bare `make` = help; перечислены install/run/iphone/dev/test/lint/check. | PASS — `make help` == `make`. `R3/S6/E/CLI-01.txt` | |
| ☐ CLI-02 | `make install`, `make run` | На чистом клоне. | Зависимости, UI собран/пересобран, Today на `127.0.0.1:8000`. | PASS — только `make -n` (install ходит в сеть). `R3/S6/E/OFF-04-make.txt` | |
| ☐ CLI-03 | `zotero-summarizer --help` | Выполнить. | Есть serve, serve-mobile, mcp, migrate, setup, doctor, calibrate, feeds, goldenset, faithbench, research-feed; удалённых `make api/ui` нет. | PASS — все подкоманды есть, `make api/ui` нет. `R3/S6/E/CLI-03.txt` | |
| ☐ CLI-04 | `zotero-summarizer migrate`; `smoke-test` | Выполнить на копии. | Миграции без потери; smoke OK. | PASS — migrate rc 0 (schema 18), smoke-test ok (174 routes). `R3/S6/E/CLI-04.txt` | |
| ☐ CLI-05 🔌 | `zotero-summarizer feeds list` / `preview` / `run` | Preview (read-only, без LLM), затем run на копии. | Список/preview без записи; run паркует в `awaiting_review`. | PASS — частично: list/preview; `feeds run` не запускали (расход модели). `R3/S6/E/(R3/B/txt, раунд 1)` | |
| ☐ CLI-06 🔌 | `research-feed run … --from --to --shortlist-budget --card-budget` (FEATURE-12, #12) | Запустить; `--cached-only`; `research-feed view`. | JSON+Markdown, карточки связаны с проектом; лимиты соблюдены; без `--queue-zotero` Zotero не пишется. | PASS — исправление проверено тестами: `view` без отчёта → одна строка, rc 2 (раньше traceback); счётчик «not_triaged» не считается сбоем (w1/w4, scratch-прогон). `fixes-w4/cli/after.txt, fixes-w1/cli-item3.txt` | |
| ☐ CLI-07 🔌 | `research-feed telegram-auth` (FEATURE-13, #27) | Авторизоваться один раз. | Сессия сохранена; **сбор — только из UI**; `run --source telegram` удалён (см. WEEK-05). | BLOCKED-owner — интерактивный вход в Telegram; проверено: `run --source telegram` удалён. `R3/S6/E/CLI-07-help.txt` | |
| ☐ CLI-08 | `goldenset analyze-feedback --project-root ROOT --cutoff ISO` (FEATURE-14, #47) | Запустить; weekly по конфигу. | Описательный отчёт, ограничения, примеры; labels/модель не меняются. | PASS — отчёт опубликован, labels/модель не тронуты. `R3/S6/E/CLI-08.txt` | |
| ☐ CLI-09 ✍ | `goldenset model-history`, `restore-model` | Список, затем restore на копии. | Снимки перечислены; текущая модель сохраняется; нужен restart daemon. | PASS — список и restore на копии; ошибка несуществующего снимка — одна строка. `R3/S6/E/CLI-09-history.txt, CLI-09-restore.txt` | |
| ☐ CLI-10 ✍ | `goldenset export`, `train-classifier` | На копии. | Экспорт CSV/JSONL; обучение не пишет в golden CSV. | PASS — CSV/JSONL, обучение не трогает golden CSV. `R3/S6/E/CLI-10-export.txt` | |
| ☐ CLI-11 🔌 | `zotero-summarizer doctor [--json --fix]` | Выполнить. | Чек-лист; `--fix` — только безопасные исправления. | PASS — `doctor` «Needs action»; формулировка «Local model not tested…» исправлена (w4, проверено тестами). `R3/S6/E/CLI-11.txt` | |
| ☐ CLI-12 🔌 | `zotero-summarizer calibrate --papers 3` | Запустить. | Подбор технических default под вашу среду. | PASS — калибровка ≈24 мин; построчный прогресс добавлен (w4, проверено тестами). `R3/S6/E/CLI-12.txt` | |
| ☐ CLI-13 | `zotero-summarizer serve` / `feeds serve` | Запустить на свободном порту (не `:8000` релиза). | Ошибка bind сообщается, чужой процесс не убивается. | PASS — исправлено (w2), перепроверено: занятый порт → одна строка, rc 1 за <1 с. `R3/S6/E/CLI-13.txt` | |
| ☐ CLI-14 | `make lint`, `make test`, `make check` | Выполнить. | Зелёные (кроме известного macOS fork crash — сравнить с baseline). | PASS — только `make lint` (rc 0); `make test` и `make check` гоняет лид. `R3/S6/E/CLI-14-lint.txt` | |

## 13. Запись в Zotero

| ID | Где | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ ZOT-01 ✍ | любая запись | Add/тег/коллекция/Apply на копии. | Перед записью backup базы Zotero; результат виден в копии Zotero. | PASS — Regenerate → Apply: ровно одна «Deep digest», backup; исправление дубля старых заметок (w3) проверено тестами. `R3/R4a/txt/pend2-ZOT01-d.txt` | |
| ☐ ZOT-02 ✍ | Zotero note | Открыть review-note в копии. | Заметка несёт весь review: digest, rubric с цитатами, «k of n checks», red flags, type, goal board, code link, section lines. | PASS — заметка: digest, rubric с цитатами, «Grade A · 4 of 5 checks», goal board. `R3/R4a/(sqlite note 47177, results-R4a.md)` | |
| ☐ ZOT-03 ✍ | вердикт → note | Поставить вердикт. | Вердикт и rationale зеркалятся заметкой; не считаются одобрением Pending. | PASS — вердикт → тег `label:should_read`, rationale → заметка «My notes»; не одобрение Pending. `R3/R4a/shots/rat-ZOT03-d.png` | |
| ☐ ZOT-04 | неопределённая запись | Прервать запись. | Показан «unconfirmed», без авто-повтора. | BLOCKED-owner — прервать запись без остановки сервера нельзя; при блокировке БД запись ждёт ~64 с без сообщения, затем успешна. `R3/R4a/shots/lockfail2-ZOT04-d.png` | |

## 14. Экспериментальные инструменты (только при использовании)

Только на frozen inputs, не на основной библиотеке. Вывод инструмента ≠ научная польза.

> Примеры EXP-01…04 — **синтетические** (без данных владельца и вызовов моделей): `python -m tools.method_validation.examples OUT_DIR` пишет их в пустую `OUT_DIR`; они проверяют проводку CLI, а не качество методов. Ошибки путей/конфигов — одна строка и код 2.

| ID | Команда | Как проверить | Ожидаемый результат | Результат | Апрув |
|---|---|---|---|---|---|
| ☐ EXP-01 🔌 | `python -m tools.method_validation support --config FILE` (FEATURE-15, #37) | Синтетический пример: `python -m tools.method_validation.examples /tmp/exp` печатает готовые команды. Без `--run` (preview, 0 вызовов); затем `--run` (replay записанных ответов, пишет только в `/tmp/exp/support/selected`), `--inspect <каталог из «Private artifacts:»>`. Свои данные — свой frozen config. | Ограничения до запуска; после — source-linked readouts; inspect не запускает модель. | PASS — preview, `--run`, `--inspect` rc 0. `R3/S6/E/EXP-support-*.txt` | |
| ☐ EXP-02 🔌 | `… priority --config FILE` (FEATURE-16, #38) | То же на `/tmp/exp/priority/config.json` (см. EXP-01). | Сравнение admitted frozen priorities; production ranking не меняется. | PASS — preview/run/inspect rc 0. `R3/S6/E/EXP-priority-*.txt` | |
| ☐ EXP-03 🔌 | `… collections --config FILE` (FEATURE-17, #42) | То же на `/tmp/exp/collections/config.json` (см. EXP-01). | Только существующие коллекции; новые имена/membership не применяются. | PASS — preview/run/inspect rc 0. `R3/S6/E/EXP-collections-*.txt` | |
| ☐ EXP-04 🔌 | `zotero-summarizer faithbench interpretation --project-root ROOT --recipe FILE` (FEATURE-18, #49) | Inspect без моделей на синтетическом рецепте: `ZOTERO_SUMMARIZER_HOME=/tmp/exp/faithbench/home zotero-summarizer faithbench interpretation --recipe /tmp/exp/faithbench/home/data/faithbench/recipe.json` → `INSPECTED_NOT_EXECUTED`; `--run --capture-local` — отдельно (нужны провайдер и ваш рецепт); `--run-id ID`. Без рецепта — подсказка с кодом 2, не traceback. | Условие C: Finding / Scope / Unproven с evidence; обычный review карточку не получает; вывод adopt/reject без оценок людей не объявлен. | PASS — INSPECTED_NOT_EXECUTED; без рецепта — одна строка, rc 2. `R3/S6/E/EXP-04.txt` | |

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
