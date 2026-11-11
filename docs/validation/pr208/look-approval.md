# PR208 — апрув «как выглядит весь функционал»

Ссылки ведут в `data/` (gitignored) и работают только на Mac владельца. Прошлый апрув: v4 + R6 ([manual-checklist.md](manual-checklist.md)).

## Открыть на телефоне

`make iphone` → напечатает `https://<mac>.<tailnet>.ts.net`; открыть в Safari при включённом Tailscale (README.md, «Use on iPhone»). Одновременно не запускать `make run`/`make dev`.

## Что изменилось с прошлого апрува

1. Простые слова: «Not interested», «Saved to Zotero», без «Inspect the attempt error», без склеенных абстрактов.
2. Search: «What was searched» понятно, «Reviewed N of 10», влезает в 390 px.
3. «Server is busy — retrying…» отличается от «Server unreachable»; Today не врёт «AI not connected».
4. Triage длинных статей целиком/по частям, «Stopping after the current AI call finishes…».
5. Ваши ревью идут вне очереди; Pending сворачивает старые версии; Settings не дублирует подключения.

Подробнее: [CHANGELOG](../../../CHANGELOG.md).

## Today
[iPhone](../../../data/perf-20261107/acceptance/R3/R6/ui/TODAY07-top-m.png) · [после Add](../../../data/perf-20261107/acceptance/R3/R6/ui/TODAY07-added-m.png) · [desktop](../../../data/perf-20261107/acceptance/R3/R5/today-d.png)

Что смотреть: баннер «still being added», вердикты, нижняя панель не закрывает текст.

Апрув ☐

## Paper
[iPhone, рисунок](../../../data/perf-20261107/acceptance/R3/R6/ui/PAPER14-fig-9TG4SAGY-m.png) · [desktop](../../../data/perf-20261107/acceptance/R3/R4b/shots/p07a-p-d.png) · [Ask, iPhone](../../../data/perf-20261107/acceptance/R3/R4a/shots/ZOT05-ask-m.png)

Что смотреть: простые слова в вердикте/чеклисте, красные флаги без повторов, ошибки Ask понятны.

Апрув ☐

## Library
[desktop](../../../data/perf-20261107/acceptance/R3/R6/ui/LIB-top-d.png) · [фильтры](../../../data/perf-20261107/acceptance/R3/R6/ui/LIB02-filters0-d.png)

Что смотреть: подписи «Not interested» и бины приоритета читаются; абстракты без склейки. Мобильного снимка нет.

Апрув ☐

## Search
[iPhone](../../../data/perf-20261107/acceptance/R3/R6/ui/SRCH-a-m.png) · [What was searched](../../../data/perf-20261107/acceptance/R3/R5/srch-sources-m.png) · [Find new relevant](../../../data/perf-20261107/acceptance/R3/R5/srch-findnew-m.png)

Что смотреть: «What was searched» простыми словами, «Reviewed N of 10», нет горизонтального скролла. R6b: [фильтры простыми словами (iPhone)](../../../data/perf-20261107/acceptance/R3/R6b/ui/srch06-m.png)

Апрув ☐

## Weekly / Channels
[Weekly, iPhone](../../../data/perf-20261107/acceptance/R3/R6/ui/WEEK03-a-m.png) · [Channels, iPhone](../../../data/perf-20261107/acceptance/R3/R6/ui/RSS04-refresh1-m.png) · [Weekly, desktop](../../../data/perf-20261107/acceptance/R3/R5/weekly-d.png)

Что смотреть: «Dataset provenance», «1 card», «Last checked»; счётчики RSS «unchanged».

Апрув ☐

## Triage
[desktop](../../../data/perf-20261107/acceptance/R3/R5/triage-d.png) · [идёт](../../../data/perf-20261107/acceptance/R3/R6/ui/W8d-running-d.png) · [остановка](../../../data/perf-20261107/acceptance/R3/R6/ui/W8d-stopping-d.png)

Что смотреть: прогресс понятен, «Stopping after the current AI call finishes…», ошибки без жаргона. Мобильного снимка нет.

Апрув ☐

## Ops / Pending
[Pending, iPhone](../../../data/perf-20261107/acceptance/R3/R6/ui/OPS7-after-m.png) · [Pending, desktop](../../../data/perf-20261107/acceptance/R3/R6/ui/OPS7-top-d.png) · [applied](../../../data/perf-20261107/acceptance/R3/R5/pending-applied2-d.png)

Что смотреть: «6 changes applied.», «N older versions», локальное время. R6b: [«1 older version» раскрыт (iPhone)](../../../data/perf-20261107/acceptance/R3/R6b/ui/ops06-fold-open-m.png)

Апрув ☐

## Settings
[desktop](../../../data/perf-20261107/acceptance/R3/R5/sett-edit-d.png) · [connections](../../../data/perf-20261107/acceptance/R3/R5/sett-conn-d.png) · [attention](../../../data/perf-20261107/acceptance/R3/R5/sett02-attention-d.png)

Что смотреть: «Approve this connection», статус провайдера не устарел, текст «Settings are saving…». Мобильного снимка нет.

Апрув ☐

## Offline / busy header
[busy, iPhone](../../../data/perf-20261107/acceptance/R3/R6/ui/W8b-a1-busy-m.png) · [unreachable, iPhone](../../../data/perf-20261107/acceptance/R3/R6/ui/W8b-b1-unreachable-m.png) · [conflict, desktop](../../../data/perf-20261107/acceptance/R3/R5/off-conflict-d.png)

Что смотреть: «Server is busy — retrying…» против «Server unreachable»; «You are offline — AI review needs the Mac».

Апрув ☐

## CLI

[CLI-06 (view/run)](../../../data/perf-20261107/acceptance/R3/R6/cli/CLI-06.txt) · [CLI-11](../../../data/perf-20261107/acceptance/R3/R6/cli/CLI-11.txt) · [CLI-12](../../../data/perf-20261107/acceptance/R3/R6/cli/CLI-12.txt)

Что смотреть: одна строка ошибки вместо traceback, прогресс «screened 40/432 …», «Local model not tested».

Апрув ☐
