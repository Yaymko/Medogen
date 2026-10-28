# Маршрутизация пакетов между стендами: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** medogen сам переносит пакеты МЭДО из OUT одного стенда в IN другого (в обе стороны), проверив пакет по формату.

**Architecture:** Новый модуль `medogen_core/router.py` (чистая логика: один проход маршрута поверх `exchange.take`, `validate.validate_folder`, `exchange.put_in`). `webui.App` хранит состояние маршрутов, пишет журнал и даёт API; `serve()` запускает фоновый поток опроса. Настройка маршрута — поля `route_to`, `route_auto` в блоке `exchange` стенда-отправителя.

**Tech Stack:** Python 3 stdlib (unittest, threading, http.server), чистый JS в `ui/index.html`, Playwright для проверки интерфейса.

**Spec:** `docs/superpowers/specs/2026-10-28-stand-routing-design.md`

## Global Constraints

- Только стандартная библиотека Python, как во всём проекте.
- Тесты: `python -m unittest discover -s tests -v` (новый файл `tests/test_routing.py`).
- Интервал опроса по умолчанию 10 секунд; `settle` по умолчанию 5 секунд.
- Журнал: `out/ui/_routing/journal.jsonl`, интерфейс показывает последние 200 записей.
- Статусы результата: `sent`, `invalid`, `put_failed`, `take_failed`, `resent`.
- Все операции с папками обмена под `EXCHANGE_LOCK`.
- Тексты интерфейса и ошибок — по-русски, как в спецификации («Переслать сейчас», «Переслать без проверки», «Пересылать в стенд», «Автоматически», «Маршрутизация»).
- Коммиты в ветку `claude/project-thread-ugq0o5`.

## Review Focus

1. Пакет из OUT, у которого envelope.ini уже есть, а остальные файлы ещё дописываются — должен пропускаться по `settle` (тест в Task 2 трогает mtime вложенного файла, а не только папки).
2. Два прохода одновременно (фон и «Переслать сейчас») — пакет не должен уйти в IN дважды; проход целиком под `EXCHANGE_LOCK` (тест в Task 3: два потока `App.route` по одному пакету → в IN одна папка).
3. `route_to` указывает на удалённый стенд или на самого себя — понятная ошибка конфигурации, а не исключение в потоке (Task 3).
4. Повторный `resend` пакета, который уже лежит в IN — `MedoError` «в IN уже есть пакет», а не дубль (Task 2).
5. Битый файл стенда в `orgs/` не должен останавливать `route_all_auto` для остальных стендов (Task 3).

---

### Task 1: Поля маршрута в настройках стенда

**Files:**
- Modify: `medogen_core/exchange.py` (`paths`)
- Modify: `medogen_core/webui.py` (`exchange_save`)
- Test: `tests/test_routing.py` (новый, класс `RouteSettings`)

**Interfaces:**
- Produces: `exchange.paths(stand) -> {"out", "archive", "in", "route_to": str, "route_auto": bool} | None`; `exchange_save` сохраняет `route_to` (строка, обрезанная) и `route_auto` (bool).

- [ ] **Step 1: Тесты.** Заготовка файла как в `tests/test_exchange.py` (ROOT, `build_packages`, `DEFAULT_ORGS`). В `RouteSettings` (корень App во временной папке, как `AppExchange.setUp`):
  - `test_paths_has_route_fields`: `exchange.paths({"exchange": {"out": "O", "route_to": "s2.json", "route_auto": True}})` содержит `route_to == "s2.json"`, `route_auto is True`; без полей — `""` и `False`.
  - `test_save_keeps_route`: `exchange_save({"stand": "default.json", "exchange": {"out": o, "in": i, "route_to": " s2.json ", "route_auto": True}})`, затем `app.orgs("default.json")["exchange"]` → `route_to == "s2.json"`, `route_auto is True`.
  - `test_import_orgs_keeps_route`: после `importer.import_file(...)` (как в `test_import_orgs_keeps_exchange`) `route_to` на месте.
- [ ] **Step 2:** `python -m unittest tests.test_routing -v` — FAIL (нет ключей).
- [ ] **Step 3:** Дополнить `paths` и `exchange_save`.
- [ ] **Step 4:** Тесты проходят, весь набор тоже.
- [ ] **Step 5:** Commit `feat(routing): поля маршрута в настройках стенда`.

### Task 2: Модуль `router.py` — один проход и повторная отправка

**Files:**
- Create: `medogen_core/router.py`
- Test: `tests/test_routing.py` (класс `RouterModule`)

**Interfaces:**
- Consumes: `exchange.list_out`, `exchange.take(out, archive, name, dest) -> (copy, archived, err)`, `exchange.put_in(folder, in_dir) -> path`, `validate.validate_folder(folder, warnings) -> errors`.
- Produces:
  - `route_once(src: dict, dst_in: str, incoming_dir: str, settle: float = 5) -> list[dict]` — `src` с ключами `out`, `archive`; каждая запись: `at` (строка `%d.%m.%Y %H:%M:%S`), `name`, `format`, `msg_uid`, `copy`, `archived`, `status`, `in_path`, `errors`, `warnings`.
  - `resend(copy: str, dst_in: str) -> str` — путь в IN.
  - `last_change(folder: str) -> float` — максимум mtime папки и всех файлов в ней (рекурсивно).

- [ ] **Step 1: Тесты** (временные OUT1, OUTArchiveGen1, IN2, incoming; пакеты 3.0 и 2.7.1 из `build_packages`; в OUT кладутся копией, затем `os.utime` всех файлов на `time.time() - 60`):
  - `test_valid_package_goes_to_in`: один пакет, `settle=5` → статус `sent`, `os.listdir(IN2) == [name]`, OUT пуст, `archived` существует, `copy` существует.
  - `test_invalid_package_stays`: у копии в OUT удалить один файл из `[ФАЙЛЫ]` (не envelope.ini) → `invalid`, `errors` непусто, IN2 пуст, копия во входящих есть.
  - `test_fresh_package_skipped`: после копирования `os.utime` только envelope.ini на старое время, вложенный файл свежий → результат `[]`, пакет в OUT.
  - `test_name_taken_in_in`: два пакета, в IN2 заранее папка с именем первого → первый `put_failed` (`errors` с «уже есть пакет»), второй `sent`.
  - `test_order_oldest_first`: два пакета с mtime −120 и −60 → порядок результатов по возрастанию времени.
  - `test_resend_invalid`: после `invalid` `resend(copy, IN2)` возвращает путь в IN2; повторный `resend` → `MedoError` «уже есть пакет».
  - `test_out_unreachable`: `out` не существует → `MedoError` «нет доступа».
- [ ] **Step 2:** Тесты падают (`ImportError`).
- [ ] **Step 3:** Реализовать. Копия: `os.path.join(incoming_dir, safe_dir_name(f"{stamp}_{name}"))` с `safe_dir_name` и меткой времени из `webui` — перенести их в `common.py`, если они там не лежат, и импортировать в обоих модулях. `MedoError` из `take` → `take_failed` с текстом в `errors`; ошибка переноса в архив (третье значение `take`) — в `warnings`. Формат и msgUid — из записи `list_out`.
- [ ] **Step 4:** Тесты проходят, весь набор тоже.
- [ ] **Step 5:** Commit `feat(routing): router.route_once и resend`.

### Task 3: Маршруты в `App`: проход, журнал, API, оба направления

**Files:**
- Modify: `medogen_core/webui.py`
- Test: `tests/test_routing.py` (класс `AppRouting`)

**Interfaces:**
- Consumes: `router.route_once`, `router.resend`, `exchange.paths` (Task 1–2).
- Produces:
  - `App.routes() -> {"routes": [{"from", "from_title", "to", "to_title", "auto", "state", "error"}]}` — по стенду с непустым `route_to`; `error` — ошибка конфигурации («стенд-получатель не найден», «маршрут на самого себя», «у стенда-получателя не задана папка IN», «у стенда не задана папка OUT») или `None`; `state` — `{"last_run", "last_error", "sent", "invalid", "failed"}`.
  - `App.route(stand: str) -> {"results": list}` — проход под `EXCHANGE_LOCK`; при ошибке конфигурации `MedoError`; ошибку доступа пишет в `state.last_error` и поднимает `MedoError`.
  - `App.route_all_auto() -> None` — все стенды с `route_auto` и без ошибки конфигурации; исключения глотает в `last_error` маршрута.
  - `App.route_journal(limit=200) -> {"items": list}` — новые сверху; к записи добавлены `from`, `to`, `id` (pkg_id копии).
  - `App.route_resend({"id"}) -> {"path"}` — только пакеты `_incoming/`, у которых в `_taken` есть `route`; пишет журнал со статусом `resent`.
  - Запись `_taken/<папка>.json` как у `exchange_take` плюс `route: {"to", "status", "in_path"}`.
  - HTTP: `GET /api/routes`, `GET /api/routes/journal`, `POST /api/routes/run {stand}`, `POST /api/routes/resend {id}`.

- [ ] **Step 1: Тесты** (корень App; два стенда `s1.json`, `s2.json` — копии `default.json`; у каждого свои OUT/IN во временной папке; `route_to` друг на друга, `route_auto: true`; settle в тестах — 0 через атрибут `App.route_settle`):
  - `test_route_sends_and_logs`: пакет в OUT s1 → `app.route("s1.json")` → в IN s2; `route_journal()["items"][0]["status"] == "sent"`, `from == "s1.json"`; карточка в истории имеет «Забран со стенда».
  - `test_return_path`: сценарий через `builder.Run`: `doc` (3.0, pravit_mo → mo_duma), `receipt_ok` (`reply_to: "@doc"`), `registered` (notification), `answer` (type document, `reply_to: "@doc"`, from mo_duma to pravit_mo). `doc` в OUT s1, три ответа в OUT s2; один `route_all_auto()` → `doc` в IN s2, три ответа в IN s1.
  - `test_config_errors`: `route_to` на себя, на несуществующий стенд, на стенд без `in` → `routes()` с соответствующим `error`; `route()` → `MedoError`.
  - `test_unreachable_does_not_stop_others`: OUT s1 не существует → после `route_all_auto()` у s1 `last_error` содержит «нет доступа», пакет s2 дошёл до IN s1. Битый `orgs/bad.json` рядом не мешает.
  - `test_resend_from_journal`: битый пакет → `invalid` → `route_resend({"id": item["id"]})` → в IN s2, в журнале `resent`. `route_resend` для собранного пакета (не `_incoming`) → `MedoError`.
  - `test_parallel_runs_send_once`: два `threading.Thread(target=app.route, args=("s1.json",))` → в IN s2 одна папка.
- [ ] **Step 2:** Тесты падают.
- [ ] **Step 3:** Реализовать методы и маршруты в `make_handler` рядом с `/api/exchange/*`. Состояние маршрутов — словарь в `App`, `route_settle = 5`.
- [ ] **Step 4:** Тесты проходят, весь набор тоже.
- [ ] **Step 5:** Commit `feat(routing): маршруты, журнал и API в интерфейсе`.

### Task 4: Фоновый опрос

**Files:**
- Modify: `medogen_core/webui.py` (`serve`, новая `start_router(app, interval) -> threading.Event`)
- Modify: `medogen.py` (`ui --route-interval`, по умолчанию 10, 0 — без опроса)
- Test: `tests/test_routing.py` (`test_background_thread`)

**Interfaces:**
- Produces: `start_router(app: App, interval: float) -> threading.Event` — запускает поток-демон: `app.route_all_auto()`, затем `event.wait(interval)`; `event.set()` останавливает.

- [ ] **Step 1: Тест** `test_background_thread`: настройка как в Task 3, `stop = start_router(app, 0.05)`, ждать до 5 с, пока пакет не появится в IN s2; `stop.set()`. Отдельно: исключение в `route_all_auto` (подменить на функцию, бросающую `RuntimeError`) не убивает поток — второй вызов происходит.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Реализовать; `serve(root, host, port, open_browser, route_interval=10)`; при `route_interval > 0` печатать «Маршрутизация: опрос каждые N с».
- [ ] **Step 4:** Тесты проходят.
- [ ] **Step 5:** Commit `feat(routing): фоновый опрос маршрутов`.

### Task 5: Интерфейс

**Files:**
- Modify: `medogen_core/ui/index.html`

**Interfaces:**
- Consumes: API Task 3, `exchange` в ответе `/api/orgs`.

- [ ] **Step 1:** «Организации» → «Обмен с СЭД»: список «Пересылать в стенд» (пусто + все стенды, кроме текущего), галочка «Автоматически» с подсказкой «Всё из OUT этого стенда будет забирать маршрутизатор»; сохраняются тем же «Сохранить».
- [ ] **Step 2:** «Пакеты»: блок «Маршрутизация» в оглавлении слева, сворачиваемый (состояние в localStorage, как «Папка OUT»). Таблица маршрутов (стенд → стенд, авто/вручную, последний проход, ошибка, счётчики, «Переслать сейчас»), журнал (время, маршрут, пакет, формат, результат, ссылка на пакет, «Переслать без проверки» у `invalid` и `put_failed`). Автообновление раз в 10 секунд, пока блок развёрнут. Без маршрутов — подсказка «Маршруты настраиваются на вкладке «Организации», блок «Обмен с СЭД»».
- [ ] **Step 3: Проверка Playwright** (скрипт в scratchpad, не в репозитории): корень во временной папке, стенды s1/s2 с временными папками и маршрутами, `ui --no-browser --route-interval 2`; положить пакет в OUT s1 и ответ в OUT s2; убедиться, что журнал показывает два `в IN`, пакеты в IN; положить битый пакет, нажать «Переслать без проверки», проверить IN. Скриншот блока.
- [ ] **Step 4:** Commit `feat(routing): блок «Маршрутизация» в интерфейсе`.

### Task 6: Документация и итоговая проверка

**Files:**
- Modify: `docs/USER_GUIDE.md`, `docs/TESTER_GUIDE.md`, `docs/DEPLOY.md`, `README.md`

- [ ] **Step 1:** USER_GUIDE — раздел «Маршрутизация между стендами» (настройка обоих направлений, журнал, «Переслать без проверки»). TESTER_GUIDE — сквозной сценарий «стенд 1 отправил → стенд 2 ответил → стенд 1 получил». DEPLOY — доступ службы к папкам обоих стендов, `--route-interval`, опрос работает только при запущенной службе. README — строка о возможности.
- [ ] **Step 2:** `python -m unittest discover -s tests -v` — все OK; `python medogen.py build examples` — 0 ошибок.
- [ ] **Step 3:** Commit `docs: маршрутизация между стендами`, push в `claude/project-thread-ugq0o5`, PR в main.
