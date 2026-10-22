# Обмен с СЭД через папки OUT и IN: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** забирать пакеты СЭД из OUT (с переносом в OUTArchive), готовить ответы в интерфейсе и класть их в IN.

**Architecture:** новый модуль `medogen_core/exchange.py` с чистыми функциями над папками; `webui.App` вызывает его, берёт пути из стенда и пишет отметки в манифест; `ui/index.html` показывает блок OUT, кнопку «Положить в IN» и галочку в «Ответах пачкой».

**Tech Stack:** Python 3.8+ (только стандартная библиотека), unittest, ванильный JS; Playwright для ручной проверки интерфейса.

**Spec:** `docs/superpowers/specs/2026-10-22-sed-exchange-folders-design.md`

## Global Constraints

- Только стандартная библиотека Python, без новых зависимостей.
- Пути к папкам берутся только из стенда (`"exchange": {"out", "archive", "in"}`); API принимает имена пакетов, не пути.
- `archive` по умолчанию — `OUTArchive` рядом с OUT.
- Пакет в OUT — подпапка с `envelope.ini`; имена с `.` и `_` в начале пропускаются.
- Запись в IN через временную папку `IN/.<имя>.medogen-tmp` и переименование.
- В IN кладутся только собранные пакеты (не `_incoming`) без ошибок `validate_folder`.
- Отметка в манифесте: `"sent_in": [{"at": "%d.%m.%Y %H:%M:%S", "path": ...}]`.
- Галочка «сразу в IN» по умолчанию выключена.
- Тексты ошибок как в спецификации: «пакет уже забран или не найден», «в IN уже есть пакет …», «в пакете ошибки проверки, в IN не положен», «нет доступа к <путь>: <ошибка ОС>».

## Review Focus

1. Сохранение организации стенда (`save_org`, `delete_org`) не должно стирать блок `exchange`: `_write_json` сейчас пишет только stand/self/orgs. Тест в Task 2.
2. Имя пакета вида `..` или с разделителем пути в `take` — отказ, а не выход за OUT. Тест в Task 1.
3. Перенос в архив не удался (папка занята, нет прав) — копия в `_incoming` остаётся, ошибка понятная. Тест в Task 1 (архив — файл вместо папки).
4. Недоступный путь OUT при открытии вкладки — блок с ошибкой, а не падение списка. Тест в Task 2 (`exchange_out` возвращает `error`).
5. Повторное «Положить в IN» того же пакета, пока СЭД его не забрала — отказ; после того как забрала — разрешено. Тест в Task 1.

---

### Task 1: модуль `exchange.py`

**Files:**
- Create: `medogen_core/exchange.py`
- Test: `tests/test_exchange.py`

**Interfaces:**
- Consumes: `reply.read_package(folder) -> dict` (format, msg_uid, source, receivers, doc_number, doc_date), `common.MedoError`.
- Produces:
  - `paths(stand: dict) -> dict | None` — `{"out", "archive", "in"}` (пустые строки допустимы для `in`), `None`, если `out` и `in` пусты.
  - `list_out(out_dir: str) -> list[dict]` — `{"name", "mtime", "format", "msg_uid", "from", "to", "reg_number", "reg_date"}` или `{"name", "mtime", "error"}`; сортировка по mtime убыв.; недоступная папка → `MedoError("нет доступа к …")`.
  - `take(out_dir, archive_dir, name, dest_dir) -> tuple[str, str | None, str | None]` — (папка копии, путь в архиве, ошибка переноса).
  - `put_in(package_dir: str, in_dir: str) -> str` — путь в IN.

- [ ] **Step 1: Тесты.** В `setUpClass` собрать генератором (как `AssignmentFromGas`, через `builder.Run`) пакет 3.0 и пакет 2.7.1 во временную папку; для каждого теста свежие `OUT`, `OUTArchive`, `IN`, `dest` во временной папке.
  - `test_list_out_finds_packages_only`: в OUT две папки пакетов, файл `x.txt`, папка без `envelope.ini`, `.tmp1` и `_svc` с `envelope.ini` → имена ровно два пакета; у пакета 3.0 `from["guid"]` = GUID `pravit_mo` из `orgs/default.json`, `msg_uid` совпадает с манифестом.
  - `test_list_out_unreadable_package_has_error`: папка только с `envelope.ini` (пустой) → запись с ключом `error`.
  - `test_take_copies_and_archives`: после `take` папки нет в OUT, есть `OUTArchive/<name>`, копия в `dest` содержит те же файлы.
  - `test_take_name_collision_in_archive`: в архиве уже есть `<name>` → путь архива оканчивается на `<name>_1`.
  - `test_take_rejects_missing_and_traversal`: `take(..., "nope", ...)` и `take(..., "..", ...)`, `take(..., "a/../..", ...)` → `MedoError`.
  - `test_take_archive_failure_keeps_copy`: `archive_dir` — существующий файл → копия есть, третий элемент результата непустой, оригинал остался в OUT.
  - `test_put_in_writes_whole_folder`: файлы в `IN/<имя папки пакета>` совпадают; в IN нет имён, начинающихся с `.`.
  - `test_put_in_refuses_existing_then_allows_after_pickup`: второй `put_in` → `MedoError` с «в IN уже есть пакет»; после удаления `IN/<имя>` третий проходит.
  - `test_paths_default_archive`: `paths({"exchange": {"out": "/x/OUT", "in": "/x/IN"}})["archive"] == os.path.join("/x", "OUTArchive")`; `paths({})` → `None`.
- [ ] **Step 2:** `python -m unittest tests.test_exchange -v` — падает с ImportError.
- [ ] **Step 3:** Реализовать функции. `take`: имя проверять `name == os.path.basename(name) and name not in ("", ".", "..")`; копия `shutil.copytree`; перенос `shutil.move`; суффиксы `_1`, `_2`… `put_in`: `copytree` во временную, `os.rename`; при исключении `shutil.rmtree(tmp, ignore_errors=True)` и `MedoError`. OSError при чтении OUT → `MedoError(f"нет доступа к {out_dir}: {e}")`.
- [ ] **Step 4:** Тесты зелёные; весь набор `python -m unittest discover -s tests -v` зелёный.
- [ ] **Step 5:** Коммит `feat: модуль обмена с папками OUT/IN`.

### Task 2: методы `App` и маршруты API

**Files:**
- Modify: `medogen_core/webui.py` (`App.orgs`, `App._write_json`, новые методы, `_get`/`_post` routes)
- Test: `tests/test_exchange.py` (класс `AppExchange`)

**Interfaces:**
- Consumes: Task 1.
- Produces (JSON для Task 3):
  - `App.exchange_save(body{stand, exchange}) -> orgs(...)`; `App.orgs()` дополнительно возвращает `"exchange": {...}` (пустой dict, если нет).
  - `App.exchange_check(body{stand}) -> {"out": {"ok": bool, "error"?}, "archive": {...}, "in": {...}}` (для пустого пути: `{"ok": false, "error": "путь не задан"}`).
  - `App.exchange_out(stand) -> {"items": [...], "error": str | None, "out": путь}` — `GET /api/exchange/out?stand=`.
  - `App.exchange_take(body{stand, name}) -> {"package": card, "archived": путь|None, "archive_error": str|None}`; копия в `_incoming/<_stamp()>_<name>`.
  - `App.exchange_put(body{stand, ids}) -> {"results": [{"id", "ok": bool, "path"?, "error"?}]}`; пишет `sent_in` в `_manifests/<папка>.json`.
  - `package_card` отдаёт `sent_in` из манифеста (уже идёт через `card.update(manifest)`).
  - Маршруты POST: `/api/exchange/save`, `/api/exchange/check`, `/api/exchange/take`, `/api/exchange/put`.
  - Все методы, кроме `exchange_save`, под `LOCK`.

- [ ] **Step 1: Тесты** с `App(tmp_root)`, где `tmp_root/orgs/default.json` — копия из репозитория плюс `exchange` на временные OUT/IN; пакеты собирать через `app.build({"stand": "default.json", "scenario": {...}})`.
  - `test_save_org_keeps_exchange`: `exchange_save`, затем `save_org` новой организации → в файле стенда `exchange` на месте, `orgs()["exchange"]["out"]` прежний.
  - `test_take_puts_card_in_incoming`: положить собранный пакет в OUT вручную → `exchange_take` → `package["id"]` начинается с `_incoming/`, `archived` существует.
  - `test_put_refuses_incoming_and_invalid`: id `_incoming/...` → `ok: false`; пакет, в котором испорчен `envelope.ini` (удалить строку `[ФАЙЛЫ]`) → `ok: false`, ошибка содержит «ошибки проверки».
  - `test_put_records_sent_in`: верный пакет → `ok: true`, `package_card(...)["sent_in"][0]["path"]` равен пути в IN.
  - `test_check_and_out_report_unreachable`: стенд с `out` = несуществующий путь → `exchange_check()["out"]["ok"] is False`, `exchange_out()["error"]` содержит «нет доступа».
- [ ] **Step 2:** Тесты падают (AttributeError).
- [ ] **Step 3:** Реализовать; `_write_json` дописывает `"exchange"`, если он есть в данных.
- [ ] **Step 4:** Весь набор тестов зелёный; `python medogen.py build examples` — «ошибок: 0».
- [ ] **Step 5:** Коммит `feat: API обмена с СЭД в интерфейсе`.

### Task 3: интерфейс

**Files:**
- Modify: `medogen_core/ui/index.html`

**Interfaces:**
- Consumes: JSON из Task 2; `ORGS.exchange` после `loadOrgs`.

- [ ] **Step 1:** Вкладка «Организации»: блок «Обмен с СЭД» с полями OUT, OUTArchive (подсказка «пусто — OUTArchive рядом с OUT»), IN; кнопки «Сохранить» и «Проверить доступ» (результат по каждой папке: «доступна» или ошибка).
- [ ] **Step 2:** Вкладка «Пакеты»: если `ORGS.exchange.out` задан, над списком карточка «Папка OUT (стенд …)»: таблица (время, формат, от → кому, документ, замечание), «Обновить», «Забрать» в строке. «Забрать» → `/api/exchange/take` → `loadPackages`, открыть пакет справа, `openReplies(card)`, если у пакета есть `from` и тип не квитанция/уведомление; при `archive_error` — предупреждение.
- [ ] **Step 3:** `packageView`: если `ORGS.exchange.in` и пакет не `_incoming/` — кнопка «Положить в IN»; строка «В IN: <at>» по последнему `sent_in`.
- [ ] **Step 4:** «Ответы пачкой»: галочка `RP.to_in` «Сразу положить в IN пакеты без ошибок» (видна при заданном IN); после сборки при включённой галочке — `/api/exchange/put` для пакетов без ошибок; в таблице результата колонка «IN» («✓» или ошибка); кнопка «Положить все в IN», если ещё не положены.
- [ ] **Step 5: Проверка Playwright** на копии в `uitest4` (порт 8767) с OUT/IN во временной папке: задать пути, «Проверить доступ», положить пакет в OUT, «Забрать» → пакет исчез из OUT и есть в OUTArchive, «Ответы пачкой» с галочкой → в IN появились папки ответов, в OUT-блоке пусто. Скриншот.
- [ ] **Step 6:** Коммит `feat: интерфейс обмена с СЭД через OUT и IN`.

### Task 4: документация, публикация, перенос на ПК

**Files:**
- Modify: `docs/USER_GUIDE.md` (новый раздел «Обмен с СЭД через папки OUT и IN», строка в таблице «Где лежат файлы»), `docs/DEPLOY.md` (учётная запись службы, cifs на Linux), `README.md` (одна строка со ссылкой).

- [ ] **Step 1:** Написать разделы.
- [ ] **Step 2:** Полный прогон тестов и `build examples`.
- [ ] **Step 3:** Коммит `docs: обмен с СЭД через OUT и IN`, `git push origin main`.
- [ ] **Step 4:** Перенос изменённых файлов на ПК (патч через device_bash), md5 совпадают, на ПК `python -m unittest discover -s tests` — OK.
