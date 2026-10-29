# HTTP API medogen

API даёт автотестам и другим системам всё, что тестировщик делает в интерфейсе: собрать пакет
МЭДО по сценарию, проверить его, положить в IN или OUT стенда, забрать пакет из OUT, ответить на
входящий квитанцией или уведомлением, запустить маршрутизацию между стендами.

Машиночитаемое описание (OpenAPI 3.0) — `GET /api/v1/openapi.json` на запущенном сервере или файл
`medogen_core/openapi.json`. Его можно открыть в Swagger Editor, Postman или Insomnia
(импорт OpenAPI) и сгенерировать по нему клиента.

## 1. Запуск

| Команда | Что открывается |
|---|---|
| `python medogen.py ui` | интерфейс и API на одном порту (http://localhost:8765/api/v1), без ключа |
| `python medogen.py api` | только API, без страницы интерфейса; ключ — по желанию |

```
python medogen.py api --host 0.0.0.0 --port 8766 --token СЕКРЕТ
```

- `--host` — `127.0.0.1` (по умолчанию, только этот компьютер) или `0.0.0.0` (доступ по сети).
- `--port` — порт, по умолчанию 8765. API и интерфейс можно запустить одновременно на разных
  портах: у них общие папки `out/ui` и `orgs`.
- `--token` или переменная окружения `MEDOGEN_API_TOKEN` — ключ API. Если он задан, каждый запрос
  (кроме `/openapi.json`) передаёт его в заголовке `Authorization: Bearer СЕКРЕТ` или
  `X-Api-Key: СЕКРЕТ`, иначе ответ 401. Без ключа сервер с `--host 0.0.0.0` напоминает о нём в консоли.
- `--route-interval` — фоновый опрос маршрутов между стендами, как у `ui` (0 — выключить).

Через `ui` API работает без ключа — так же, как сам интерфейс. Для доступа по сети с паролем
используйте `api --token` или обратный прокси (docs/DEPLOY.md, раздел 4).

## 2. Общие правила

- Все адреса начинаются с `/api/v1`. Адреса без версии (`/api/build` и т.п.) — внутренние для
  страницы интерфейса и могут меняться; в автотестах используйте только `/api/v1`.
- Запросы и ответы — JSON в UTF-8. Исключения: загрузка и скачивание пакетов (`application/zip`)
  и скачивание отдельных файлов.
- **Идентификатор пакета** (`id`) — поле `id` карточки, путь относительно `out/ui`, например
  `20261029-120000-123_01_3.0_document_ДЭВ` или `_incoming/20261029-120500_пакет`. В адресе
  кодируйте его целиком (в Python `urllib.parse.quote(id, safe="")`, в JavaScript
  `encodeURIComponent(id)`). Незакодированный `_incoming/…` тоже принимается.
- **Стенд** — имя файла в `orgs/` с `.json` или без: `default`, `stand_test.json`. Если в запросе
  стенд не указан, берётся `default.json`.
- **Ошибки** — JSON `{"error": "текст"}` и код:

| Код | Когда |
|---|---|
| 400 | неверный запрос: не JSON, нет обязательного поля, не zip, путь вне рабочей папки, у стенда нет папок обмена |
| 401 | сервер запущен с ключом, а ключ не передан или неверен |
| 404 | нет такого адреса, пакета, стенда или файла |
| 405 | метод не поддерживается этим адресом (в заголовке `Allow` — какие можно) |
| 409 | пакет не положен в IN/OUT: там уже есть пакет с таким именем, в пакете ошибки проверки, пакет входящий |
| 422 | сценарий не собрался; кроме `error` в ответе есть `log` и `warnings` |
| 500 | сбой генератора (подробности — в консоли сервера) |

Результат проверки пакета **не** является ошибкой HTTP: собранный с ошибками пакет возвращается
с кодом 201, ошибки — в поле `errors` карточки.

## 3. Адреса

### Справочники

| Запрос | Ответ |
|---|---|
| `GET /health` | `{"service": "medogen", "api": "v1", "xsd": true, "formats": [...], "auth": false}`; `xsd: false` — не установлен lxml, проверки по XSD нет |
| `GET /meta` | справочники для сценариев: типы пакетов, виды документов, места, грифы, причины квитанций и отказов, виды уведомлений, события поручений, стенды, примеры |
| `GET /examples` | `{"items": [{"file", "description"}]}` — сценарии из папки `examples` |
| `GET /gas?q=Тюмень&limit=20` | поиск в справочнике ГАС: `{"items", "found", "total", "updated"}` |
| `GET /openapi.json` | описание OpenAPI, без ключа |

### Стенды и папки обмена с СЭД

| Запрос | Ответ |
|---|---|
| `GET /stands` | `{"items": [{"file", "stand", "count"}]}` |
| `GET /stands/{stand}` | организации стенда (`orgs[]`: `key`, `guid`, `name`, `address`), своя организация `self`, папки `exchange` (`out`, `archive`, `in`, `route_to`, `route_auto`) |
| `POST /stands/{stand}/check` | доступ к папкам: `{"out": {"ok": true, "path": ...}, "archive": {...}, "in": {...}}` |
| `GET /stands/{stand}/out` | пакеты в OUT стенда: `{"out", "items": [{"name", ...}], "error"}` |
| `POST /stands/{stand}/out/take` | тело `{"name": "<папка в OUT>"}` → 201, `{"package": <карточка>, "archived", "archive_error"}`; копия появляется во входящих, оригинал уходит в OUTArchiveGen |

Папки обмена и организации настраиваются во вкладке «Организации» интерфейса или в файле стенда
(docs/USER_GUIDE.md, раздел 5).

### Пакеты

| Запрос | Ответ |
|---|---|
| `GET /packages` | `{"items": [<карточка>]}`, новые сверху; фильтры `type`, `format`, `incoming=true/false`, `limit` (по умолчанию 100, не больше 300) |
| `POST /packages` | собрать: 201, `{"packages": [<карточка>], "warnings", "log", "scenario", "put"?}` |
| `GET /packages/{id}` | карточка с результатом проверки (`errors`, `warnings`) |
| `DELETE /packages/{id}` | удалить пакет: `{"ok": true}` |
| `GET /packages/{id}/archive` | zip с файлами пакета |
| `GET /packages/{id}/files/{name}` | файл пакета; `?entry=...` — файл внутри zip (`container.edc.zip` и т.п.) |
| `POST /packages/{id}/validate` | `{"ok", "errors", "warnings", "xsd"}` |
| `POST /packages/{id}/put` | тело `{"stand": "...", "to": "in"\|"out"}` → `{"id", "ok": true, "path"}`; ошибка — 409 |

Тело `POST /packages`:

| Поле | Значение |
|---|---|
| `stand` | файл стенда, из которого берутся организации (по умолчанию `default.json`) |
| `scenario` | сценарий в формате README, раздел «Сценарий»: один пакет или `{"settings", "defaults", "packages": [...]}` |
| `example` | вместо `scenario` — файл примера из `/examples` |
| `put` | `"in"` или `"out"` — сразу положить пакеты без ошибок в папку стенда; итог по каждому — в `put[]` ответа |

Карточка пакета: `id`, `folder` (папка на компьютере генератора), `incoming`, `created`, `format`,
`type`, `name`, `msg_uid`, `doc_uid`, `reg_number`, `reg_date`, `from`, `to`, `files[]` (`name`,
`size`, `entries[]` для zip), `errors`, `warnings`; в списке ещё `parent_id` и `children` (связь
ответа с основанием), отметки `sent_in`/`sent_out`, у забранных из OUT — `taken_from`.

### Загрузка

| Запрос | Ответ |
|---|---|
| `POST /incoming?name=...` | тело — zip пакета (`Content-Type: application/zip`) → 201, карточка входящего; её `id` используется в `reply_to` |
| `POST /incoming` с JSON `{"path": "D:\\...\\пакет"}` | то же из папки на компьютере генератора |
| `POST /validate` | тело — zip пакета → `{"ok", "errors", "warnings", "xsd"}`; ничего не сохраняется |

### Маршрутизация между стендами

| Запрос | Ответ |
|---|---|
| `GET /routes` | `{"items": [{"from", "to", "auto", "state", "error"}]}` |
| `GET /routes/journal?limit=50` | журнал пересылок, новые сверху |
| `POST /routes/{stand}/run` | «Переслать сейчас» для стенда-отправителя; тело `{"names": [...]}` — только эти пакеты из OUT; ответ `{"results": [...]}` |

Маршрут (куда пересылать и автоматически ли) настраивается в интерфейсе, см. docs/USER_GUIDE.md, раздел 6.

## 4. Примеры

Ниже `B=http://localhost:8765/api/v1`; с ключом добавьте `-H "Authorization: Bearer СЕКРЕТ"`.
В Windows (cmd) вместо `'...'` в `-d` используйте файл: `-d @body.json`.

Собрать документ 3.0 и сразу положить в IN стенда:

```
curl -X POST $B/packages -H "Content-Type: application/json" \
  -d '{"stand": "stand_test", "put": "in",
       "scenario": {"format": "3.0", "type": "document", "from": "my_org", "to": ["partner"],
                    "document": {"annotation": "О проведении мероприятий", "reg_number": "П-1"}}}'
```

Скачать пакет и отдельный файл:

```
curl -o pkg.zip "$B/packages/<id>/archive"
curl "$B/packages/<id>/files/container.edc.zip?entry=passport.xml"
```

Ответить квитанцией на пакет, который прислала СЭД:

```
curl -X POST "$B/incoming?name=from_sed" -H "Content-Type: application/zip" --data-binary @from_sed.zip
# в ответе id входящего, например "_incoming/20261029-120000_from_sed"
curl -X POST $B/packages -H "Content-Type: application/json" \
  -d '{"stand": "stand_test", "put": "in",
       "scenario": {"type": "receipt", "reply_to": "ui:_incoming/20261029-120000_from_sed"}}'
```

Забрать пакет из OUT стенда и ответить уведомлением о регистрации:

```
curl $B/stands/stand_test/out
curl -X POST $B/stands/stand_test/out/take -H "Content-Type: application/json" -d '{"name": "<папка>"}'
curl -X POST $B/packages -H "Content-Type: application/json" \
  -d '{"stand": "stand_test", "put": "in",
       "scenario": {"type": "notification", "reply_to": "ui:<id входящего>",
                    "notification": {"kind": "registered"}}}'
```

Формат ответа задаётся как обычно полем `format` сценария (по умолчанию 3.0); для входящего 2.7.1
укажите `"format": "2.7.1"`.

Проверить zip, который собрала СЭД, ничего не сохраняя:

```
curl -X POST $B/validate -H "Content-Type: application/zip" --data-binary @package.zip
```

Python (только стандартная библиотека):

```python
import json, urllib.parse, urllib.request

B = "http://localhost:8765/api/v1"
HEAD = {"Content-Type": "application/json"}  # с ключом: HEAD["Authorization"] = "Bearer СЕКРЕТ"

def call(method, path, body=None):
    req = urllib.request.Request(B + path, method=method, headers=HEAD,
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req) as r:
        return json.load(r)

res = call("POST", "/packages", {"stand": "default", "scenario": {
    "format": "2.7.1", "type": "document", "from": "pravit_mo", "to": ["mo_duma"]}})
pkg = res["packages"][0]
assert not pkg["errors"], pkg["errors"]
put = call("POST", f"/packages/{urllib.parse.quote(pkg['id'], safe='')}/put", {"stand": "default", "to": "in"})
print(put["path"])
```

`urllib` бросает `HTTPError` на кодах 4xx/5xx; текст ошибки — в `json.load(e)["error"]`.

## 5. Совместимость

- В `/api/v1` поля ответов только добавляются; удаление или переименование — уже `/api/v2`.
- Таблица адресов в коде (`medogen_core/api.py`, `ROUTES`) и `openapi.json` сверяются тестом
  `tests/test_api.py`: новый адрес без описания тест не пропустит.
